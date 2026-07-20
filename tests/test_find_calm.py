"""Tests de find_calm — enveloppes synthétiques, aucun média requis."""

import numpy as np

from beatsync import DEFAULT_CONFIG, find_calm

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


def test_finds_calm_valley():
    # Fort partout sauf une vallée calme [50, 90] s.
    analysis = make_analysis(lambda t: np.where((t >= 50.0) & (t < 90.0), 0.2, 0.9))
    start = find_calm(analysis, dict(DEFAULT_CONFIG), 30)
    assert start is not None
    assert 45.0 <= start <= 62.0


def test_avoids_silent_intro():
    # Intro MUETTE 0-30 s, section calme "musicale" 30-70 s, fort ensuite.
    def energy(t):
        return np.where(t < 30.0, 0.0, np.where(t < 70.0, 0.3, 0.9))

    start = find_calm(make_analysis(energy), dict(DEFAULT_CONFIG), 30)
    assert start is not None
    assert start >= 25.0  # pas l'intro muette


def test_calm_start_snapped_to_beat():
    analysis = make_analysis(lambda t: np.where((t >= 50.0) & (t < 90.0), 0.2, 0.9))
    start = find_calm(analysis, dict(DEFAULT_CONFIG), 30)
    assert np.min(np.abs(analysis["beats"] - start)) < 1e-9


def test_too_short_returns_none():
    analysis = make_analysis(lambda t: np.full_like(t, 0.5))
    assert find_calm(analysis, dict(DEFAULT_CONFIG), 999) is None


def test_calm_deterministic():
    analysis = make_analysis(lambda t: np.where((t >= 50.0) & (t < 90.0), 0.2, 0.9))
    a = find_calm(analysis, dict(DEFAULT_CONFIG), 30)
    b = find_calm(analysis, dict(DEFAULT_CONFIG), 30)
    assert a == b


from beatsync import resolve_window


def test_resolve_window_calm_sets_no_drop():
    analysis = make_analysis(lambda t: np.where((t >= 50.0) & (t < 90.0), 0.2, 0.9))
    config = dict(DEFAULT_CONFIG)
    config["section"] = "calm"
    resolve_window(analysis, config, start=None, duration=30)
    assert config["drop_time"] is None
    assert 45.0 <= config["start"] <= 65.0
    assert config["end"] <= analysis["duration"]


def test_resolve_window_drop_unchanged():
    # section="drop" par défaut : marche d'énergie -> drop détecté (non-régression).
    analysis = make_analysis(lambda t: np.where(t < 40.0, 0.15, 0.95))
    config = dict(DEFAULT_CONFIG)
    resolve_window(analysis, config, start=None, duration=30)
    assert config["drop_time"] is not None
    assert abs(config["drop_time"] - 40.0) <= 2.0


def test_resolve_window_explicit_start_wins_in_calm():
    analysis = make_analysis(lambda t: np.where((t >= 50.0) & (t < 90.0), 0.2, 0.9))
    config = dict(DEFAULT_CONFIG)
    config["section"] = "calm"
    resolve_window(analysis, config, start=12.0, duration=30)
    assert config["start"] == 12.0
    assert config["drop_time"] is None
