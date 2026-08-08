import pytest

from speaker import detect_cuts, iou, link_tracks, usable_tracks


def b(x, y=100, w=100, h=100, detected=True):
    """Rectangle de detection. `detected` distingue un rectangle reellement
    detecte de sa copie tenue entre deux passes ; par defaut on construit des
    detections reelles, le cas que la plupart des tests veulent decrire."""
    return {"x": x, "y": y, "w": w, "h": h, "detected": detected}


def test_iou_rectangles_identiques():
    assert iou(b(0), b(0)) == pytest.approx(1.0)


def test_iou_rectangles_disjoints():
    assert iou(b(0), b(500)) == pytest.approx(0.0)


def test_iou_recouvrement_partiel():
    # deux carrés de 100, décalés de 50 : intersection 50x100, union 150x100
    assert iou(b(0), b(50)) == pytest.approx(50 * 100 / (150 * 100))


def test_une_piste_suivie_sur_trois_images():
    tracks = link_tracks([[b(100)], [b(105)], [b(110)]])
    assert len(tracks) == 1
    assert tracks[0]["boxes"] == {0: b(100), 1: b(105), 2: b(110)}


def test_deux_visages_gardent_leur_identite():
    tracks = link_tracks([[b(100), b(800)], [b(110), b(810)]])
    assert len(tracks) == 2
    gauche = next(t for t in tracks if t["boxes"][0]["x"] == 100)
    droite = next(t for t in tracks if t["boxes"][0]["x"] == 800)
    assert gauche["boxes"][1]["x"] == 110
    assert droite["boxes"][1]["x"] == 810


def test_une_piste_qui_disparait_puis_revient_garde_son_id():
    """La cascade rate un visage sur une image (tête tournée) : ce n'est pas une
    nouvelle personne quand il réapparaît au même endroit."""
    tracks = link_tracks([[b(100)], [], [b(105)]])
    assert len(tracks) == 1
    assert set(tracks[0]["boxes"]) == {0, 2}


def test_deux_pistes_qui_se_croisent_n_echangent_pas_d_identite():
    """Les deux visages se rapprochent sans se recouvrir : chacun doit rester
    apparié au plus recouvrant, pas au premier venu."""
    tracks = link_tracks([[b(0), b(400)], [b(40), b(360)]])
    assert len(tracks) == 2
    gauche = next(t for t in tracks if t["boxes"][0]["x"] == 0)
    assert gauche["boxes"][1]["x"] == 40


def test_un_visage_qui_saute_trop_loin_ouvre_une_nouvelle_piste():
    """Recouvrement nul : c'est un autre visage, pas le même qui a bondi."""
    tracks = link_tracks([[b(0)], [b(900)]])
    assert len(tracks) == 2


def test_aucune_detection():
    assert link_tracks([[], [], []]) == []


def test_liste_vide():
    assert link_tracks([]) == []


def test_les_pistes_portent_un_dictionnaire_d_agitation_vide():
    """`activity` est rempli plus tard par la couche d'I/O ; la structure doit
    déjà exister pour que `usable_tracks` puisse la lire sans garde."""
    tracks = link_tracks([[b(100)]])
    assert tracks[0]["activity"] == {}


def test_ids_attribues_dans_l_ordre_d_apparition():
    tracks = link_tracks([[b(100)], [b(100), b(800)]])
    assert [t["id"] for t in tracks] == [0, 1]


def test_une_piste_trop_ancienne_ne_capture_pas_un_nouveau_visage():
    """Une personne sort du cadre ; longtemps apres, quelqu'un d'autre apparait
    au meme endroit. Sans borne d'anciennete, les deux fusionneraient sous la
    meme identite et le cadrage tiendrait l'un en croyant tenir l'autre."""
    detections = [[b(100)]] + [[] for _ in range(40)] + [[b(100)]]
    tracks = link_tracks(detections, max_gap=30)
    assert len(tracks) == 2


def test_un_trou_court_ne_casse_pas_la_piste():
    """La cascade rate un visage quelques images : c'est le cas que la
    tolerance existe pour couvrir."""
    detections = [[b(100)]] + [[] for _ in range(5)] + [[b(100)]]
    tracks = link_tracks(detections, max_gap=30)
    assert len(tracks) == 1


def test_la_borne_d_anciennete_est_reglable():
    detections = [[b(100)]] + [[] for _ in range(10)] + [[b(100)]]
    assert len(link_tracks(detections, max_gap=3)) == 2
    assert len(link_tracks(detections, max_gap=30)) == 1


# --- usable_tracks : filtrage des pistes qui peuvent être un interlocuteur ---


def piste(id_, boxes, activity=None):
    return {"id": id_, "boxes": boxes,
            "activity": activity if activity is not None else
            {i: 5.0 for i in boxes}}


def test_une_piste_normale_est_conservee():
    t = piste(0, {0: b(100, w=200, h=200), 1: b(110, w=200, h=200)})
    assert [x["id"] for x in usable_tracks([t], frame_h=1080)] == [0]


def test_un_visage_trop_petit_est_ecarte():
    """60 px sur 1080 = 5,5 %, sous le seuil : c'est une vignette."""
    t = piste(0, {0: b(100, w=60, h=60), 1: b(110, w=60, h=60)})
    assert usable_tracks([t], frame_h=1080) == []


def test_une_piste_jamais_agitee_est_ecartee():
    """Une affiche, un visage de dos, un faux positif : ça ne parle pas."""
    t = piste(0, {0: b(100, w=200, h=200), 1: b(110, w=200, h=200)},
              activity={0: 0.0, 1: 0.0})
    assert usable_tracks([t], frame_h=1080) == []


def test_un_habillage_parfaitement_immobile_est_ecarte():
    """Rectangle au pixel près identique sur toute la durée : de l'habillage.
    Il porte de l'agitation (compression, bruit) mais ne bouge pas d'un pixel."""
    fixe = b(88, y=92, w=200, h=200)
    t = piste(0, {i: dict(fixe) for i in range(40)})
    assert usable_tracks([t], frame_h=1080) == []


def test_une_personne_qui_bouge_reste_conservee():
    """Le deplacement se juge A L'ECHELLE DU VISAGE : 40 px sur un rectangle de
    200 px, c'est 20 %, quelqu'un de vivant.

    (Ce test disait auparavant que TROIS pixels suffisaient. Sous une tolerance
    absolue de 2 px, oui ; sous une tolerance proportionnelle, 3 px sur un
    visage de 200 px est de l'immobilite. La specification a change.)"""
    t = piste(0, {i: b(100 + (i % 2) * 40, w=200, h=200) for i in range(40)})
    assert [x["id"] for x in usable_tracks([t], frame_h=1080)] == [0]


def test_un_habillage_qui_frissonne_est_ecarte():
    """Cas reel de la source d'essai : une vignette d'habillage de 70 px dont le
    rectangle oscille de 10 px. Une tolerance absolue de 2 px la gardait ; 10 px
    sur 70, soit 14 %, est de l'immobilite a cette echelle."""
    t = piste(0, {i: b(60 + (i % 2) * 10, y=90, w=70, h=70) for i in range(40)})
    assert usable_tracks([t], frame_h=1000) == []


def test_un_grand_visage_qui_se_deplace_est_conserve():
    """Meme oscillation en fraction, mais sur un visage de 400 px : le seuil
    suit la taille du rectangle au lieu d'etre le meme pour tous."""
    t = piste(0, {i: b(100 + (i % 2) * 80, w=400, h=400) for i in range(40)})
    assert [x["id"] for x in usable_tracks([t], frame_h=1080)] == [0]


def test_une_piste_trop_courte_pour_juger_l_immobilite_est_conservee():
    """Sur deux passes de detection, l'immobilite ne prouve rien : un invite
    assis peut ne pas parcourir 15 % de la hauteur de son visage en une demi-
    seconde. On ne rejette pas sur un echantillon aussi court."""
    fixe = b(88, y=92, w=200, h=200)
    t = piste(0, {0: dict(fixe), 1: dict(fixe)})
    assert [x["id"] for x in usable_tracks([t], frame_h=1080)] == [0]


def test_les_pistes_conservees_gardent_leur_ordre():
    a = piste(0, {i: b(100 + i * 40, w=200, h=200) for i in range(5)})
    petit = piste(1, {i: b(400 + i * 40, w=50, h=50) for i in range(5)})
    c = piste(2, {i: b(800 + i * 40, w=200, h=200) for i in range(5)})
    assert [x["id"] for x in usable_tracks([a, petit, c], frame_h=1080)] == [0, 2]


# --- Existence : une piste vue une seule fois n'est pas quelqu'un ------------


def test_une_piste_vue_sur_une_seule_passe_est_ecartee():
    """Un faux positif Haar detecte une seule fois porte quand meme plusieurs
    images, parce que le rectangle est TENU jusqu'a la passe suivante. Compter
    les images le prenait pour quelqu'un ; compter les detections non."""
    boxes = {0: b(100, w=200, h=200)}
    boxes.update({i: b(100, w=200, h=200, detected=False) for i in range(1, 5)})
    assert usable_tracks([piste(0, boxes)], frame_h=1080) == []


def test_une_piste_vue_sur_deux_passes_est_conservee():
    """Revue a la passe suivante : quelqu'un est bien la."""
    boxes = {0: b(100, w=200, h=200)}
    boxes.update({i: b(100, w=200, h=200, detected=False) for i in range(1, 5)})
    boxes[5] = b(140, w=200, h=200)
    boxes.update({i: b(140, w=200, h=200, detected=False) for i in range(6, 10)})
    assert [t["id"] for t in usable_tracks([piste(0, boxes)], frame_h=1080)] == [0]


# --- detect_cuts : la coupe est un ecart qui sort de la distribution ---------


def test_une_coupe_franche_est_relevee():
    """Un clip calme (ecarts autour de 0,02) traverse par un ecart de 0,20 :
    c'est une coupe du montage d'origine."""
    diffs = [0.0] + [0.02] * 40
    diffs[17] = 0.20
    assert detect_cuts(diffs) == {17}


def test_un_pic_de_mouvement_dans_un_plan_agite_n_est_pas_une_coupe():
    """Cas reel de la fenetre 60-90 s : camera a l'epaule, ecart median 0,037,
    et des flous de file jusqu'a 0,17. Un seuil absolu bas les prendrait pour
    des coupes ; le critere relatif (5 x la mediane) ne le fait pas."""
    diffs = [0.0] + [0.037] * 60
    diffs[10] = 0.17
    assert detect_cuts(diffs) == set()


def test_le_plancher_protege_un_clip_tres_calme():
    """Sur de longs plans fixes la mediane s'effondre (0,005) et un simple
    multiple relevrait le moindre frisson. Le plancher absolu l'en empeche."""
    diffs = [0.0] + [0.005] * 60
    diffs[10] = 0.05
    assert detect_cuts(diffs) == set()


def test_l_ecart_de_la_premiere_image_ne_compte_pas():
    """`diffs[0]` n'a pas de sens : aucune image ne precede la premiere. Meme
    porte a une valeur absurde, il ne doit etre ni releve ni compte dans la
    mediane."""
    assert detect_cuts([9.0] + [0.02] * 40) == set()


def test_detect_cuts_sur_une_entree_vide():
    assert detect_cuts([]) == set()
    assert detect_cuts([0.0]) == set()


def test_aucune_piste():
    assert usable_tracks([], frame_h=1080) == []


def test_un_faux_positif_isole_ne_sauve_pas_une_piste_de_vignettes():
    """Dix vignettes plus une grande detection erronee : la piste reste de
    l'habillage. Le maximum se laisserait tromper, la mediane non."""
    boxes = {i: b(100 + i * 5, w=60, h=60) for i in range(10)}
    boxes[10] = b(400, w=300, h=300)
    assert usable_tracks([piste(0, boxes)], frame_h=1080) == []


def test_un_visage_qui_s_approche_reste_conserve():
    """Petit au debut, grand ensuite : grand sur la moitie de la piste, donc
    conserve. La mediane ne doit pas rejeter ce cas legitime."""
    boxes = {i: b(100 + i * 20, w=60, h=60) for i in range(5)}
    boxes.update({i: b(100 + i * 20, w=300, h=300) for i in range(5, 11)})
    assert [t["id"] for t in usable_tracks([piste(0, boxes)], frame_h=1080)] == [0]


def test_une_piste_sans_aucune_mesure_est_ecartee():
    """Cas reel : un visage vu seulement sur la premiere image echantillonnee
    n'a pas d'image precedente avec quoi etre compare, donc aucune mesure. Sans
    preuve qu'il parle, on ne lui donne pas le cadre."""
    t = {"id": 0, "boxes": {0: b(100, w=200, h=200)}, "activity": {}}
    assert usable_tracks([t], frame_h=1080) == []


# --- _mouth_activity : la bouche rapportee a l'agitation de l'image ----------


def _paire(mouvement_bouche, mouvement_global):
    """Deux images grises 100x100 : le fond bouge de `mouvement_global`, et la
    bande du bas (la bouche d'un rectangle qui couvre tout) bouge en plus."""
    import numpy as np
    avant = np.full((100, 100), 100, dtype="uint8")
    apres = np.full((100, 100), 100 + mouvement_global, dtype="uint8")
    apres[66:, :] = 100 + mouvement_global + mouvement_bouche
    return avant, apres


def test_l_agitation_est_rapportee_a_l_agitation_globale():
    """Meme mouvement de bouche, deux fois plus de mouvement d'image : le score
    doit etre deux fois plus faible. Sans cela, un panoramique fait passer tout
    le monde pour bavard."""
    from speaker import _mouth_activity
    box = {"x": 0, "y": 0, "w": 100, "h": 100}
    avant, apres = _paire(10, 0)
    calme = _mouth_activity(avant, apres, box, 1.0, global_diff=10.0)
    agite = _mouth_activity(avant, apres, box, 1.0, global_diff=20.0)
    assert calme == pytest.approx(2 * agite)


def test_une_image_parfaitement_statique_ne_divise_pas_par_zero():
    """Ecart global nul : la division doit etre bornee par le plancher, pas
    lever une erreur ni renvoyer un infini."""
    from speaker import _mouth_activity
    box = {"x": 0, "y": 0, "w": 100, "h": 100}
    avant, apres = _paire(10, 0)
    valeur = _mouth_activity(avant, apres, box, 1.0, global_diff=0.0)
    assert valeur > 0
    assert valeur < float("inf")
