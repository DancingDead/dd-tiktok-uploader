"""Tests des leviers d'ambiance : config par défaut, filtres couleur/grain,
coercion glitch, slow-mo global. Logique pure, aucun média requis."""

import numpy as np
from pathlib import Path

from beatsync import DEFAULT_CONFIG, build_edl, color_grade_filter, grain_filter, glitch_amount


def test_default_config_has_ambiance_keys():
    assert DEFAULT_CONFIG["color_grade"] == "neutre"
    assert DEFAULT_CONFIG["grain"] == 0.0
    assert DEFAULT_CONFIG["clip_speed"] == 1.0


def test_color_grade_neutre_and_unknown_are_empty():
    assert color_grade_filter("neutre") == ""
    assert color_grade_filter("inconnu") == ""


def test_color_grade_known_values_return_eq_fragment():
    for grade in ("chaud", "froid", "delave"):
        frag = color_grade_filter(grade)
        assert frag.startswith("eq=")
    # chaud et froid diffèrent
    assert color_grade_filter("chaud") != color_grade_filter("froid")


def test_grain_zero_is_empty():
    assert grain_filter(0.0) == ""
    assert grain_filter(-0.5) == ""


def test_grain_low_is_noise_only():
    frag = grain_filter(0.2)
    assert frag.startswith("noise=alls=")
    assert "rgbashift" not in frag


def test_grain_high_adds_chroma_bleed():
    frag = grain_filter(0.8)
    assert frag.startswith("noise=alls=")
    assert "rgbashift" in frag  # dérive VHS au-delà de 0.6


def test_glitch_amount_bool_and_missing():
    assert glitch_amount({"glitch": True}) == 0.6
    assert glitch_amount({"glitch": False}) == 0.0
    assert glitch_amount({}) == 0.0


def test_glitch_amount_number_is_clamped():
    assert glitch_amount({"glitch": 0.35}) == 0.35
    assert glitch_amount({"glitch": 2.0}) == 1.0
    assert glitch_amount({"glitch": -1.0}) == 0.0


# --- Slow-mo global (clip_speed) --------------------------------------------

_BPM = 128.0
_BEAT = 60.0 / _BPM
_DURATION = 60.0


def _analysis():
    beats = np.arange(0.0, _DURATION, _BEAT)
    times = np.linspace(0.0, _DURATION, 601)
    # Rampe faible puis forte, comme make_analysis() dans test_build_edl.py :
    # évite les égalités dégénérées dans les rangs percentiles.
    energy = np.where(
        times < _DURATION / 2,
        np.interp(times, [0.0, _DURATION / 2], [0.05, 0.20]),
        np.interp(times, [_DURATION / 2, _DURATION], [0.80, 1.00]),
    )
    return {
        "duration": _DURATION,
        "bpm": _BPM,
        "beats": beats,
        "energy": energy,
        "energy_times": times,
    }


def _clip(name, duration):
    return {
        "path": Path(f"/clips/{name}"),
        "duration": duration,
        "width": 1920,
        "height": 1080,
        "ratio": 1920 / 1080,
    }


def _clips():
    return [_clip("a.mp4", 45.0), _clip("b.mp4", 60.0), _clip("c.mp4", 90.0)]


def test_clip_speed_propagates_to_all_segments():
    config = {
        **DEFAULT_CONFIG,
        "clip_speed": 0.85,
        "start": 0.0,
        "end": _DURATION,
        "drop_time": None,
    }
    edl = build_edl(_analysis(), _clips(), config, seed=1)
    assert edl, "EDL non vide"
    assert all(abs(entry["speed"] - 0.85) < 1e-9 for entry in edl)
