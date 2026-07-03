"""Tests de find_drop — enveloppes synthétiques, aucun média requis."""

import numpy as np

from beatsync import DEFAULT_CONFIG, find_drop

BPM = 128.0
BEAT = 60.0 / BPM
DURATION = 120.0


def make_analysis(energy_fn):
    beats = np.arange(0.0, DURATION, BEAT)
    times = np.linspace(0.0, DURATION, 1201)
    return {
        "duration": DURATION,
        "bpm": BPM,
        "beats": beats,
        "energy": energy_fn(times),
        "energy_times": times,
    }


def test_finds_energy_step():
    # Calme jusqu'à 40 s, fort ensuite : le drop est la marche.
    analysis = make_analysis(lambda t: np.where(t < 40.0, 0.15, 0.95))
    drop = find_drop(analysis, dict(DEFAULT_CONFIG))
    assert drop is not None
    assert abs(drop - 40.0) <= 2.0


def test_drop_snapped_to_a_beat():
    analysis = make_analysis(lambda t: np.where(t < 40.0, 0.15, 0.95))
    drop = find_drop(analysis, dict(DEFAULT_CONFIG))
    beats = analysis["beats"]
    assert np.min(np.abs(beats - drop)) < 1e-9


def test_buildup_ramp_then_drop():
    # Rampe qui monte (buildup) puis plateau fort : le drop est au début du
    # plateau, pas au début de la rampe.
    def energy(t):
        return np.where(t < 30.0, 0.1, np.where(t < 50.0, 0.1 + 0.4 * (t - 30.0) / 20.0, 0.95))

    analysis = make_analysis(energy)
    drop = find_drop(analysis, dict(DEFAULT_CONFIG))
    assert drop is not None
    assert 44.0 <= drop <= 56.0


def test_flat_energy_gives_none():
    analysis = make_analysis(lambda t: np.full_like(t, 0.5))
    assert find_drop(analysis, dict(DEFAULT_CONFIG)) is None
