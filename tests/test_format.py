"""Format de sortie : dérivation des dimensions et cadrage sensible au ratio."""

import pytest

from beatsync import DEFAULT_CONFIG, FORMATS, apply_format


def test_vertical_is_the_default():
    assert DEFAULT_CONFIG["format"] == "vertical"
    out = apply_format(dict(DEFAULT_CONFIG))
    assert (out["width"], out["height"]) == (1080, 1920)


def test_square_gives_square_dimensions():
    out = apply_format({**DEFAULT_CONFIG, "format": "carre"})
    assert (out["width"], out["height"]) == (1080, 1080)


def test_unknown_format_degrades_to_vertical():
    """Dégradation sûre, comme section et subtitles.mode."""
    out = apply_format({**DEFAULT_CONFIG, "format": "n'importe quoi"})
    assert (out["width"], out["height"]) == (1080, 1920)


def test_input_config_is_not_mutated():
    config = {**DEFAULT_CONFIG, "format": "carre"}
    apply_format(config)
    assert config["height"] == 1920


def test_nested_dicts_are_copied_not_shared():
    """Sans copie des dicts imbriqués, modifier la config de sortie polluerait
    celle de l'appelant — et donc la variante suivante du lot."""
    config = {**DEFAULT_CONFIG, "format": "carre", "effects": {"zoom": True}}
    out = apply_format(config)
    out["effects"]["zoom"] = False
    assert config["effects"]["zoom"] is True


def test_every_format_has_even_dimensions():
    """H.264 refuse les dimensions impaires."""
    for width, height in FORMATS.values():
        assert width % 2 == 0 and height % 2 == 0
