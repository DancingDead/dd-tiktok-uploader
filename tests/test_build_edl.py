"""Tests de build_edl — logique pure, aucun fichier média requis.

Fixtures synthétiques : morceau de 60 s à 128 BPM, énergie faible sur la
première moitié et forte sur la seconde, trois clips factices.
"""

from pathlib import Path

import numpy as np
import pytest

from beatsync import DEFAULT_CONFIG, build_edl

BPM = 128.0
BEAT = 60.0 / BPM  # 0.46875 s
DURATION = 60.0
FPS = 30
FRAME = 1.0 / FPS
HALF_FRAME = FRAME / 2
EPS = 1e-6


def make_analysis():
    beats = np.arange(0.0, DURATION, BEAT)
    times = np.linspace(0.0, DURATION, 601)
    # Rampe faible (0.05→0.20) puis forte (0.80→1.00) : évite les égalités
    # dégénérées dans les rangs percentiles.
    energy = np.where(
        times < DURATION / 2,
        np.interp(times, [0.0, DURATION / 2], [0.05, 0.20]),
        np.interp(times, [DURATION / 2, DURATION], [0.80, 1.00]),
    )
    return {
        "duration": DURATION,
        "bpm": BPM,
        "beats": beats,
        "energy": energy,
        "energy_times": times,
    }


def make_clip(name: str, duration: float) -> dict:
    return {
        "path": Path(f"/clips/{name}"),
        "duration": duration,
        "width": 1920,
        "height": 1080,
        "ratio": 1920 / 1080,
    }


def make_clips():
    return [make_clip("a.mp4", 45.0), make_clip("b.mp4", 60.0), make_clip("c.mp4", 90.0)]


def make_config(**overrides):
    config = dict(DEFAULT_CONFIG)
    config.update({"start": 0.0, "end": DURATION})
    config.update(overrides)
    return config


# --- Reproductibilité -------------------------------------------------------


def test_same_seed_same_edl():
    analysis, clips = make_analysis(), make_clips()
    edl_a = build_edl(analysis, clips, make_config(), seed=42)
    edl_b = build_edl(analysis, clips, make_config(), seed=42)
    assert edl_a == edl_b
    assert len(edl_a) > 0


def test_different_seed_different_edl():
    analysis, clips = make_analysis(), make_clips()
    edl_a = build_edl(analysis, clips, make_config(), seed=1)
    edl_b = build_edl(analysis, clips, make_config(), seed=2)
    assert edl_a != edl_b


# --- Structure de la timeline ------------------------------------------------


def test_segments_contiguous_and_cover_window():
    edl = build_edl(make_analysis(), make_clips(), make_config(), seed=42)
    assert edl[0]["timeline_start"] == pytest.approx(0.0, abs=EPS)
    for prev, cur in zip(edl, edl[1:]):
        assert cur["timeline_start"] == pytest.approx(
            prev["timeline_start"] + prev["duration"], abs=EPS
        )
    total = edl[-1]["timeline_start"] + edl[-1]["duration"]
    assert total == pytest.approx(DURATION, abs=HALF_FRAME + EPS)


def test_timeline_starts_on_frame_grid():
    edl = build_edl(make_analysis(), make_clips(), make_config(), seed=42)
    assert edl
    for entry in edl:
        frames = entry["timeline_start"] * FPS
        assert frames == pytest.approx(round(frames), abs=EPS)


def test_durations_at_least_one_frame():
    edl = build_edl(make_analysis(), make_clips(), make_config(), seed=42)
    assert edl
    for entry in edl:
        assert entry["duration"] >= FRAME - EPS


def test_cuts_fall_on_beats():
    config = make_config()
    edl = build_edl(make_analysis(), make_clips(), config, seed=42)
    assert edl
    beats = make_analysis()["beats"]
    # Le premier segment démarre au début de la fenêtre, pas forcément sur un beat.
    for entry in edl[1:]:
        cut_in_track = entry["timeline_start"] + config["start"]
        nearest = np.min(np.abs(beats - cut_in_track))
        assert nearest <= HALF_FRAME + EPS


def test_window_respected():
    config = make_config(start=10.0, end=40.0)
    edl = build_edl(make_analysis(), make_clips(), config, seed=42)
    assert edl
    total = edl[-1]["timeline_start"] + edl[-1]["duration"]
    assert total == pytest.approx(30.0, abs=HALF_FRAME + EPS)
    beats = make_analysis()["beats"]
    for entry in edl[1:]:
        cut_in_track = entry["timeline_start"] + 10.0
        assert 10.0 - HALF_FRAME <= cut_in_track <= 40.0 + HALF_FRAME
        assert np.min(np.abs(beats - cut_in_track)) <= HALF_FRAME + EPS


# --- Sélection des clips ------------------------------------------------------


def test_extracts_within_clip_bounds():
    clips = make_clips()
    by_path = {c["path"]: c for c in clips}
    edl = build_edl(make_analysis(), clips, make_config(), seed=42)
    assert edl
    for entry in edl:
        clip = by_path[entry["clip_path"]]
        assert entry["clip_in"] >= -EPS
        assert entry["clip_in"] + entry["duration"] <= clip["duration"] + EPS


def test_no_immediate_clip_repeat():
    edl = build_edl(make_analysis(), make_clips(), make_config(), seed=42)
    for prev, cur in zip(edl, edl[1:]):
        assert prev["clip_path"] != cur["clip_path"]
    assert len({entry["clip_path"] for entry in edl}) >= 2


def test_short_clip_skipped_for_long_segments():
    clips = make_clips() + [make_clip("tiny.mp4", 0.2)]
    edl = build_edl(make_analysis(), clips, make_config(), seed=42)
    assert edl
    for entry in edl:
        if entry["clip_path"] == Path("/clips/tiny.mp4"):
            assert entry["duration"] <= 0.2 + EPS


def test_raises_when_no_clip_long_enough():
    clips = [make_clip("tiny.mp4", 0.05)]
    with pytest.raises(ValueError):
        build_edl(make_analysis(), clips, make_config(), seed=42)


# --- Rythme des coupes ---------------------------------------------------------


def test_energy_drives_cut_density():
    edl = build_edl(make_analysis(), make_clips(), make_config(cut_mode="energy"), seed=42)
    assert edl
    low = [e for e in edl if e["timeline_start"] < DURATION / 2]
    high = [e for e in edl if e["timeline_start"] >= DURATION / 2]
    assert len(high) >= 2 * len(low)


def test_fixed_mode_cuts_every_n_beats():
    config = make_config(cut_mode="fixed", cut_every=2)
    edl = build_edl(make_analysis(), make_clips(), config, seed=42)
    assert edl
    # Tous les segments (sauf éventuellement le dernier) durent 2 beats.
    for entry in edl[:-1]:
        assert entry["duration"] == pytest.approx(2 * BEAT, abs=FRAME + EPS)
