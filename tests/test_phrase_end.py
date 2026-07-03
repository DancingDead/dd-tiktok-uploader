"""Tests de snap_end_to_phrase — la fin de fenêtre tombe sur une fin de phrase."""

import numpy as np
import pytest

from beatsync import snap_end_to_phrase

BPM = 128.0
BEAT = 60.0 / BPM  # 0.46875 s
PHRASE = 16 * BEAT  # 7.5 s


def beats(duration=200.0):
    return np.arange(0.0, duration, BEAT)


def test_extends_to_next_phrase_boundary_after_drop():
    # Drop à 40 s, fin nominale à 60 s = drop + 20 s, en plein milieu d'une
    # phrase (20/7.5 = 2.67) : on étend à drop + 3 phrases = 62.5 s.
    end = snap_end_to_phrase(60.0, drop_time=40.0, beats=beats(), track_duration=200.0)
    assert end == pytest.approx(40.0 + 3 * PHRASE, abs=1e-6)


def test_keeps_end_already_on_boundary():
    end = snap_end_to_phrase(40.0 + 2 * PHRASE, drop_time=40.0, beats=beats(), track_duration=200.0)
    assert end == pytest.approx(40.0 + 2 * PHRASE, abs=1e-6)


def test_snaps_down_when_extension_exceeds_track():
    # La phrase suivante dépasserait la fin du morceau : on retombe sur la
    # dernière frontière qui tient dans le morceau.
    track_end = 40.0 + 2.5 * PHRASE
    end = snap_end_to_phrase(40.0 + 2.2 * PHRASE, drop_time=40.0, beats=beats(track_end),
                             track_duration=track_end)
    assert end == pytest.approx(40.0 + 2 * PHRASE, abs=1e-6)


def test_without_drop_returns_end_unchanged():
    assert snap_end_to_phrase(60.0, drop_time=None, beats=beats(), track_duration=200.0) == 60.0


def test_uses_median_beat_period():
    # Grille légèrement irrégulière : la période médiane fait foi.
    irregular = np.cumsum([BEAT + (0.01 if i % 7 == 0 else 0.0) for i in range(300)])
    end = snap_end_to_phrase(60.0, drop_time=40.0, beats=irregular, track_duration=200.0)
    period = float(np.median(np.diff(irregular)))
    n_phrases = (end - 40.0) / (16 * period)
    assert n_phrases == pytest.approx(round(n_phrases), abs=1e-6)
