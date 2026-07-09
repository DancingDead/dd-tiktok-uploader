"""Tests des parties pures de webui : merge des réglages (réexport de beatsync)."""

from webui import merge_settings


def test_merge_settings_nested_dicts():
    base = {
        "fps": 30,
        "effects": {"zoom": True, "flash": True, "shake": True, "speed": True},
        "accents": {"rgb": True, "glitch": True},
    }
    merged = merge_settings(base, {"fps": 60, "effects": {"shake": False}})
    assert merged["fps"] == 60
    assert merged["effects"] == {"zoom": True, "flash": True, "shake": False, "speed": True}
    assert merged["accents"] == {"rgb": True, "glitch": True}
    # la base n'est pas mutée
    assert base["fps"] == 30 and base["effects"]["shake"] is True


def test_merge_settings_ignores_unknown_keys():
    base = {"fps": 30}
    merged = merge_settings(base, {"inconnu": 1, "fps": 25})
    assert merged == {"fps": 25}
