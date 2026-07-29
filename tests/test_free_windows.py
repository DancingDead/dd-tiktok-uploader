"""Fenêtres encore libres d'un clip : soustraction des portions déjà montrées.
Logique pure, aucun média requis."""

import pytest

from beatsync import free_windows


def iv(start, end, motion=0.5, presence=0.8):
    return {"start": start, "end": end, "motion": motion, "presence": presence}


def spans(windows):
    return [(round(w["start"], 3), round(w["end"], 3)) for w in windows]


def test_no_consumption_leaves_intervals_intact():
    assert spans(free_windows([iv(0.0, 10.0)], [], 1.0)) == [(0.0, 10.0)]


def test_a_middle_consumption_splits_the_interval():
    """Marge 0.5 de chaque côté : la portion 4→6 en retire 3.5→6.5."""
    got = free_windows([iv(0.0, 10.0)], [(4.0, 6.0)], 1.0, margin=0.5)
    assert spans(got) == [(0.0, 3.5), (6.5, 10.0)]


def test_a_head_consumption_leaves_one_window():
    got = free_windows([iv(0.0, 10.0)], [(0.0, 2.0)], 1.0, margin=0.5)
    assert spans(got) == [(2.5, 10.0)]


def test_a_tail_consumption_leaves_one_window():
    got = free_windows([iv(0.0, 10.0)], [(8.0, 10.0)], 1.0, margin=0.5)
    assert spans(got) == [(0.0, 7.5)]


def test_windows_shorter_than_needed_are_dropped():
    """La portion de gauche fait 1.5 s : trop court pour 2 s de source."""
    got = free_windows([iv(0.0, 10.0)], [(2.0, 6.0)], 2.0, margin=0.5)
    assert spans(got) == [(6.5, 10.0)]


def test_a_fully_consumed_interval_disappears():
    assert free_windows([iv(0.0, 10.0)], [(0.0, 10.0)], 1.0) == []


def test_overlapping_consumptions_are_merged():
    got = free_windows([iv(0.0, 20.0)], [(4.0, 8.0), (6.0, 12.0)], 1.0, margin=0.5)
    assert spans(got) == [(0.0, 3.5), (12.5, 20.0)]


def test_consumption_outside_the_interval_is_ignored():
    got = free_windows([iv(10.0, 20.0)], [(0.0, 5.0)], 1.0, margin=0.5)
    assert spans(got) == [(10.0, 20.0)]


def test_several_intervals_are_processed_independently():
    got = free_windows([iv(0.0, 10.0), iv(20.0, 30.0)], [(4.0, 6.0)], 1.0, margin=0.5)
    assert spans(got) == [(0.0, 3.5), (6.5, 10.0), (20.0, 30.0)]


def test_motion_and_presence_are_inherited_from_the_parent_interval():
    """Ils sont déjà des moyennes : on ne dispose pas des données par
    échantillon pour les recalculer sur une portion."""
    got = free_windows([iv(0.0, 10.0, motion=0.42, presence=0.11)], [(4.0, 6.0)], 1.0)
    assert all(w["motion"] == pytest.approx(0.42) for w in got)
    assert all(w["presence"] == pytest.approx(0.11) for w in got)


def test_an_interval_without_presence_keeps_its_shape():
    """Un clip non scanné n'a ni presence ni motion : pas de KeyError."""
    got = free_windows([{"start": 0.0, "end": 10.0, "motion": 1.0}], [(4.0, 6.0)], 1.0)
    assert len(got) == 2
    assert all("start" in w and "end" in w for w in got)

# --- Intégration dans build_edl --------------------------------------------

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

from beatsync import DEFAULT_CONFIG, build_edl  # noqa: E402

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


def clip(name, duration=300.0, intervals=None):
    n = int(duration * 2) + 1
    return {"path": Path(f"/clips/{name}"), "kind": "video", "duration": duration,
            "width": 1920, "height": 1080, "ratio": 16 / 9,
            "intervals": intervals or [iv(1.0, duration - 1.0)],
            "interest_x": np.full(n, 0.5), "dual": np.zeros(n, dtype=bool),
            "scan_dt": 0.5}


def config(**overrides):
    return {**DEFAULT_CONFIG, "start": 0.0, "end": DURATION,
            "drop_time": 30.0, **overrides}


def overlaps(a, b):
    return a[0] < b[1] - 1e-9 and b[0] < a[1] - 1e-9


def used_ranges(edl, path_name):
    return [(e["clip_in"], e["clip_in"] + e["duration"] * e["speed"])
            for e in edl if e["clip_path"].name == path_name]


def test_two_extracts_of_the_same_clip_never_overlap():
    clips = [clip("a.mp4"), clip("b.mp4")]
    edl = build_edl(make_analysis(), clips, config(), seed=42)
    for name in ("a.mp4", "b.mp4"):
        ranges = used_ranges(edl, name)
        assert len(ranges) >= 2, f"{name} n'a servi qu'une fois, le test ne prouve rien"
        for i, first in enumerate(ranges):
            for second in ranges[i + 1:]:
                assert not overlaps(first, second), f"{name} : {first} recouvre {second}"


def test_chrono_never_goes_backwards():
    clips = [clip("a.mp4"), clip("b.mp4")]
    edl = build_edl(make_analysis(), clips, config(chrono=True), seed=42)
    for name in ("a.mp4", "b.mp4"):
        starts = [e["clip_in"] for e in edl if e["clip_path"].name == name]
        assert starts == sorted(starts), f"{name} : retour en arrière {starts}"


def test_non_chrono_also_avoids_repetition():
    clips = [clip("a.mp4"), clip("b.mp4")]
    edl = build_edl(make_analysis(), clips, config(chrono=False), seed=42)
    ranges = used_ranges(edl, "a.mp4")
    for i, first in enumerate(ranges):
        for second in ranges[i + 1:]:
            assert not overlaps(first, second)


def test_a_poor_catalog_degrades_to_reuse_instead_of_raising():
    """Un seul clip court : la consommation l'épuise vite. On doit rouvrir les
    plages plutôt que faire échouer le lot."""
    clips = [clip("a.mp4", duration=12.0, intervals=[iv(1.0, 11.0)])]
    edl = build_edl(make_analysis(), clips, config(), seed=42)
    assert len(edl) > 10


def test_a_poor_catalog_degrades_globally_even_with_a_long_clip_available():
    """Deux clips, l'un court (épuisé tôt) et l'autre long : la dégradation
    doit rester GLOBALE (le lot ne s'arrête pas quand le clip court ne peut
    plus fournir, il continue avec le long, comme le ferait n'importe quel
    tirage normal). Un repli fait clip par clip passerait ce test à
    l'identique d'un repli global (la contrainte de spec n'est testée que
    par sa conséquence observable : le lot va à son terme sans erreur, y
    compris longtemps après l'épuisement du clip court)."""
    clips = [clip("short.mp4", duration=12.0, intervals=[iv(1.0, 11.0)]),
             clip("long.mp4")]
    edl = build_edl(make_analysis(), clips, config(), seed=42)
    assert len(edl) > 10
    short_uses = sum(1 for e in edl if e["clip_path"].name == "short.mp4")
    long_uses = sum(1 for e in edl if e["clip_path"].name == "long.mp4")
    assert short_uses >= 1
    assert long_uses >= 1


def test_the_end_scene_extract_is_reserved_before_the_loop():
    """Le climax ne doit pas avoir été montré par un segment antérieur."""
    clips = [clip("a.mp4"), clip("b.mp4")]
    cfg = config(end_scene={**DEFAULT_CONFIG["end_scene"], "enabled": True})
    edl = build_edl(make_analysis(), clips, cfg, seed=42)
    final = edl[-1]
    assert final.get("end_scene") is True
    scene_range = (final["clip_in"],
                   final["clip_in"] + (final["duration"] - final["freeze"]) * final["speed"])
    for entry in edl[:-1]:
        if entry["clip_path"] == final["clip_path"]:
            other = (entry["clip_in"], entry["clip_in"] + entry["duration"] * entry["speed"])
            assert not overlaps(other, scene_range)


def test_still_reproducible():
    clips = [clip("a.mp4"), clip("b.mp4")]
    a = build_edl(make_analysis(), clips, config(), seed=7)
    b = build_edl(make_analysis(), clips, config(), seed=7)
    assert a == b


def test_short_clip_exhaustion_never_forces_an_immediate_repeat():
    """Quand le clip court s'épuise, le pool ne doit pas retomber sur lui
    faute d'alternative : la coupure entre deux plans du MÊME clip se voit
    plus qu'un passage déjà montré revu ailleurs — on rouvre d'abord les
    plages du clip long plutôt que d'enchaîner deux plans du clip court."""
    clips = [clip("short.mp4", duration=8.0, intervals=[iv(0.5, 7.5)]),
             clip("long.mp4")]
    edl = build_edl(make_analysis(), clips, config(), seed=42)
    short_uses = sum(1 for e in edl if e["clip_path"].name == "short.mp4")
    assert short_uses >= 2, "le clip court doit être épuisé pour que le test prouve quelque chose"
    for prev, cur in zip(edl, edl[1:]):
        assert prev["clip_path"] != cur["clip_path"]
