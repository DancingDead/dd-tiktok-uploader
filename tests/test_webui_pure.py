"""Tests des parties pures de webui : sérialisation TOML du plan, merge des réglages."""

import tomllib

from webui import merge_settings, plan_to_toml


def test_plan_to_toml_roundtrip():
    plan = {
        "defaults": {
            "duration": 30,
            "caption": '{title} — OUT NOW "🔥"',
            "hashtags": ["hardstyle", "anime"],
        },
        "posts": [
            {
                "track": "tracks/NLCK & HANNAH - Virus V4 (Radio Edit).wav",
                "title": "NLCK & HANNAH — Virus V4",
                "accounts": ["dd.main", "dd.edits"],
                "date": "2026-07-06",
                "time": "18:00",
            },
            {
                "track": "tracks/autre.mp3",
                "title": "Autre",
                "accounts": ["dd.main"],
                "date": "2026-07-07",
                "time": "21:00",
                "duration": 45,
                "hashtags": ["frenchcore"],
            },
        ],
    }
    assert tomllib.loads(plan_to_toml(plan)) == plan


def test_plan_to_toml_empty_posts():
    plan = {"defaults": {"duration": 30, "caption": "{title}", "hashtags": []}, "posts": []}
    parsed = tomllib.loads(plan_to_toml(plan))
    assert parsed["defaults"]["duration"] == 30
    assert parsed.get("posts", []) == []


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
