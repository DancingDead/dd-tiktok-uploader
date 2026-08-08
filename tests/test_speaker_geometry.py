import pytest

from speaker import crop_expr, crop_size, smooth_track


def test_fill_holes_interpole_les_trous_interieurs():
    """Testé sur _fill_holes et non sur smooth_track : la moyenne glissante
    écraserait la rampe et le test ne dirait plus rien de l'interpolation."""
    from speaker import _fill_holes
    assert _fill_holes([100.0, None, 300.0], 0.0) == \
        pytest.approx([100.0, 200.0, 300.0])


def test_smooth_prolonge_les_trous_de_bord():
    """Pas de visage sur les premières frames : on tient la première valeur
    connue plutôt que d'inventer un mouvement."""
    out = smooth_track([None, None, 500.0], default=0.0, dead_zone=0.0)
    assert out == pytest.approx([500.0, 500.0, 500.0])


def test_smooth_sans_aucune_detection_retombe_sur_le_defaut():
    assert smooth_track([None] * 4, default=640.0, dead_zone=0.0) == \
        pytest.approx([640.0] * 4)


def test_smooth_absorbe_une_secousse_isolee():
    """Une fausse détection sur une frame ne doit pas déplacer le cadre."""
    centers = [500.0, 500.0, 900.0, 500.0, 500.0]
    out = smooth_track(centers, default=0.0, dead_zone=0.0)
    assert max(out) < 700.0     # la moyenne glissante écrase le pic


def test_dead_zone_immobilise_le_cadre_sur_les_petits_ecarts():
    out = smooth_track([500.0, 505.0, 510.0, 508.0], default=0.0, dead_zone=50.0)
    assert len(set(out)) == 1        # le cadre n'a pas bougé d'un pixel


def test_dead_zone_laisse_passer_un_vrai_deplacement():
    centers = [500.0] * 6 + [900.0] * 6
    out = smooth_track(centers, default=0.0, dead_zone=50.0)
    assert out[0] == pytest.approx(500.0)
    assert out[-1] > 800.0


def test_smooth_liste_vide():
    assert smooth_track([], default=100.0, dead_zone=0.0) == []


def test_crop_size_preleve_en_9_16_dans_du_16_9():
    # 1080 de haut → 607,5 de large en 9:16 → 606 (entier pair inférieur,
    # exigence yuv420p ; on rogne un pixel de trop plutôt qu'un de moins)
    assert crop_size(1920, 1080) == (606, 1080)


def test_crop_size_plafonne_a_la_largeur_source():
    """Une source déjà plus verticale que 9:16 ne se recadre pas en largeur."""
    assert crop_size(720, 1600) == (720, 1600)


def test_crop_expr_constante_quand_le_cadre_ne_bouge_pas():
    """Une seule valeur : pas d'expression conditionnelle, donc pas de coût
    d'évaluation par frame."""
    assert crop_expr([500.0] * 20, 2.0, 400, 1920) == "300"


def test_crop_expr_borne_le_cadre_dans_l_image():
    assert crop_expr([0.0] * 4, 2.0, 400, 1920) == "0"
    assert crop_expr([9999.0] * 4, 2.0, 400, 1920) == "1520"   # 1920 - 400


def test_crop_expr_interpole_entre_deux_points():
    """Un palier sec fait sauter le cadre d'un coup quand la zone morte cède :
    c'est un saut de cadrage, pas un panoramique. La trajectoire doit donc
    évoluer continûment entre deux points."""
    track = [200.0] * 4 + [1000.0] * 4     # 2 fps → point à t=0 puis t=2 s
    expr = crop_expr(track, 2.0, 400, 1920)
    assert expr.startswith("if(lt(t,")
    # De x=0 (t=0) à x=800 (t=2 s) : pente 400 px/s, pas de marche.
    assert "2*floor((0+400*t)/2)" in expr
    assert expr.endswith(",800)")


def _eval_expr(expr: str, t: float) -> float:
    """Évalue une expression crop_expr comme le ferait FFmpeg. Le sous-ensemble
    émis (`if`, `lt`, `floor`, arithmétique) se traduit littéralement en Python
    une fois les noms de fonctions renommés (`if` est un mot-clé)."""
    import math
    scope = {"t": t, "floor": math.floor,
             "if_": lambda cond, a, b: a if cond else b,
             "lt_": lambda x, y: x < y}
    return eval(expr.replace("if(", "if_(").replace("lt(", "lt_("), scope)


def test_crop_expr_est_continue_a_mi_chemin():
    """À mi-parcours entre deux points, la valeur est à mi-chemin — un palier
    rendrait encore la valeur de départ."""
    track = [200.0] * 4 + [1000.0] * 4
    expr = crop_expr(track, 2.0, 400, 1920)
    assert _eval_expr(expr, 0.0) == pytest.approx(0, abs=2)
    assert _eval_expr(expr, 1.0) == pytest.approx(400, abs=2)
    assert _eval_expr(expr, 2.0) == pytest.approx(800, abs=2)


def test_crop_expr_produit_des_valeurs_paires():
    """x impair = artefacts de chroma en yuv420p. 301 → 300, 705 → 704, et
    l'interpolation elle-même reste sur la grille paire."""
    expr = crop_expr([501.0, 703.0, 905.0, 1107.0], 2.0, 400, 1920)
    assert expr == "if(lt(t,1),2*floor((300+404*t)/2),704)"
    for tenth in range(0, 11):
        assert _eval_expr(expr, tenth / 10) % 2 == 0


def test_crop_expr_borne_le_nombre_de_paliers():
    from speaker import MAX_STEPS
    track = [float(i * 7 % 1500) for i in range(4000)]
    expr = crop_expr(track, 2.0, 400, 1920)
    assert expr.count("if(") <= MAX_STEPS


def test_crop_expr_trajectoire_vide():
    assert crop_expr([], 2.0, 400, 1920) == "760"    # centré : (1920-400)/2


def test_crop_size_arrondit_une_hauteur_impaire():
    """Une hauteur impaire fait échouer l'encodage yuv420p, donc un `failed`
    opaque à la toute fin du pipeline."""
    assert crop_size(1920, 1081) == (606, 1080)
