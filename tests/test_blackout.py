"""Strobe de build-up : grille comptée à rebours depuis le drop, puis entrées
noires dans l'EDL. Logique pure, aucun média requis."""

import pytest

from beatsync import DEFAULT_CONFIG, blackout_boundaries

FPS = 30.0
BEAT = 0.4   # 150 BPM : un demi-beat = 0,2 s = PILE 6 frames à 30 fps.
#              Un pas non entier en frames (0,25 s = 7,5 frames) rendrait
#              l'alternance mathématiquement irrégulière après quantification.


def cfg(**overrides):
    base = {**DEFAULT_CONFIG, "blackout_beats": 0.5}
    base["effects"] = {**DEFAULT_CONFIG["effects"], "blackout": True}
    base.update(overrides)
    return base


def grid(drop_out=4.0, window_end=8.0, **overrides):
    """Frontières d'origine : début de fenêtre, le drop, la fin. La fonction
    ne doit toucher qu'à ce qui précède le drop."""
    boundaries = [(0.0, -1), (2.0, -1), (drop_out, 42), (6.0, -1), (window_end, -1)]
    return blackout_boundaries(boundaries, drop_out, BEAT, cfg(**overrides), FPS)


def starts(boundaries):
    return [round(t, 4) for t, _ in boundaries]


def test_the_buildup_becomes_a_regular_alternation():
    """blackout_beats=0.5 à 150 BPM → un pas de 0,2 s, soit 6 frames pile.
    De 0 à 4 s, les frontières tombent donc sur 0,2 · 0,4 · … · 3,8."""
    bounds, _ = grid()
    before_drop = [t for t in starts(bounds) if t < 4.0]
    steps = [round(b - a, 4) for a, b in zip(before_drop, before_drop[1:])]
    assert set(steps) == {0.2}, f"pas irrégulier : {steps}"


def test_the_segment_ending_on_the_drop_is_an_image():
    """C'est la raison du comptage à rebours : l'impact tombe sur une image."""
    bounds, black = grid()
    last_before_drop = max(t for t, _ in bounds if t < 4.0)
    assert round(last_before_drop * FPS) not in black


def test_alternation_is_image_black_image_going_back_from_the_drop():
    bounds, black = grid()
    before = sorted(t for t, _ in bounds if t < 4.0)
    # k=0 est le segment qui finit sur le drop, k=1 celui d'avant, etc.
    for k, t in enumerate(reversed(before)):
        is_black = round(t * FPS) in black
        if k == len(before) - 1:
            continue  # le segment de tête a sa propre règle, testée à part
        assert is_black == (k % 2 == 1), f"k={k} (t={t}) devrait être {'noir' if k % 2 else 'image'}"


def test_the_head_segment_is_always_an_image():
    """Une vidéo qui s'ouvre sur du noir ressemble à un bug. On force l'image,
    quitte à avoir deux éclairs d'affilée au tout début.

    Avec cette fixture le cas est réellement exercé : 20 segments avant le
    drop, donc la tête est à k=19 — impaire, sa parité voudrait du noir."""
    bounds, black = grid()
    assert round(0.0 * FPS) not in black


def test_the_drop_boundary_keeps_its_beat_index():
    """C'est son indice qui fait du drop un impact pour ramp_speed : le perdre
    casserait le ralenti d'anticipation qui le précède."""
    bounds, _ = grid()
    assert (4.0, 42) in [(round(t, 4), b) for t, b in bounds]


def test_intermediate_boundaries_are_not_beats():
    bounds, _ = grid()
    for t, beat_index in bounds:
        if 0.0 < t < 4.0:
            assert beat_index == -1, f"la frontière {t} prétend être le beat {beat_index}"


def test_what_follows_the_drop_is_untouched():
    bounds, _ = grid()
    after = [(round(t, 4), b) for t, b in bounds if t > 4.0]
    assert after == [(6.0, -1), (8.0, -1)]


def test_boundaries_stay_sorted_and_at_least_one_frame_apart():
    bounds, _ = grid()
    times = starts(bounds)
    assert times == sorted(times)
    assert all(b - a >= 1.0 / FPS - 1e-9 for a, b in zip(times, times[1:]))


def test_a_step_longer_than_the_buildup_still_produces_a_grid():
    """blackout_beats=20 à 150 BPM = 8 s de pas, pour un build-up de 4 s.
    On ne doit pas rendre une grille vide ni perdre le drop."""
    bounds, _ = grid(blackout_beats=20.0)
    assert (0.0, -1) in [(round(t, 4), b) for t, b in bounds]
    assert 4.0 in starts(bounds)


def test_a_drop_at_the_very_start_leaves_the_grid_alone():
    """Pas de build-up à strober : rien à faire."""
    boundaries = [(0.0, -1), (0.0, 7), (4.0, -1)]
    bounds, black = blackout_boundaries(boundaries, 0.0, BEAT, cfg(), FPS)
    assert black == set()
