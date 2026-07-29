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
