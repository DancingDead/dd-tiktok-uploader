"""Tests de build_edl v2 : drop/strobo/gasp, effets, plages utilisables.

Fixture : 60 s à 128 BPM, drop exactement au beat 64 (t = 30.0 s), énergie
DÉCROISSANTE (1.0 → 0.1) pour que les beats post-drop soient à faible
percentile : sans l'override strobo ils seraient coupés tous les 4 beats.
"""

from pathlib import Path

import numpy as np
import pytest

from beatsync import DEFAULT_CONFIG, build_edl

BPM = 128.0
BEAT = 60.0 / BPM
DURATION = 60.0
DROP = 30.0  # = beat 64 exactement
FPS = 30
FRAME = 1.0 / FPS
EPS = 1e-6


def make_analysis():
    beats = np.arange(0.0, DURATION, BEAT)
    times = np.linspace(0.0, DURATION, 601)
    return {
        "duration": DURATION,
        "bpm": BPM,
        "beats": beats,
        "energy": np.interp(times, [0.0, DURATION], [1.0, 0.1]),
        "energy_times": times,
    }


def make_clip(name, duration, intervals=None):
    clip = {
        "path": Path(f"/clips/{name}"),
        "duration": duration,
        "width": 1920,
        "height": 1080,
        "ratio": 1920 / 1080,
    }
    if intervals is not None:
        clip["intervals"] = intervals
    return clip


def make_clips():
    return [make_clip("a.mp4", 45.0), make_clip("b.mp4", 60.0), make_clip("c.mp4", 90.0)]


def make_config(**overrides):
    config = dict(DEFAULT_CONFIG)
    config.update({"start": 0.0, "end": DURATION, "drop_time": DROP})
    config.update(overrides)
    return config


def drop_entries(edl):
    return [e for e in edl if e["section"] == "drop"]


# --- Drop : strobo, impact, gasp, sections -----------------------------------


def test_cut_lands_exactly_on_drop():
    edl = build_edl(make_analysis(), make_clips(), make_config(), seed=42)
    assert any(abs(e["timeline_start"] - DROP) <= FRAME / 2 + EPS for e in edl)


def test_strobe_one_beat_cuts_after_drop():
    edl = build_edl(make_analysis(), make_clips(), make_config(), seed=42)
    strobe_zone = [
        e for e in edl if DROP - EPS <= e["timeline_start"] < DROP + 16 * BEAT - BEAT / 2
    ]
    assert len(strobe_zone) >= 14
    for entry in strobe_zone:
        assert entry["duration"] == pytest.approx(BEAT, abs=FRAME + EPS)


def test_sections_split_at_drop():
    edl = build_edl(make_analysis(), make_clips(), make_config(), seed=42)
    for entry in edl:
        expected = "buildup" if entry["timeline_start"] < DROP - EPS else "drop"
        assert entry["section"] == expected
    assert drop_entries(edl)


def test_gasp_slowmo_on_last_buildup_segment():
    edl = build_edl(make_analysis(), make_clips(), make_config(), seed=42)
    before_drop = [e for e in edl if e["section"] == "buildup"]
    gasp = before_drop[-1]
    assert gasp["speed"] == pytest.approx(0.5)
    for entry in edl:
        if entry is not gasp:
            assert entry["speed"] == pytest.approx(1.0)


def test_no_gasp_when_speed_effect_disabled():
    config = make_config(effects={"zoom": True, "flash": True, "shake": True, "speed": False})
    edl = build_edl(make_analysis(), make_clips(), config, seed=42)
    assert all(e["speed"] == pytest.approx(1.0) for e in edl)


# --- Effets -------------------------------------------------------------------


def test_flash_on_drop_impact():
    edl = build_edl(make_analysis(), make_clips(), make_config(), seed=42)
    impact = drop_entries(edl)[0]
    assert "flash" in impact["effects"]
    assert "shake" in impact["effects"]


def test_zoom_on_all_drop_segments():
    edl = build_edl(make_analysis(), make_clips(), make_config(), seed=42)
    for entry in drop_entries(edl):
        assert "zoom" in entry["effects"]


def test_all_effects_disabled():
    config = make_config(effects={"zoom": False, "flash": False, "shake": False, "speed": False})
    edl = build_edl(make_analysis(), make_clips(), config, seed=42)
    assert all(e["effects"] == [] for e in edl)
    assert all(e["speed"] == pytest.approx(1.0) for e in edl)


def test_no_drop_time_means_no_drop_section():
    config = make_config(drop_time=None)
    edl = build_edl(make_analysis(), make_clips(), config, seed=42)
    assert all(e["section"] == "main" for e in edl)
    assert all(e["speed"] == pytest.approx(1.0) for e in edl)
    assert all("flash" not in e["effects"] for e in edl)


# --- Plages utilisables et bornes ---------------------------------------------


def test_bounds_account_for_speed():
    clips = make_clips()
    by_path = {c["path"]: c for c in clips}
    edl = build_edl(make_analysis(), clips, make_config(), seed=42)
    for entry in edl:
        clip = by_path[entry["clip_path"]]
        assert entry["clip_in"] + entry["duration"] * entry["speed"] <= clip["duration"] + EPS


def test_extracts_stay_inside_usable_intervals():
    intervals_a = [
        {"start": 5.0, "end": 15.0, "motion": 0.3},
        {"start": 30.0, "end": 40.0, "motion": 0.9},
    ]
    intervals_b = [{"start": 10.0, "end": 50.0, "motion": 0.5}]
    clips = [
        make_clip("a.mp4", 45.0, intervals=intervals_a),
        make_clip("b.mp4", 60.0, intervals=intervals_b),
    ]
    by_path = {c["path"]: c for c in clips}
    edl = build_edl(make_analysis(), clips, make_config(), seed=42)
    for entry in edl:
        needed = entry["duration"] * entry["speed"]
        ivs = by_path[entry["clip_path"]]["intervals"]
        assert any(
            iv["start"] - EPS <= entry["clip_in"] and entry["clip_in"] + needed <= iv["end"] + EPS
            for iv in ivs
        )


def test_clip_with_only_tiny_intervals_never_used():
    clips = [
        make_clip("tiny_iv.mp4", 60.0, intervals=[{"start": 2.0, "end": 2.2, "motion": 0.9}]),
        make_clip("b.mp4", 60.0),
    ]
    edl = build_edl(make_analysis(), clips, make_config(), seed=42)
    assert all(e["clip_path"] != Path("/clips/tiny_iv.mp4") for e in edl)


def test_fully_unusable_scanned_clip_never_used():
    # intervals == [] signifie « scanné, rien d'exploitable » : à exclure,
    # PAS à retomber sur le clip entier (contrairement à l'absence de scan).
    clips = [make_clip("unusable.mp4", 60.0, intervals=[]), make_clip("b.mp4", 60.0)]
    edl = build_edl(make_analysis(), clips, make_config(), seed=42)
    assert all(e["clip_path"] != Path("/clips/unusable.mp4") for e in edl)


def test_raises_when_all_intervals_too_small():
    clips = [make_clip("tiny_iv.mp4", 60.0, intervals=[{"start": 2.0, "end": 2.2, "motion": 0.9}])]
    with pytest.raises(ValueError):
        build_edl(make_analysis(), clips, make_config(), seed=42)
