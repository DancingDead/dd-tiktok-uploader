import pytest

from speaker import iou, link_tracks


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
