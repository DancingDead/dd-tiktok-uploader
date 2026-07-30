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
    """C'est son indice qui fait du drop un impact pour `ramp_speed` — mais
    pour la relance accélérée (`fast`) qui SUIT le drop, pas pour le ralenti
    d'anticipation qui le précède : celui-ci ne s'applique plus une fois le
    strobe actif, remplacé par l'éclair qui termine le build-up. Perdre cet
    indice casserait quand même la relance."""
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


# --- Intégration dans build_edl --------------------------------------------

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

from beatsync import build_edl  # noqa: E402

BPM = 150.0
DURATION = 60.0


def make_analysis():
    beats = np.arange(0.0, DURATION, 60.0 / BPM)
    times = np.linspace(0.0, DURATION, 601)
    energy = np.where(
        times < DURATION / 2,
        np.interp(times, [0.0, DURATION / 2], [0.05, 0.20]),
        np.interp(times, [DURATION / 2, DURATION], [0.80, 1.00]),
    )
    return {"duration": DURATION, "bpm": BPM, "beats": beats,
            "energy": energy, "energy_times": times}


def clips():
    n = 1201
    return [{"path": Path(f"/clips/{name}.mp4"), "kind": "video", "duration": 600.0,
             "width": 1920, "height": 1080, "ratio": 16 / 9,
             "intervals": [{"start": 1.0, "end": 599.0, "motion": 0.5, "presence": 0.9}],
             "interest_x": np.full(n, 0.5), "dual": np.zeros(n, dtype=bool),
             "scan_dt": 0.5}
            for name in ("a", "b", "c")]


def edl_config(blackout=True, **overrides):
    return {**DEFAULT_CONFIG,
            "start": 0.0, "end": 30.0, "drop_time": 10.0,
            "effects": {**DEFAULT_CONFIG["effects"], "blackout": blackout},
            **overrides}


def test_the_buildup_alternates_video_and_black():
    edl = build_edl(make_analysis(), clips(), edl_config(), seed=42)
    buildup = [e for e in edl if e["section"] == "buildup"]
    kinds = [e["kind"] for e in buildup]
    assert "black" in kinds, "aucune entrée noire produite"
    assert kinds.count("video") >= 5
    # Deux noirs ne peuvent pas se suivre.
    assert not any(a == "black" and b == "black" for a, b in zip(kinds, kinds[1:]))


def test_black_entries_carry_no_clip():
    edl = build_edl(make_analysis(), clips(), edl_config(), seed=42)
    for entry in edl:
        if entry["kind"] == "black":
            assert "clip_path" not in entry
            assert "clip_in" not in entry
            assert "layout" not in entry
            assert entry["speed"] == pytest.approx(1.0)
            assert entry["effects"] == []


def test_the_drop_section_is_untouched():
    edl = build_edl(make_analysis(), clips(), edl_config(), seed=42)
    assert all(e["kind"] != "black" for e in edl if e["section"] == "drop")


def images():
    return [{"path": Path(f"/clips/{name}.png"), "kind": "image", "duration": None,
             "width": 1920, "height": 1080, "ratio": 16 / 9}
            for name in ("x", "y")]


def test_the_strobe_excludes_images_from_the_buildup():
    """« Un plan différent à chaque éclair » : le strobe ne doit pas piocher
    dans les images, sans quoi un catalogue riche en images transformerait
    une part de la montée en diaporama (mesuré : 7 éclairs sur 22 avant ce
    correctif). Les images restent utilisables partout ailleurs (drop, ou
    build-up sans strobe — voir le test suivant)."""
    edl = build_edl(make_analysis(), clips() + images(), edl_config(), seed=42)
    buildup_kinds = [e["kind"] for e in edl if e["section"] == "buildup"]
    assert "image" not in buildup_kinds
    assert "black" in buildup_kinds and "video" in buildup_kinds


def test_images_stay_eligible_elsewhere_when_the_strobe_is_off():
    """Le même catalogue mixte, strobe éteint : les images redeviennent
    éligibles (ici dans la section drop, dont les segments d'un beat sont
    déjà assez courts pour un flash)."""
    edl = build_edl(make_analysis(), clips() + images(), edl_config(blackout=False), seed=42)
    assert any(e["kind"] == "image" for e in edl)


def test_black_entries_do_not_consume_the_catalog():
    """Un noir ne montre rien : il ne doit pas retirer de matière aux clips.
    Preuve : à catalogue et seed égaux, les points d'entrée des extraits
    vidéo sont les mêmes que le strobe soit actif ou non... pour les
    segments qui existent dans les deux cas — on vérifie plus simplement
    qu'aucun extrait vidéo ne se recouvre, malgré les nombreux segments."""
    edl = build_edl(make_analysis(), clips(), edl_config(), seed=42)
    by_clip: dict = {}
    for e in edl:
        if e["kind"] != "video":
            continue
        by_clip.setdefault(e["clip_path"], []).append(
            (e["clip_in"], e["clip_in"] + e["duration"] * e["speed"]))
    for ranges in by_clip.values():
        for i, first in enumerate(ranges):
            for second in ranges[i + 1:]:
                assert not (first[0] < second[1] - 1e-9 and second[0] < first[1] - 1e-9)


def test_disabled_leaves_the_edl_identical():
    analysis = make_analysis()
    off = build_edl(analysis, clips(), edl_config(blackout=False), seed=42)
    assert all(e["kind"] != "black" for e in off)


def test_no_drop_means_no_strobe():
    analysis = make_analysis()
    edl = build_edl(analysis, clips(), edl_config(drop_time=None), seed=42)
    assert all(e["kind"] != "black" for e in edl)


def test_reproducible():
    a = build_edl(make_analysis(), clips(), edl_config(), seed=7)
    b = build_edl(make_analysis(), clips(), edl_config(), seed=7)
    assert a == b


def test_the_drop_beat_index_survives_so_the_ramp_pattern_still_applies():
    """Le strobe ne doit pas voler au drop son indice de beat : le motif de
    ramp reste calculable sur le segment qui s'y termine. `min_dur: 0.0` est
    forcé ici pour l'observer, car à réglages par défaut l'éclair qui précède
    le drop est plus court que `min_dur` et `_ramp_decision` l'exempte —
    CE test ne prouve donc PAS que le « gasp » (le ralenti d'anticipation)
    s'applique par défaut avec le strobe actif : il ne s'applique pas, le
    strobe le remplace par un éclair. Voir `test_the_drop_boundary_keeps_its_beat_index`
    pour ce que la conservation de l'indice protège réellement (la relance
    accélérée après le drop)."""
    config = edl_config(speed_ramp={**DEFAULT_CONFIG["speed_ramp"],
                                    "impact_beats": 8, "slow_beats": 1,
                                    "min_dur": 0.0})
    edl = build_edl(make_analysis(), clips(), config, seed=42)
    drop_start = min(e["timeline_start"] for e in edl if e["section"] == "drop")
    last_buildup = max((e for e in edl if e["section"] == "buildup"),
                       key=lambda e: e["timeline_start"])
    assert last_buildup["timeline_start"] + last_buildup["duration"] == pytest.approx(drop_start)
    assert last_buildup["kind"] == "video", "l'impact doit tomber sur une image"
    assert last_buildup["speed"] == pytest.approx(DEFAULT_CONFIG["speed_ramp"]["slow"])


# --- Rendu ------------------------------------------------------------------

from beatsync import _segment_filters, _segment_input_args  # noqa: E402


def black_entry(**overrides):
    return {"timeline_start": 0.0, "duration": 0.25, "kind": "black",
            "beat_index": -1, "section": "buildup", "speed": 1.0,
            "effects": [], **overrides}


def video_entry(**overrides):
    return {"timeline_start": 0.0, "duration": 1.0, "clip_path": Path("/clips/a.mp4"),
            "kind": "video", "clip_in": 5.0, "speed": 1.0, "effects": [],
            "layout": "crop", "focus_x": 0.5, "clip_w": 1920, "clip_h": 1080,
            **overrides}


def test_a_black_segment_opens_no_file():
    args = _segment_input_args(black_entry())
    assert "-f" in args and "lavfi" in args
    assert not any(a.endswith(".mp4") for a in args)
    assert "-ss" not in args and "-loop" not in args


def test_the_black_source_matches_the_output_size():
    args = " ".join(_segment_input_args(black_entry()))
    assert "color=c=black" in args
    assert "1080x1920" in args


def test_a_black_segment_has_no_crop_no_delogo_no_layout():
    joined = " ".join(_segment_filters(black_entry(), DEFAULT_CONFIG))
    for absent in ("delogo=", "zoompan=", "boxblur", "vstack", "minterpolate"):
        assert absent not in joined, f"{absent} ne devrait pas être là"


def test_a_black_segment_keeps_its_punchline():
    """Si le texte disparaissait un demi-temps sur deux, il clignoterait à 2 Hz
    et deviendrait illisible. Sur fond noir il est au contraire parfaitement lisible."""
    joined = " ".join(_segment_filters(black_entry(caption="LIEN EN BIO"), DEFAULT_CONFIG))
    assert "drawtext=" in joined
    assert "LIEN EN BIO" in joined


def test_a_black_segment_is_still_normalised_and_padded():
    joined = " ".join(_segment_filters(black_entry(), DEFAULT_CONFIG))
    assert "setsar=1,format=yuv420p" in joined
    assert "tpad=stop_mode=clone" in joined


def test_video_segments_are_unchanged_by_the_extraction():
    """L'extraction de _caption_filter est un refactor pur."""
    joined = " ".join(_segment_filters(video_entry(caption="TEST"), DEFAULT_CONFIG))
    assert "drawtext=" in joined
    assert "fontsize=64" in joined
    assert "x=w*0.5000-text_w/2" in joined
