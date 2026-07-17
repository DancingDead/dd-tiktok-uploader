"""Tests des leviers d'ambiance : config par défaut, filtres couleur/grain,
coercion glitch, slow-mo global. Logique pure, aucun média requis."""

from beatsync import DEFAULT_CONFIG, color_grade_filter


def test_default_config_has_ambiance_keys():
    assert DEFAULT_CONFIG["color_grade"] == "neutre"
    assert DEFAULT_CONFIG["grain"] == 0.0
    assert DEFAULT_CONFIG["clip_speed"] == 1.0


def test_color_grade_neutre_and_unknown_are_empty():
    assert color_grade_filter("neutre") == ""
    assert color_grade_filter("inconnu") == ""


def test_color_grade_known_values_return_eq_fragment():
    for grade in ("chaud", "froid", "delave"):
        frag = color_grade_filter(grade)
        assert frag.startswith("eq=")
    # chaud et froid diffèrent
    assert color_grade_filter("chaud") != color_grade_filter("froid")
