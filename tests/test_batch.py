"""Tests de la logique pure de batch_generate : plan → jobs, seeds, captions."""

import pytest

from batch_generate import build_jobs, derive_seed, make_caption, output_stem, schedule_time

PLAN = {
    "defaults": {
        "duration": 30,
        "caption": "{title} — OUT NOW",
        "hashtags": ["hardstyle", "anime"],
    },
    "posts": [
        {
            "track": "tracks/virus.wav",
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
            "caption": "{title} 🔥",
            "hashtags": ["frenchcore"],
            "duration": 45,
        },
    ],
}


def test_build_jobs_expands_posts_by_account():
    jobs = build_jobs(PLAN)
    assert len(jobs) == 3
    assert [j["account"] for j in jobs] == ["dd.main", "dd.edits", "dd.main"]
    assert jobs[0]["track"] == "tracks/virus.wav"


def test_build_jobs_merges_defaults_and_overrides():
    jobs = build_jobs(PLAN)
    assert jobs[0]["duration"] == 30
    assert jobs[0]["hashtags"] == ["hardstyle", "anime"]
    assert jobs[2]["duration"] == 45
    assert jobs[2]["hashtags"] == ["frenchcore"]
    assert jobs[2]["caption_template"] == "{title} 🔥"


def test_build_jobs_rejects_missing_fields():
    with pytest.raises(ValueError):
        build_jobs({"posts": [{"track": "x.wav"}]})  # ni comptes, ni date


def test_derive_seed_stable_and_distinct():
    a = derive_seed("tracks/virus.wav", "dd.main", "2026-07-06")
    assert a == derive_seed("tracks/virus.wav", "dd.main", "2026-07-06")
    assert a != derive_seed("tracks/virus.wav", "dd.edits", "2026-07-06")
    assert a != derive_seed("tracks/virus.wav", "dd.main", "2026-07-07")


def test_make_caption_fills_template_and_hashtags():
    caption = make_caption("{title} — OUT NOW", "Virus V4", ["hardstyle", "anime"])
    assert caption == "Virus V4 — OUT NOW #hardstyle #anime"


def test_schedule_time_jitter_deterministic_and_bounded():
    a = schedule_time("2026-07-06", "18:00", seed=42)
    b = schedule_time("2026-07-06", "18:00", seed=42)
    c = schedule_time("2026-07-06", "18:00", seed=43)
    assert a == b
    assert a != c
    assert a.startswith("2026-07-06T18:")
    minutes = int(a[14:16])
    assert 0 <= minutes <= 14


def test_output_stem_is_filesystem_safe():
    job = {
        "account": "dd.main",
        "title": "NLCK & HANNAH — Virus V4 (Radio Edit)",
        "date": "2026-07-06",
        "time": "18:00",
    }
    stem = output_stem(job, seed=123)
    assert stem == "2026-07-06_18-00_dd.main_nlck-hannah-virus-v4-radio-edit_s123"
    assert "/" not in stem and " " not in stem
