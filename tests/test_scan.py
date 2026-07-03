"""Tests de classify_frames / usable_intervals — frames numpy synthétiques."""

import numpy as np
import pytest

from beatsync import classify_frames, usable_intervals

DT = 0.5
H, W = 18, 32

CRUNCHYROLL_ORANGE = (244, 117, 33)
NATURAL = (90, 110, 140)


def solid(color, n=1):
    frame = np.zeros((H, W, 3), dtype=np.uint8)
    frame[:] = color
    return np.stack([frame] * n)


def noisy(n, seed=0):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(n, H, W, 3), dtype=np.uint8)


def test_orange_card_flagged():
    frames = np.concatenate([solid(CRUNCHYROLL_ORANGE, 3), solid(NATURAL, 3)])
    result = classify_frames(frames, DT)
    assert list(result["orange"]) == [True, True, True, False, False, False]


def test_black_frames_flagged():
    frames = np.concatenate([solid((5, 5, 5), 2), solid(NATURAL, 2)])
    result = classify_frames(frames, DT)
    assert list(result["black"]) == [True, True, False, False]


def test_motion_low_on_static_high_on_changing():
    static = solid(NATURAL, 5)
    changing = noisy(5)
    r_static = classify_frames(static, DT)
    r_changing = classify_frames(changing, DT)
    assert r_static["motion"].max() < 0.01
    assert r_changing["motion"][1:].min() > 0.05


def test_usable_intervals_excludes_bad_zones():
    # 40 échantillons à 0.5 s = 20 s : orange 0-5, ok 6-25, noir 26-30, ok 31-39
    frames = np.concatenate(
        [solid(CRUNCHYROLL_ORANGE, 6), noisy(20, seed=1), solid((0, 0, 0), 5), noisy(9, seed=2)]
    )
    result = classify_frames(frames, DT)
    intervals = usable_intervals(result, duration=20.0, sample_dt=DT)
    assert len(intervals) == 2
    first, second = intervals
    assert 2.5 <= first["start"] <= 4.0 and 12.0 <= first["end"] <= 13.0
    assert 15.0 <= second["start"] <= 16.5 and 19.0 <= second["end"] <= 20.0
    assert all(iv["motion"] > 0.0 for iv in intervals)


def test_usable_intervals_drops_too_short_runs():
    # Un seul échantillon utilisable entre deux zones orange : trop court.
    frames = np.concatenate([solid(CRUNCHYROLL_ORANGE, 5), noisy(1), solid(CRUNCHYROLL_ORANGE, 5)])
    result = classify_frames(frames, DT)
    assert usable_intervals(result, duration=5.5, sample_dt=DT) == []


def test_interval_presence_from_presence_array():
    # 10 échantillons ok ; visages sur les 5 premiers, contours seuls ensuite.
    frames = noisy(10, seed=5)
    result = classify_frames(frames, DT)
    result["presence"] = np.array([1.0] * 5 + [0.5] * 5)
    intervals = usable_intervals(result, duration=5.0, sample_dt=DT)
    assert len(intervals) == 1
    assert intervals[0]["presence"] == pytest.approx(0.75)


def test_interval_presence_defaults_to_one_without_array():
    frames = noisy(10, seed=6)
    intervals = usable_intervals(classify_frames(frames, DT), duration=5.0, sample_dt=DT)
    assert intervals[0]["presence"] == pytest.approx(1.0)


def test_zero_presence_zone_splits_intervals():
    # 30 échantillons ok, mais personnages absents (présence 0) au milieu :
    # la zone vide coupe la plage en deux, comme une carte orange.
    frames = noisy(30, seed=7)
    result = classify_frames(frames, DT)
    result["presence"] = np.array([0.8] * 10 + [0.0] * 10 + [0.8] * 10)
    intervals = usable_intervals(result, duration=15.0, sample_dt=DT)
    assert len(intervals) == 2
    assert intervals[0]["end"] <= 5.5
    assert intervals[1]["start"] >= 9.5
    assert all(iv["presence"] == pytest.approx(0.8) for iv in intervals)


def test_slow_pan_interval_excluded_by_mean_motion():
    # Mouvement faible mais non nul (pan d'établissement ~0.015) : chaque
    # échantillon passe le filtre statique, mais la plage entière est écartée
    # car son mouvement MOYEN reste sous le seuil d'intérêt.
    base = solid(NATURAL, 12).astype(np.int16)
    base[::2] += 4  # alternance de 4 -> diff moyenne 4/255 ≈ 0.016
    frames = np.clip(base, 0, 255).astype(np.uint8)
    result = classify_frames(frames, DT)
    assert (result["motion"][1:] > 0.008).all()
    assert usable_intervals(result, duration=6.0, sample_dt=DT) == []


def test_static_zone_splits_intervals():
    # ok 0-9, statique 10-19, ok 20-29 : la zone figée (générique) est exclue.
    frames = np.concatenate([noisy(10, seed=3), solid(NATURAL, 10), noisy(10, seed=4)])
    result = classify_frames(frames, DT)
    intervals = usable_intervals(result, duration=15.0, sample_dt=DT)
    assert len(intervals) == 2
    assert intervals[0]["end"] <= 5.5
    assert intervals[1]["start"] >= 9.5
