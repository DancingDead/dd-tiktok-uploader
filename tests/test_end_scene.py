"""Scène de fin : sélection du climax (pure) puis montage dans l'EDL.
Aucun fichier média requis — les clips sont des dicts factices."""

from pathlib import Path

import numpy as np
import pytest

from beatsync import (DEFAULT_CONFIG, build_edl, find_final_scene,
                      interval_dual_ratio)


def scanned_clip(name, duration=100.0, intervals=None, dual_ranges=(), ratio=16 / 9):
    """Clip scanné factice. `dual_ranges` = liste de (début, fin) en secondes
    où le flag duel est vrai ; scan_dt = 0.5 s comme le scan réel (2 fps)."""
    dt = 0.5
    dual = np.zeros(int(duration / dt) + 1, dtype=bool)
    for a, b in dual_ranges:
        dual[int(a / dt):int(b / dt)] = True
    return {
        "path": Path(f"/clips/{name}"),
        "kind": "video",
        "duration": duration,
        "width": 1920, "height": 1080, "ratio": ratio,
        "intervals": intervals or [],
        "interest_x": np.full(len(dual), 0.5),
        "dual": dual,
        "scan_dt": dt,
    }


def iv(start, end, motion=0.5, presence=0.8):
    return {"start": start, "end": end, "motion": motion, "presence": presence}


# --- Fraction de duel d'une plage -------------------------------------------


def test_dual_ratio_counts_only_the_interval_samples():
    clip = scanned_clip("a.mp4", dual_ranges=[(90.0, 100.0)])
    assert interval_dual_ratio(clip, iv(90.0, 100.0)) == pytest.approx(1.0)
    assert interval_dual_ratio(clip, iv(10.0, 20.0)) == pytest.approx(0.0)


def test_dual_ratio_is_zero_without_scan_data():
    clip = scanned_clip("a.mp4")
    del clip["dual"]
    assert interval_dual_ratio(clip, iv(90.0, 100.0)) == pytest.approx(0.0)


# --- Sélection --------------------------------------------------------------


def test_only_the_last_third_is_considered():
    """Une plage du début, même parfaite, n'est jamais la scène de fin."""
    clip = scanned_clip("a.mp4", intervals=[iv(5.0, 15.0, motion=1.0, presence=1.0),
                                            iv(80.0, 90.0, motion=0.1, presence=0.1)],
                        dual_ranges=[(5.0, 15.0)])
    scene = find_final_scene([clip])
    assert scene is not None
    assert scene["interval"]["start"] == pytest.approx(80.0)


def test_duel_wins_at_equal_motion_and_presence():
    duel = scanned_clip("a.mp4", intervals=[iv(80.0, 90.0)], dual_ranges=[(80.0, 90.0)])
    plain = scanned_clip("b.mp4", intervals=[iv(80.0, 90.0)])
    scene = find_final_scene([duel, plain])
    assert scene["clip"]["path"].name == "a.mp4"


def test_motion_and_presence_break_a_tie_without_duel():
    weak = scanned_clip("a.mp4", intervals=[iv(80.0, 90.0, motion=0.1, presence=0.1)])
    strong = scanned_clip("b.mp4", intervals=[iv(80.0, 90.0, motion=1.0, presence=1.0)])
    assert find_final_scene([weak, strong])["clip"]["path"].name == "b.mp4"


def test_images_are_ignored():
    image = {"path": Path("/clips/x.png"), "kind": "image", "duration": None,
             "width": 1920, "height": 1080, "ratio": 16 / 9}
    clip = scanned_clip("a.mp4", intervals=[iv(80.0, 90.0)])
    assert find_final_scene([image, clip])["clip"]["path"].name == "a.mp4"


def test_unscanned_catalog_yields_no_scene():
    """Sans scan, pas de plages : on dégrade en None plutôt que d'inventer."""
    raw = {"path": Path("/clips/a.mp4"), "kind": "video", "duration": 100.0,
           "width": 1920, "height": 1080, "ratio": 16 / 9}
    assert find_final_scene([raw]) is None


def test_no_interval_in_the_last_third_yields_no_scene():
    clip = scanned_clip("a.mp4", intervals=[iv(5.0, 15.0)])
    assert find_final_scene([clip]) is None


def test_empty_catalog_yields_no_scene():
    assert find_final_scene([]) is None


def test_tie_break_is_deterministic():
    """Scores strictement égaux : le clip dont le nom vient en premier, puis
    la plage la plus tardive. Deux appels donnent le même résultat."""
    a = scanned_clip("a.mp4", intervals=[iv(70.0, 80.0), iv(85.0, 95.0)])
    b = scanned_clip("b.mp4", intervals=[iv(70.0, 80.0)])
    first = find_final_scene([a, b])
    assert first["clip"]["path"].name == "a.mp4"
    assert first["interval"]["start"] == pytest.approx(85.0)
    assert find_final_scene([a, b])["interval"] == first["interval"]
