"""Tests du cadrage intelligent : focus_x, layouts crop/split/blur, accents."""

from pathlib import Path

import numpy as np
import pytest

from beatsync import DEFAULT_CONFIG, build_edl

BPM = 128.0
BEAT = 60.0 / BPM
DURATION = 60.0
DROP = 30.0
SCAN_DT = 0.5
EPS = 1e-6


def make_analysis():
    beats = np.arange(0.0, DURATION, BEAT)
    times = np.linspace(0.0, DURATION, 601)
    return {
        "duration": DURATION,
        "bpm": BPM,
        "beats": beats,
        "energy": np.interp(times, [0.0, DURATION], [0.2, 1.0]),
        "energy_times": times,
    }


def make_clip(name, duration, interest_x=None, dual=None):
    n = int(duration / SCAN_DT)
    clip = {
        "path": Path(f"/clips/{name}"),
        "duration": duration,
        "width": 1920,
        "height": 1080,
        "ratio": 1920 / 1080,
        "intervals": [{"start": 0.0, "end": duration, "motion": 0.5, "presence": 0.8}],
        "scan_dt": SCAN_DT,
    }
    if interest_x is not None:
        clip["interest_x"] = np.full(n, interest_x) if np.isscalar(interest_x) else interest_x
    if dual is not None:
        clip["dual"] = np.full(n, dual, dtype=bool) if np.isscalar(dual) else dual
    return clip


def make_config(**overrides):
    config = dict(DEFAULT_CONFIG)
    config.update({"start": 0.0, "end": DURATION, "drop_time": DROP})
    config.update(overrides)
    return config


def test_focus_follows_detected_interest():
    clips = [make_clip("a.mp4", 150.0, interest_x=0.8, dual=False)]
    edl = build_edl(make_analysis(), clips, make_config(), seed=42)
    for entry in edl:
        assert entry["focus_x"] == pytest.approx(0.8, abs=0.05)
        assert entry["layout"] == "crop"


def test_split_layout_on_duel():
    clips = [make_clip("a.mp4", 150.0, interest_x=0.5, dual=True)]
    edl = build_edl(make_analysis(), clips, make_config(), seed=42)
    assert all(e["layout"] == "split" for e in edl)


def test_blur_layout_on_wide_spread():
    n = int(150.0 / SCAN_DT)
    zigzag = np.where(np.arange(n) % 2 == 0, 0.15, 0.85)  # action sur toute la largeur
    clips = [make_clip("a.mp4", 150.0, interest_x=zigzag, dual=False)]
    edl = build_edl(make_analysis(), clips, make_config(), seed=42)
    assert all(e["layout"] == "blur" for e in edl)


def test_defaults_without_scan_arrays():
    clips = [make_clip("a.mp4", 150.0)]
    edl = build_edl(make_analysis(), clips, make_config(), seed=42)
    for entry in edl:
        assert entry["focus_x"] == pytest.approx(0.5)
        assert entry["layout"] == "crop"
        # Dimensions source nécessaires au rendu (delogo en pixels)
        assert entry["clip_w"] == 1920 and entry["clip_h"] == 1080


def test_rgb_accent_on_drop_impact():
    clips = [make_clip("a.mp4", 150.0, interest_x=0.5, dual=False)]
    edl = build_edl(make_analysis(), clips, make_config(), seed=42)
    impact = [e for e in edl if e["section"] == "drop"][0]
    assert "rgb" in impact["effects"]


def test_accents_disabled():
    config = make_config(accents={"rgb": False, "glitch": False})
    clips = [make_clip("a.mp4", 150.0, interest_x=0.5, dual=False)]
    edl = build_edl(make_analysis(), clips, config, seed=42)
    for entry in edl:
        assert "rgb" not in entry["effects"]
        assert "glitch" not in entry["effects"]


def test_framing_reproducible():
    clips = [make_clip("a.mp4", 150.0, interest_x=0.7, dual=False)]
    a = build_edl(make_analysis(), clips, make_config(), seed=9)
    b = build_edl(make_analysis(), clips, make_config(), seed=9)
    assert a == b
