"""Tests des leviers d'ambiance : config par défaut, filtres couleur/grain,
coercion glitch, slow-mo global. Logique pure, aucun média requis."""

from beatsync import DEFAULT_CONFIG


def test_default_config_has_ambiance_keys():
    assert DEFAULT_CONFIG["color_grade"] == "neutre"
    assert DEFAULT_CONFIG["grain"] == 0.0
    assert DEFAULT_CONFIG["clip_speed"] == 1.0
