"""Tests du mode chronologique et du filtre de présence dans build_edl."""

from pathlib import Path

import numpy as np
import pytest

from beatsync import DEFAULT_CONFIG, build_edl

BPM = 128.0
BEAT = 60.0 / BPM
DURATION = 60.0
EPS = 1e-6
MONO_TOL = 0.5  # tolérance de clamp en bord de plage


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


def make_config(**overrides):
    config = dict(DEFAULT_CONFIG)
    config.update({"start": 0.0, "end": DURATION, "drop_time": None, "chrono": True})
    config.update(overrides)
    return config


def clip_ins_of(edl, path):
    return [e["clip_in"] for e in edl if e["clip_path"] == path]


def test_chrono_clip_ins_progress_forward():
    clips = [make_clip("a.mp4", 150.0)]
    edl = build_edl(make_analysis(), clips, make_config(), seed=42)
    ins = clip_ins_of(edl, Path("/clips/a.mp4"))
    assert len(ins) > 10
    for prev, cur in zip(ins, ins[1:]):
        assert cur >= prev - MONO_TOL


def test_chrono_covers_start_and_end_of_story():
    clips = [make_clip("a.mp4", 150.0)]
    edl = build_edl(make_analysis(), clips, make_config(), seed=42)
    ins = clip_ins_of(edl, Path("/clips/a.mp4"))
    assert ins[0] < 150.0 * 0.25          # le montage démarre au début de l'histoire
    assert ins[-1] > 150.0 * 0.60         # et finit vers le climax


def test_chrono_monotonic_per_clip_with_multiple_clips():
    clips = [make_clip("a.mp4", 120.0), make_clip("b.mp4", 90.0)]
    edl = build_edl(make_analysis(), clips, make_config(), seed=42)
    for path in (Path("/clips/a.mp4"), Path("/clips/b.mp4")):
        ins = clip_ins_of(edl, path)
        assert len(ins) > 3
        for prev, cur in zip(ins, ins[1:]):
            assert cur >= prev - MONO_TOL


def test_low_presence_intervals_avoided():
    intervals = [
        {"start": 0.0, "end": 60.0, "motion": 0.5, "presence": 0.05},
        {"start": 60.0, "end": 120.0, "motion": 0.5, "presence": 0.9},
    ]
    clips = [make_clip("a.mp4", 120.0, intervals=intervals)]
    edl = build_edl(make_analysis(), clips, make_config(), seed=42)
    for entry in edl:
        assert entry["clip_in"] >= 60.0 - EPS


def test_all_low_presence_falls_back_gracefully():
    intervals = [{"start": 0.0, "end": 120.0, "motion": 0.5, "presence": 0.05}]
    clips = [make_clip("a.mp4", 120.0, intervals=intervals)]
    edl = build_edl(make_analysis(), clips, make_config(), seed=42)
    assert len(edl) > 10


def test_chrono_off_keeps_free_random_order():
    clips = [make_clip("a.mp4", 150.0)]
    edl = build_edl(make_analysis(), clips, make_config(chrono=False), seed=42)
    ins = clip_ins_of(edl, Path("/clips/a.mp4"))
    diffs = np.diff(ins)
    assert (diffs < 0).any()  # au moins un retour en arrière : ordre libre


def test_chrono_reproducible_and_seed_sensitive():
    clips = [make_clip("a.mp4", 150.0), make_clip("b.mp4", 90.0)]
    a = build_edl(make_analysis(), clips, make_config(), seed=7)
    b = build_edl(make_analysis(), clips, make_config(), seed=7)
    c = build_edl(make_analysis(), clips, make_config(), seed=8)
    assert a == b
    assert a != c
