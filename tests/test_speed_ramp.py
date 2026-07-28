"""Ramps de vitesse : règle pure (ralenti d'anticipation / accéléré de relance)
puis intégration dans build_edl. Aucun fichier média requis."""

import numpy as np
import pytest

from beatsync import DEFAULT_CONFIG, build_edl, is_impact, ramp_speed


def cfg(clip_speed=1.0, speed=True, **ramp):
    """Config minimale pour ramp_speed : effects.speed activé par défaut."""
    return {
        **DEFAULT_CONFIG,
        "clip_speed": clip_speed,
        "effects": {**DEFAULT_CONFIG["effects"], "speed": speed},
        "speed_ramp": {**DEFAULT_CONFIG["speed_ramp"], **ramp},
    }


# --- is_impact --------------------------------------------------------------


def test_impacts_are_multiples_of_impact_beats_from_the_anchor():
    assert is_impact(16, anchor=16, impact_beats=8)      # l'ancre elle-même
    assert is_impact(24, anchor=16, impact_beats=8)      # après
    assert is_impact(8, anchor=16, impact_beats=8)       # avant
    assert not is_impact(20, anchor=16, impact_beats=8)


def test_window_boundary_is_never_an_impact():
    """Les bornes de fenêtre portent beat_index = -1 : jamais un impact, même
    quand -1 tombe juste sur le modulo (ici anchor=7 → (-1-7) % 8 == 0)."""
    assert not is_impact(-1, anchor=7, impact_beats=8)


def test_impact_beats_zero_disables_impacts():
    assert not is_impact(16, anchor=16, impact_beats=0)


# --- ramp_speed -------------------------------------------------------------


def test_segment_ending_on_impact_slows_down():
    assert ramp_speed(4, 8, 1.0, anchor=0, config=cfg()) == pytest.approx(0.5)


def test_segment_starting_on_impact_speeds_up():
    assert ramp_speed(8, 12, 1.0, anchor=0, config=cfg()) == pytest.approx(1.4)


def test_slow_wins_when_segment_both_starts_and_ends_on_impact():
    assert ramp_speed(0, 8, 1.0, anchor=0, config=cfg()) == pytest.approx(0.5)


def test_plain_segment_keeps_the_global_clip_speed():
    assert ramp_speed(9, 11, 1.0, anchor=0, config=cfg(clip_speed=0.85)) == pytest.approx(0.85)


def test_short_segment_is_exempt_from_ramps():
    """Strobo à 1 beat : un 0.5x sur trois frames ne se voit pas et coûterait
    cher en interpolation."""
    assert ramp_speed(4, 8, 0.2, anchor=0, config=cfg()) == pytest.approx(1.0)


def test_speed_effect_disabled_returns_clip_speed():
    assert ramp_speed(4, 8, 1.0, anchor=0, config=cfg(speed=False)) == pytest.approx(1.0)


def test_ramp_values_are_clamped_to_the_engine_bounds():
    assert ramp_speed(4, 8, 1.0, anchor=0, config=cfg(slow=0.01)) == pytest.approx(0.5)
    assert ramp_speed(8, 12, 1.0, anchor=0, config=cfg(fast=9.0)) == pytest.approx(1.5)


# --- Intégration dans build_edl --------------------------------------------

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


def make_clips():
    from pathlib import Path
    return [
        {"path": Path(f"/clips/{n}.mp4"), "duration": 90.0,
         "width": 1920, "height": 1080, "ratio": 1920 / 1080}
        for n in ("a", "b", "c")
    ]


def test_build_edl_produces_both_slow_and_fast_segments():
    config = {**DEFAULT_CONFIG, "start": 0.0, "end": DURATION, "drop_time": 30.0}
    edl = build_edl(make_analysis(), make_clips(), config, seed=42)
    speeds = {round(e["speed"], 3) for e in edl}
    assert 0.5 in speeds, "aucun ralenti d'anticipation"
    assert 1.4 in speeds, "aucun accéléré de relance"


def test_build_edl_ramps_are_deterministic():
    config = {**DEFAULT_CONFIG, "start": 0.0, "end": DURATION, "drop_time": 30.0}
    a = build_edl(make_analysis(), make_clips(), config, seed=42)
    b = build_edl(make_analysis(), make_clips(), config, seed=42)
    assert [e["speed"] for e in a] == [e["speed"] for e in b]


def test_ramps_are_active_without_a_drop():
    """Sans drop connu (mode calme), l'ancre est le premier beat de la fenêtre :
    le motif reste actif."""
    config = {**DEFAULT_CONFIG, "start": 0.0, "end": DURATION, "drop_time": None}
    edl = build_edl(make_analysis(), make_clips(), config, seed=42)
    assert any(e["speed"] < 1.0 for e in edl)


# --- Flux optique au rendu --------------------------------------------------

from beatsync import _segment_filters  # noqa: E402


def seg(speed, ramp_slow=False):
    return {"timeline_start": 0.0, "duration": 1.0, "clip_path": "/clips/a.mp4",
            "clip_in": 0.0, "speed": speed, "ramp_slow": ramp_slow, "effects": [],
            "layout": "crop", "focus_x": 0.5, "clip_w": 1920, "clip_h": 1080}


def test_slowed_segment_gets_optical_flow_interpolation():
    joined = " ".join(_segment_filters(seg(0.5, ramp_slow=True), DEFAULT_CONFIG))
    assert "minterpolate=fps=30:mi_mode=mci" in joined
    # L'interpolation REMPLACE le fps= simple, elle ne s'y ajoute pas.
    assert ",fps=30," not in joined


def test_normal_and_fast_segments_keep_the_plain_fps_filter():
    for speed in (1.0, 1.4):
        joined = " ".join(_segment_filters(seg(speed), DEFAULT_CONFIG))
        assert "minterpolate" not in joined
        assert "fps=30" in joined


def test_interpolation_can_be_disabled():
    config = {**DEFAULT_CONFIG,
              "speed_ramp": {**DEFAULT_CONFIG["speed_ramp"], "interpolate": False}}
    joined = " ".join(_segment_filters(seg(0.5, ramp_slow=True), config))
    assert "minterpolate" not in joined
    assert "fps=30" in joined


def test_interpolation_applies_to_every_layout():
    for layout in ("crop", "split", "blur"):
        entry = {**seg(0.5, ramp_slow=True), "layout": layout}
        assert "minterpolate" in " ".join(_segment_filters(entry, DEFAULT_CONFIG))


def test_global_clip_speed_slowdown_does_not_trigger_interpolation():
    """Régression : un ralenti venant de `clip_speed` (réglage global de preset,
    0.5 à 1.5) n'est PAS un ralenti de ramp — minterpolate ne doit pas se
    déclencher dessus, même si la vitesse finale est < 1.0."""
    joined = " ".join(_segment_filters(seg(0.85, ramp_slow=False), DEFAULT_CONFIG))
    assert "minterpolate" not in joined
    assert "fps=30" in joined


def test_ramp_slowdown_triggers_interpolation_even_with_global_clip_speed():
    """Un ralenti de ramp déclenche bien minterpolate, même combiné à un
    clip_speed global qui n'est pas 1.0 (le champ `ramp_slow` est ce qui compte,
    pas la vitesse absolue)."""
    joined = " ".join(_segment_filters(seg(0.5, ramp_slow=True), DEFAULT_CONFIG))
    assert "minterpolate=fps=30:mi_mode=mci" in joined


# --- delogo exclu des images -------------------------------------------------


def test_delogo_is_not_applied_to_image_segments():
    """Une image fixe (affiche, visuel uploadé) n'a pas de logo de chaîne à
    gommer : le rectangle flouté de delogo l'abîmerait pour rien."""
    entry = {**seg(1.0), "kind": "image", "clip_w": 1920, "clip_h": 1080}
    config = {**DEFAULT_CONFIG, "delogo": True}
    joined = " ".join(_segment_filters(entry, config))
    assert "delogo" not in joined


def test_delogo_still_applies_to_video_segments():
    entry = {**seg(1.0), "kind": "video"}
    config = {**DEFAULT_CONFIG, "delogo": True}
    joined = " ".join(_segment_filters(entry, config))
    assert "delogo" in joined


# --- Fusion des coupes avant un impact -------------------------------------

from beatsync import merge_boundaries_before_impacts  # noqa: E402


def cuts(*beat_indices):
    """cut_beats factices : le timestamp n'entre pas dans la décision."""
    return [(float(b) * 0.5, b) for b in beat_indices]


def ramp_cfg(**ramp):
    return {
        **DEFAULT_CONFIG,
        "effects": {**DEFAULT_CONFIG["effects"], "speed": True},
        "speed_ramp": {**DEFAULT_CONFIG["speed_ramp"], **ramp},
    }


def test_cuts_inside_the_slow_window_are_removed():
    """anchor=0, impact_beats=8 → impacts sur 0, 8, 16. `slow_beats` est la
    LONGUEUR voulue du segment ralenti : à 2, on retire la seule coupe qui le
    couperait en deux (le beat 7 avant l'impact 8, le beat 15 avant le 16)."""
    kept = merge_boundaries_before_impacts(
        cuts(0, 4, 6, 7, 8, 12, 14, 15, 16), anchor=0, config=ramp_cfg(slow_beats=2))
    assert [b for _, b in kept] == [0, 4, 6, 8, 12, 14, 16]


def test_slow_beats_three_merges_two_cuts():
    """À 3, le segment ralenti couvre trois beats : on retire 6 ET 7."""
    kept = merge_boundaries_before_impacts(
        cuts(0, 4, 5, 6, 7, 8), anchor=0, config=ramp_cfg(slow_beats=3))
    assert [b for _, b in kept] == [0, 4, 5, 8]


def test_impact_beats_are_never_removed():
    kept = merge_boundaries_before_impacts(
        cuts(0, 8, 16), anchor=0, config=ramp_cfg(slow_beats=4))
    assert [b for _, b in kept] == [0, 8, 16]


def test_cuts_far_from_an_impact_are_kept():
    kept = merge_boundaries_before_impacts(
        cuts(1, 2, 3, 4, 5), anchor=0, config=ramp_cfg(slow_beats=2))
    assert [b for _, b in kept] == [1, 2, 3, 4, 5]


def test_slow_beats_zero_or_one_is_a_no_op():
    """1 = « le segment ralenti fait un beat », soit la grille actuelle."""
    original = cuts(0, 6, 7, 8)
    for slow_beats in (0, 1):
        assert merge_boundaries_before_impacts(
            original, anchor=0, config=ramp_cfg(slow_beats=slow_beats)) == original


def test_speed_effect_disabled_is_a_no_op():
    """Sans ramps, il n'y a pas de ralenti à allonger."""
    original = cuts(0, 6, 7, 8)
    config = {**ramp_cfg(slow_beats=2),
              "effects": {**DEFAULT_CONFIG["effects"], "speed": False}}
    assert merge_boundaries_before_impacts(original, anchor=0, config=config) == original


def test_never_returns_an_empty_list():
    """Fenêtre sans aucun impact et slow_beats couvrant tout l'intervalle :
    tout serait retiré. On rend l'original plutôt qu'un montage d'un seul plan."""
    original = cuts(1, 2, 3, 4, 5, 6, 7)
    assert merge_boundaries_before_impacts(
        original, anchor=0, config=ramp_cfg(slow_beats=8)) == original


def test_anchor_shifts_the_impact_grid():
    """anchor=3 → impacts sur 3, 11, 19. slow_beats=2 retire le beat 10."""
    kept = merge_boundaries_before_impacts(
        cuts(3, 5, 9, 10, 11), anchor=3, config=ramp_cfg(slow_beats=2))
    assert [b for _, b in kept] == [3, 5, 9, 11]


# --- Intégration dans build_edl --------------------------------------------


def test_slowed_segment_is_longer_than_a_strobe_segment():
    """Avec slow_beats=2, le segment ralenti dure au moins deux beats de
    timeline, là où le strobo coupe à chaque beat."""
    config = {**DEFAULT_CONFIG, "start": 0.0, "end": DURATION, "drop_time": 30.0,
              "speed_ramp": {**DEFAULT_CONFIG["speed_ramp"], "slow_beats": 2}}
    edl = build_edl(make_analysis(), make_clips(), config, seed=42)
    slowed = [e for e in edl if e["speed"] == pytest.approx(0.5)]
    assert slowed, "aucun segment ralenti"
    assert all(e["duration"] >= BEAT * 1.5 for e in slowed)


def test_slow_beats_zero_reproduces_the_previous_grid():
    """Non-régression : slow_beats=0 rend exactement l'EDL d'avant ce volet."""
    base = {**DEFAULT_CONFIG, "start": 0.0, "end": DURATION, "drop_time": 30.0}
    a = build_edl(make_analysis(), make_clips(),
                  {**base, "speed_ramp": {**DEFAULT_CONFIG["speed_ramp"], "slow_beats": 0}},
                  seed=42)
    b = build_edl(make_analysis(), make_clips(),
                  {**base, "speed_ramp": {**DEFAULT_CONFIG["speed_ramp"], "slow_beats": 1}},
                  seed=42)
    assert a == b  # slow_beats=1 ne retire rien non plus (fenêtre vide)
