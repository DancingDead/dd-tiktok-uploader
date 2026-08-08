import pytest

from speaker import iou, link_tracks, usable_tracks


def b(x, y=100, w=100, h=100):
    return {"x": x, "y": y, "w": w, "h": h}


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


def test_une_personne_qui_bouge_a_peine_reste_conservee():
    """Trois pixels de déplacement suffisent à prouver qu'il y a quelqu'un."""
    t = piste(0, {i: b(100 + (i % 2) * 3, w=200, h=200) for i in range(40)})
    assert [x["id"] for x in usable_tracks([t], frame_h=1080)] == [0]


def test_une_piste_trop_courte_pour_juger_l_immobilite_est_conservee():
    """Sur deux images, l'immobilité ne prouve rien — on ne rejette pas."""
    fixe = b(88, y=92, w=200, h=200)
    t = piste(0, {0: dict(fixe), 1: dict(fixe)})
    assert [x["id"] for x in usable_tracks([t], frame_h=1080)] == [0]


def test_les_pistes_conservees_gardent_leur_ordre():
    a = piste(0, {i: b(100, w=200, h=200) for i in range(5)})
    petit = piste(1, {i: b(400, w=50, h=50) for i in range(5)})
    c = piste(2, {i: b(800, w=200, h=200) for i in range(5)})
    assert [x["id"] for x in usable_tracks([a, petit, c], frame_h=1080)] == [0, 2]


def test_aucune_piste():
    assert usable_tracks([], frame_h=1080) == []
