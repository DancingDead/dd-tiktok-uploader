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


def test_a_single_interval_spanning_the_whole_clip_still_yields_a_scene():
    """Un clip proprement scanné produit UNE plage couvrant presque tout le
    clip ; son début est avant le dernier tiers mais sa fin est dedans. Elle
    ne doit pas être écartée en bloc — seulement restreinte à sa portion
    utile, le dernier tiers."""
    clip = scanned_clip("a.mp4", duration=100.0, intervals=[iv(0.5, 99.5)])
    scene = find_final_scene([clip])
    assert scene is not None
    assert scene["interval"]["start"] == pytest.approx(100.0 * 2 / 3)
    assert scene["interval"]["end"] == pytest.approx(99.5)


def test_an_interval_entirely_before_the_tail_is_still_rejected():
    """Une plage dont la FIN est avant le dernier tiers reste écartée — ce
    n'est pas le cas que le finding 1 corrige."""
    clip = scanned_clip("a.mp4", duration=100.0, intervals=[iv(5.0, 15.0)])
    assert find_final_scene([clip]) is None


def test_min_source_excludes_intervals_too_short_to_supply_it():
    """Une plage plus courte que la source nécessaire n'est pas candidate —
    sinon le rendu lirait au-delà, dans des images que le scan a rejetées."""
    clip = scanned_clip("a.mp4", duration=100.0, intervals=[iv(90.0, 91.0)])  # 1 s utile
    assert find_final_scene([clip], min_source=2.0) is None
    assert find_final_scene([clip], min_source=0.5) is not None


def test_min_source_is_checked_against_the_restricted_portion():
    """La restriction du finding 1 s'applique AVANT le filtre de longueur : une
    plage large mais dont seule une petite portion tombe dans le dernier
    tiers doit être jugée sur cette portion, pas sur sa longueur totale."""
    clip = scanned_clip("a.mp4", duration=90.0, intervals=[iv(0.0, 61.0)])
    # tail_start = 60.0 ; portion utile = [60, 61] = 1 s.
    assert find_final_scene([clip], min_source=2.0) is None
    assert find_final_scene([clip], min_source=0.5) is not None


def test_tie_break_is_deterministic():
    """Scores strictement égaux : le clip dont le nom vient en premier, puis
    la plage la plus tardive. Deux appels donnent le même résultat."""
    a = scanned_clip("a.mp4", intervals=[iv(70.0, 80.0), iv(85.0, 95.0)])
    b = scanned_clip("b.mp4", intervals=[iv(70.0, 80.0)])
    first = find_final_scene([a, b])
    assert first["clip"]["path"].name == "a.mp4"
    assert first["interval"]["start"] == pytest.approx(85.0)
    assert find_final_scene([a, b])["interval"] == first["interval"]


# --- Montage dans l'EDL -----------------------------------------------------

BPM = 128.0
BEAT = 60.0 / BPM
DURATION = 60.0


def make_analysis():
    beats = np.arange(0.0, DURATION, BEAT)
    times = np.linspace(0.0, DURATION, 601)
    energy = np.where(
        times < DURATION / 2,
        np.interp(times, [0.0, DURATION / 2], [0.05, 0.20]),
        np.interp(times, [DURATION / 2, DURATION], [0.80, 1.00]),
    )
    return {"duration": DURATION, "bpm": BPM, "beats": beats,
            "energy": energy, "energy_times": times}


def catalog():
    """Deux clips scannés, exploitables de bout en bout, avec un duel dans la
    queue du second."""
    return [
        scanned_clip("a.mp4", intervals=[iv(1.0, 95.0)]),
        scanned_clip("b.mp4", intervals=[iv(1.0, 40.0), iv(70.0, 95.0)],
                     dual_ranges=[(70.0, 95.0)]),
    ]


def end_cfg(**end_scene):
    return {
        **DEFAULT_CONFIG,
        "start": 0.0, "end": DURATION, "drop_time": 30.0,
        "end_scene": {**DEFAULT_CONFIG["end_scene"], "enabled": True, **end_scene},
    }


def test_end_scene_is_a_single_segment_of_the_configured_length():
    edl = build_edl(make_analysis(), catalog(), end_cfg(beats=8), seed=42)
    final = [e for e in edl if e.get("end_scene")]
    assert len(final) == 1
    assert final[0]["duration"] == pytest.approx(8 * BEAT, abs=BEAT / 2)
    assert final[0] is edl[-1]


def test_end_scene_carries_speed_freeze_and_ramp_slow():
    edl = build_edl(make_analysis(), catalog(), end_cfg(speed=0.5, freeze=1.0), seed=42)
    final = edl[-1]
    assert final["speed"] == pytest.approx(0.5)
    assert final["freeze"] == pytest.approx(1.0)
    assert final["ramp_slow"] is True, "la scène de fin mérite le flux optique"


def test_end_scene_enters_at_the_end_of_the_chosen_interval():
    """Le climax est la conclusion du plan : on cale l'entrée sur la fin de la
    plage, pas sur son début."""
    edl = build_edl(make_analysis(), catalog(), end_cfg(beats=8, freeze=1.0, speed=0.5),
                    seed=42)
    final = edl[-1]
    source = (final["duration"] - final["freeze"]) * final["speed"]
    assert final["clip_in"] + source == pytest.approx(95.0, abs=0.1)


def test_end_scene_picks_the_duel_clip():
    edl = build_edl(make_analysis(), catalog(), end_cfg(), seed=42)
    assert edl[-1]["clip_path"].name == "b.mp4"


def test_disabled_end_scene_changes_nothing():
    analysis = make_analysis()
    off = {**DEFAULT_CONFIG, "start": 0.0, "end": DURATION, "drop_time": 30.0}
    a = build_edl(analysis, catalog(), off, seed=42)
    b = build_edl(analysis, catalog(),
                  {**off, "end_scene": {**DEFAULT_CONFIG["end_scene"], "enabled": False}},
                  seed=42)
    assert a == b
    assert all("end_scene" not in e for e in a)


def test_no_scene_found_falls_back_to_a_normal_montage():
    """Catalogue non scanné : find_final_scene rend None, le montage se termine
    normalement au lieu de casser."""
    raw = [{"path": Path("/clips/a.mp4"), "kind": "video", "duration": 100.0,
            "width": 1920, "height": 1080, "ratio": 16 / 9}]
    edl = build_edl(make_analysis(), raw, end_cfg(), seed=42)
    assert edl
    assert all(not e.get("end_scene") for e in edl)


def test_end_scene_skips_a_too_short_interval_and_falls_back():
    """Si la seule plage exploitable est trop courte pour la source requise,
    la scène de fin ne se déclenche pas — le montage se termine normalement
    plutôt que de lire au-delà, dans du non-scanné."""
    # Une plage longue au milieu du clip pour alimenter le montage ordinaire,
    # et une plage de 1 s tout à la fin — trop courte pour la scène de fin.
    short = [scanned_clip("a.mp4", intervals=[iv(1.0, 50.0), iv(94.0, 95.0)])]
    edl = build_edl(make_analysis(), short, end_cfg(beats=8, freeze=0.0, speed=1.0), seed=42)
    assert all(not e.get("end_scene") for e in edl)


def test_end_scene_swallowing_the_drop_is_rejected():
    """La réservation de la scène de fin ne doit jamais avaler la coupe
    garantie sur le drop : si le candidat tombe avant ou sur le drop, pas de
    scène de fin, et le montage garde sa coupe sur le drop."""
    bpm = 90.0
    beat = 60.0 / bpm
    duration = 20.0
    beats = np.arange(0.0, duration, beat)
    times = np.linspace(0.0, duration, 201)
    energy = np.linspace(0.2, 1.0, len(times))
    analysis = {"duration": duration, "bpm": bpm, "beats": beats,
                "energy": energy, "energy_times": times}
    cfg = {
        **DEFAULT_CONFIG,
        "start": 0.0, "end": duration, "drop_time": 10.0,
        "end_scene": {**DEFAULT_CONFIG["end_scene"], "enabled": True, "beats": 32},
    }
    edl = build_edl(analysis, catalog(), cfg, seed=42)
    assert all(not e.get("end_scene") for e in edl)
    drop_out = round(10.0 * cfg["fps"]) / cfg["fps"]
    assert any(abs(e["timeline_start"] - drop_out) < 1e-6 for e in edl), \
        "la coupe garantie sur le drop doit survivre"


def test_end_scene_is_reproducible():
    a = build_edl(make_analysis(), catalog(), end_cfg(), seed=7)
    b = build_edl(make_analysis(), catalog(), end_cfg(), seed=7)
    assert a == b


# --- Rendu du figé ----------------------------------------------------------

from beatsync import _segment_filters, _segment_input_args  # noqa: E402


def final_entry(**overrides):
    return {"timeline_start": 0.0, "duration": 4.0, "clip_path": Path("/clips/b.mp4"),
            "kind": "video", "clip_in": 10.0, "speed": 0.5, "ramp_slow": True,
            "end_scene": True, "freeze": 1.0, "effects": [], "layout": "crop",
            "focus_x": 0.5, "clip_w": 1920, "clip_h": 1080, **overrides}


def test_freeze_shortens_the_source_consumed():
    """4 s de timeline dont 1 s figée, à 0.5x → (4-1)*0.5 = 1.5 s de source.
    tpad clone la dernière image pendant la seconde restante. Pas de rab de
    seek ici : un segment à figé veut délibérément manquer de source."""
    args = _segment_input_args(final_entry())
    assert float(args[3]) == pytest.approx(1.5)


def test_no_freeze_leaves_the_source_untouched():
    args = _segment_input_args(final_entry(freeze=0.0))
    assert float(args[3]) == pytest.approx(4.0 * 0.5 + 0.5)


def test_freeze_drops_the_seek_margin_exactly():
    """Verrou : un segment à figé ne doit JAMAIS regagner le rab de seek, même
    « pour cohérence » avec les segments ordinaires — le rab, étiré par
    1/speed, peut annuler tout le budget de figé (freeze=1.0, speed=0.5 :
    0,5 s de rab devient 1,0 s de trop-plein réel, exactement le budget de
    figé). Seule une entrée SANS freeze garde les 0,5 s de marge."""
    with_freeze = _segment_input_args(final_entry(freeze=1.0, speed=0.5))
    assert float(with_freeze[3]) == pytest.approx(1.5)

    without_freeze = final_entry(speed=0.5)
    del without_freeze["freeze"]
    del without_freeze["end_scene"]
    no_freeze = _segment_input_args(without_freeze)
    assert float(no_freeze[3]) == pytest.approx(4.0 * 0.5 + 0.5)


def test_ordinary_entries_are_unaffected():
    entry = final_entry()
    del entry["freeze"]
    del entry["end_scene"]
    args = _segment_input_args(entry)
    assert float(args[3]) == pytest.approx(4.0 * 0.5 + 0.5)


def test_tpad_gets_room_for_the_freeze():
    joined = " ".join(_segment_filters(final_entry(freeze=1.5), DEFAULT_CONFIG))
    assert "tpad=stop_mode=clone:stop_duration=2.5" in joined


def test_tpad_default_is_unchanged_without_freeze():
    entry = final_entry()
    del entry["freeze"]
    joined = " ".join(_segment_filters(entry, DEFAULT_CONFIG))
    assert "tpad=stop_mode=clone:stop_duration=1" in joined
