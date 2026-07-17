"""Tests des leviers d'ambiance : config par défaut, filtres couleur/grain,
coercion glitch, slow-mo global. Logique pure, aucun média requis."""

from beatsync import DEFAULT_CONFIG, color_grade_filter, grain_filter


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


def test_grain_zero_is_empty():
    assert grain_filter(0.0) == ""
    assert grain_filter(-0.5) == ""


def test_grain_low_is_noise_only():
    frag = grain_filter(0.2)
    assert frag.startswith("noise=alls=")
    assert "rgbashift" not in frag


def test_grain_high_adds_chroma_bleed():
    frag = grain_filter(0.8)
    assert frag.startswith("noise=alls=")
    assert "rgbashift" in frag  # dérive VHS au-delà de 0.6
