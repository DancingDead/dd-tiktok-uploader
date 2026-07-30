# Effet blackout — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter un effet de strobe de build-up — un nouvel extrait à chaque demi-temps, entrecoupé d'écrans noirs, jusqu'au drop.

**Architecture:** Une fonction pure réécrit la grille de frontières du build-up en une alternance comptée **à rebours depuis le drop**, et retourne l'ensemble des segments à noircir. `build_edl` émet alors des entrées `kind: "black"` sans clip ni cadrage, qui ne consomment ni catalogue ni tirage seedé. Au rendu, FFmpeg génère le noir par une source `lavfi` et la chaîne de filtres sort tôt sur une version minimale — mais conserve la punchline.

**Tech Stack:** Python 3 + uv, numpy, FFmpeg par `subprocess`, Flask (`webui.py`), React + TypeScript (`frontend/`), pytest.

**Spec:** `docs/superpowers/specs/2026-07-30-blackout-strobe-design.md`

**Branche :** `feat/blackout-strobe` (déjà créée, le spec y est commité). Partie de `02523f8` sur `master`.

## Global Constraints

- Commandes : `uv run pytest` (jamais `pip` — le venv n'a pas de module pip), `cd frontend && npm run build`.
- **Reproductibilité** : même seed + même config → même vidéo. `load_clips` trie par nom ; `build_edl` n'utilise que son `random.Random(seed)` local, jamais le RNG global, et **le nombre et l'ordre des tirages font partie du contrat** ; le rendu passe `-bitexact`.
- Les timestamps de cut sont quantifiés sur la grille de frames **dans `build_edl`**, jamais dans `render`.
- **Nombre de frames exact par segment** (`tpad` + `-frames:v`) : un segment noir compte comme n'importe quel autre.
- `build_edl` et `blackout_boundaries` sont **pures** : aucun I/O, aucun réseau.
- L'usine ne casse jamais sur un cas dégradé : sans drop dans la fenêtre, l'effet ne s'applique pas et le montage se déroule normalement.
- Commentaires, messages et libellés en **français**.
- Pas de sur-ingénierie : aucun réglage exposé qui n'est pas dans le spec. En particulier **pas** de cadence accélérée, **pas** de borne « N derniers beats », **pas** d'autre couleur que le noir.
- Un commit par tâche, message en français.

---

### Task 1 : `blackout_boundaries`

**Files:**
- Modify: `beatsync.py` — `DEFAULT_CONFIG` (`effects` ~ligne 43, nouveau `blackout_beats`), nouvelle fonction juste après `merge_boundaries_before_impacts` (~ligne 186)
- Test: `tests/test_blackout.py` (créer)

**Interfaces:**
- Consumes: rien.
- Produces:
  - `beatsync.DEFAULT_CONFIG["effects"]["blackout"]: bool` = `False`
  - `beatsync.DEFAULT_CONFIG["blackout_beats"]: float` = `0.5`
  - `beatsync.blackout_boundaries(boundaries: list[tuple[float, int]], drop_out: float, beat_dur: float, config: dict, fps: float) -> tuple[list[tuple[float, int]], set[int]]` — retourne les frontières réécrites et l'ensemble des **indices de frame** où commence un segment noir. La Task 2 consomme les deux.

- [ ] **Step 1 : Écrire le test qui échoue**

Créer `tests/test_blackout.py` :

```python
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
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_blackout.py -v`
Expected: FAIL — `ImportError: cannot import name 'blackout_boundaries' from 'beatsync'`

- [ ] **Step 3 : Ajouter les deux clés de config**

Dans `beatsync.py`, `DEFAULT_CONFIG`, remplacer la ligne `effects` par :

```python
    "effects": {"zoom": True, "flash": True, "shake": True, "speed": True,
                "blackout": False},     # strobe de build-up, opt-in
    "blackout_beats": 0.5,              # durée d'un éclair ET d'un noir, en beats
```

- [ ] **Step 4 : Écrire la fonction pure**

Dans `beatsync.py`, juste après `merge_boundaries_before_impacts` :

```python
def blackout_boundaries(boundaries: list[tuple[float, int]], drop_out: float,
                        beat_dur: float, config: dict,
                        fps: float) -> tuple[list[tuple[float, int]], set[int]]:
    """Remplace la grille du build-up par une alternance éclair / noir.

    Le comptage part **du drop et remonte** : c'est ce qui garantit que le
    segment se terminant sur le drop est un éclair d'image et non un noir —
    l'impact tombe donc sur une image. Compter depuis le début de la fenêtre
    laisserait la parité au hasard de la durée du build-up.

    Retourne les frontières réécrites et l'ensemble des **indices de frame** où
    commence un segment noir. Pure."""
    step = float(config.get("blackout_beats", 0.5)) * beat_dur
    frame = 1.0 / fps
    if step < frame or drop_out <= frame:
        return boundaries, set()          # rien à strober

    # Points de coupe à rebours depuis le drop, exclus.
    cuts: list[float] = []
    t = drop_out - step
    while t > frame - 1e-9:
        cuts.append(round(t * fps) / fps)
        t -= step
    cuts.reverse()
    # Doublons possibles après quantification si `step` est très court.
    cuts = [c for i, c in enumerate(cuts) if i == 0 or c - cuts[i - 1] >= frame - 1e-9]

    kept_after = [(t, b) for t, b in boundaries if t >= drop_out - 1e-9]
    rebuilt = [(0.0, -1)] + [(c, -1) for c in cuts if c >= frame - 1e-9] + kept_after

    # Un segment d'indice k compté à rebours depuis le drop (k=0 pour celui qui
    # s'y termine) est noir quand k est impair. Le segment de tête fait
    # exception : une vidéo qui s'ouvre sur du noir ressemble à un bug.
    starts_before = [t for t, _ in rebuilt if t < drop_out - 1e-9]
    black = {round(t * fps) for k, t in enumerate(reversed(starts_before))
             if k % 2 == 1 and k != len(starts_before) - 1}
    return rebuilt, black
```

- [ ] **Step 5 : Lancer les tests**

Run: `uv run pytest tests/test_blackout.py -v`
Expected: PASS (10 tests)

- [ ] **Step 6 : Lancer toute la suite**

Run: `uv run pytest -q`
Expected: PASS (343 tests + 10). Aucun test existant ne doit bouger : la fonction n'est encore appelée nulle part.

- [ ] **Step 7 : Commit**

```bash
git add beatsync.py tests/test_blackout.py
git commit -m "feat(montage): blackout_boundaries, grille du strobe de build-up

Le comptage part du drop et remonte : c'est ce qui garantit que le
segment s'y terminant est un éclair d'image et non un noir, donc que
l'impact tombe sur une image. Le segment de tête est forcé en image —
une vidéo qui s'ouvre sur du noir ressemble à un bug."
```

---

### Task 2 : Émettre les entrées noires dans l'EDL

**Files:**
- Modify: `beatsync.py` — `build_edl` : appel après le calcul de `drop_out` (~ligne 851), branche noire dans la boucle de segments (~ligne 917)
- Test: `tests/test_blackout.py` (ajouter une section)

**Interfaces:**
- Consumes: `blackout_boundaries(boundaries, drop_out, beat_dur, config, fps) -> (list, set[int])` (Task 1).
- Produces: une entrée d'EDL noire porte `kind: "black"`, `speed: 1.0`, `effects: []`, et **ni `clip_path`, ni `clip_in`, ni `focus_x`, ni `layout`, ni `clip_w`/`clip_h`, ni `crop`**. La Task 3 lit `entry.get("kind") == "black"`.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_blackout.py` :

```python
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


def test_the_drop_is_still_an_impact():
    """Le strobe ne doit pas voler au drop son statut d'impact : le segment
    qui s'y termine garde son ralenti d'anticipation. C'est ce que la
    conservation de l'indice de beat sur la frontière du drop protège."""
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
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_blackout.py -k "alternates or carry_no_clip" -v`
Expected: FAIL — `assert 'black' in kinds` : aucune entrée noire n'est produite

- [ ] **Step 3 : Appeler la fonction dans `build_edl`**

Dans `beatsync.py`, `build_edl`, juste **après** le bloc qui calcule `drop_out` et **avant** la déclaration de `consumed` :

```python
    # Strobe de build-up : la grille d'avant le drop devient une alternance
    # éclair / noir. Comptée à rebours depuis le drop, donc l'impact tombe
    # sur une image.
    black_frames: set = set()
    if effects_cfg.get("blackout") and drop_out is not None and len(beats) >= 2:
        boundaries, black_frames = blackout_boundaries(
            boundaries, drop_out, float(np.median(np.diff(beats))), config, fps)
```

- [ ] **Step 4 : Émettre la branche noire**

Dans la boucle de segments, juste **après** le calcul de `speed, ramp_slow = _ramp_decision(...)` et **avant** la branche de la scène de fin :

```python
        if round(seg_start * fps) in black_frames:
            # Écran noir : rien à montrer, donc rien à tirer et rien à
            # consommer au catalogue. FFmpeg génère la matière au rendu.
            edl.append(
                {
                    "timeline_start": seg_start,
                    "duration": duration,
                    "kind": "black",
                    "beat_index": beat_index,
                    "section": section,
                    "speed": 1.0,
                    "effects": [],
                }
            )
            continue
```

- [ ] **Step 5 : Lancer les tests**

Run: `uv run pytest tests/test_blackout.py -v && uv run pytest -q`
Expected: PASS partout. Les tests existants ne voient jamais `blackout: True` (défaut `False`), donc aucun ne doit bouger. Si l'un casse, c'est que la branche noire s'active à tort — revenir au Step 3.

- [ ] **Step 6 : Commit**

```bash
git add beatsync.py tests/test_blackout.py
git commit -m "feat(montage): entrées noires dans l'EDL

Une entrée kind=black n'a ni clip, ni cadrage, ni effets : elle ne montre
rien. Elle sort donc avant la sélection de clip — aucun tirage seedé
consommé, aucune matière retirée au catalogue."
```

---

### Task 3 : Rendre un segment noir

**Files:**
- Modify: `beatsync.py` — extraction de `_caption_filter` depuis `_segment_filters` (~lignes 1579-1603), branche noire dans `_segment_input_args` (~ligne 1497) et sortie anticipée dans `_segment_filters` (~ligne 1522)
- Test: `tests/test_blackout.py` (ajouter une section)

**Interfaces:**
- Consumes: `entry.get("kind") == "black"` (Task 2).
- Produces: `beatsync._caption_filter(entry: dict, config: dict) -> str | None` — le fragment `drawtext` d'un segment, ou `None` s'il n'y a pas de punchline ou pas de police.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_blackout.py` :

```python
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
    et deviendrait illisible. Sur fond noir il est au contraire très lisible."""
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
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_blackout.py -k "black_segment or black_source" -v`
Expected: FAIL — `KeyError: 'clip_path'` dans `_segment_input_args`

- [ ] **Step 3 : Extraire le fragment de punchline**

Dans `beatsync.py`, juste avant `_segment_filters`, créer la fonction en y déplaçant le bloc existant **sans changer sa logique** :

```python
def _caption_filter(entry: dict, config: dict) -> str | None:
    """Fragment `drawtext` d'un segment, ou None sans punchline ni police.
    Extrait pour être partagé par les segments ordinaires et les écrans noirs,
    qui gardent leur texte."""
    cap = entry.get("caption")
    subs = config.get("subtitles", {})
    font = resolve_caption_font(subs.get("font", "impact"))
    if not (cap and font):
        return None

    # Dernière ligne de défense avant le rendu : une valeur absente, `None`
    # ou non convertible (ex. formulaire vidé, JSON malformé) retombe sur
    # le défaut au lieu de faire planter le rendu — même principe que
    # generate_punchlines qui dégrade en `[]` plutôt que de bloquer l'usine.
    def _coerce(value, cast, default):
        try:
            return cast(value)
        except (TypeError, ValueError):
            return default

    cap_x = max(0.0, min(1.0, _coerce(subs.get("x", 0.5), float, 0.5)))
    cap_y = max(0.0, min(1.0, _coerce(subs.get("y", 0.74), float, 0.74)))
    cap_size = max(8, _coerce(subs.get("size", 64), int, 64))
    return (
        f"drawtext=fontfile={_drawtext_fontfile(font)}:text={_drawtext_escape(cap)}"
        f":fontsize={cap_size}:fontcolor=white:borderw=5:bordercolor=black@0.9"
        f":x=w*{cap_x:.4f}-text_w/2:y=h*{cap_y:.4f}-text_h/2"
    )
```

Dans `_segment_filters`, remplacer tout le bloc de la punchline (de `cap = entry.get("caption")` jusqu'au `post.append(...)` du `drawtext` inclus) par :

```python
    # Punchline incrustée (après les accents pour rester nette).
    caption = _caption_filter(entry, config)
    if caption:
        post.append(caption)
```

- [ ] **Step 4 : Ajouter la branche noire à `_segment_input_args`**

Une entrée d'EDL ne porte pas les dimensions de sortie — elles vivent dans la config. La signature gagne donc un paramètre optionnel, ce qui laisse intacts les appels existants à un seul argument (`tests/test_end_scene.py` et `tests/test_images.py` en font).

Remplacer la ligne de signature par :

```python
def _segment_input_args(entry: dict, config: dict | None = None) -> list[str]:
```

Puis, en tête du corps de la fonction — **avant** le calcul de `freeze` :

```python
    if entry.get("kind") == "black":
        # Écran noir : FFmpeg génère la matière, aucun fichier n'est ouvert.
        cfg = config or DEFAULT_CONFIG
        return ["-f", "lavfi", "-i",
                f"color=c=black:s={cfg['width']}x{cfg['height']}:r={cfg['fps']}"]
```

Dans `render`, l'appel devient `*_segment_input_args(entry, config)`.

- [ ] **Step 5 : Ajouter la sortie anticipée à `_segment_filters`**

Dans `_segment_filters`, juste après la ligne `width, height, fps = config["width"], config["height"], config["fps"]` :

```python
    if entry.get("kind") == "black":
        # La source lavfi est déjà aux bonnes dimensions : ni recadrage, ni
        # layout, ni effets. Seules la normalisation, le tpad et la punchline
        # ont un sens — le texte reste lisible sur le noir, et le faire
        # clignoter à 2 Hz serait pire que l'afficher.
        chain = [f"fps={fps}"]
        caption = _caption_filter(entry, config)
        if caption:
            chain.append(caption)
        chain.append("setsar=1,format=yuv420p")
        chain.append("tpad=stop_mode=clone:stop_duration=1")
        return ["-vf", ",".join(chain)]
```

- [ ] **Step 6 : Lancer les tests**

Run: `uv run pytest tests/test_blackout.py -v && uv run pytest -q`
Expected: PASS partout. `tests/test_subtitles.py` couvre le placement de la punchline et doit rester vert **sans modification** — c'est le contrôle que l'extraction du Step 3 n'a rien changé.

- [ ] **Step 7 : Vérifier sur un rendu réel**

Construire une EDL synthétique de trois segments — vidéo, noir, vidéo — et appeler `render()` dessus avec un clip réel de `clips/` et un morceau de `tracks/`. Puis vérifier objectivement :

```bash
ffprobe -v error -count_frames -select_streams v:0 \
  -show_entries stream=nb_read_frames -of csv=p=0 /tmp/blackout-test.mp4
```

Expected: le compte de frames vaut `durée_totale × fps`. Extraire ensuite une frame **au milieu du segment noir** et une **au milieu d'un segment vidéo**, et comparer leur luminance moyenne : la première doit être proche de 0, la seconde nettement au-dessus. Consigner les deux valeurs dans le rapport — un test de chaîne de caractères ne prouve pas qu'un écran est noir.

Ne rien déposer dans `clips/` : lire directement un fichier existant.

- [ ] **Step 8 : Commit**

```bash
git add beatsync.py tests/test_blackout.py
git commit -m "feat(rendu): écrans noirs générés par lavfi

Un segment noir n'ouvre aucun fichier : FFmpeg génère la matière. Sa
chaîne de filtres sort tôt — ni recadrage, ni layout, ni effets — mais
garde la punchline, qui clignoterait à 2 Hz sinon.

_caption_filter est extrait pour être partagé par les deux chemins plutôt
que dupliqué."
```

---

### Task 4 : Exposer l'effet dans l'éditeur de preset

**Files:**
- Modify: `webui.py` — `NUMERIC_OVERRIDE_KEYS` et `OVERRIDE_RANGES` (~lignes 32-44)
- Modify: `frontend/src/lib/api.ts` — type `Overrides.effects` (ligne 46)
- Modify: `frontend/src/features/presets/PresetEditor.tsx` — state (~ligne 122), `buildOverrides` (~ligne 158), rendu des bascules (~ligne 270)
- Test: `tests/test_webui_platform.py` (ajouter)

**Interfaces:**
- Consumes: `effects.blackout` et `blackout_beats` (Task 1).
- Produces: `coerce_overrides` borne `blackout_beats` dans `[0.25, 2.0]` et lève `ValueError` sur une valeur non convertible.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_webui_platform.py` :

```python
def test_coerce_overrides_clamps_blackout_beats():
    """En dessous de 0,25 beat le clignotement dépasse 4 Hz ; au-dessus de
    2 beats ce ne sont plus des éclairs."""
    assert coerce_overrides({"blackout_beats": 0.01})["blackout_beats"] == pytest.approx(0.25)
    assert coerce_overrides({"blackout_beats": 99})["blackout_beats"] == pytest.approx(2.0)


def test_coerce_overrides_accepts_a_numeric_string_for_blackout_beats():
    assert coerce_overrides({"blackout_beats": "0.5"})["blackout_beats"] == pytest.approx(0.5)


def test_coerce_overrides_rejects_a_non_numeric_blackout_beats():
    with pytest.raises(ValueError):
        coerce_overrides({"blackout_beats": "vite"})


def test_preset_with_blackout_round_trips(client):
    created = client.post("/api/presets", json={
        "name": "Strobe montée",
        "overrides": {"effects": {"zoom": True, "flash": True, "shake": True,
                                  "speed": True, "blackout": True},
                      "blackout_beats": 0.5}})
    assert created.status_code == 200
    presets = client.get("/api/state").get_json()["presets"]
    saved = next(p for p in presets if p["name"] == "Strobe montée")
    assert saved["overrides"]["effects"]["blackout"] is True
    assert saved["overrides"]["blackout_beats"] == pytest.approx(0.5)
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_webui_platform.py -k blackout -v`
Expected: FAIL — `KeyError: 'blackout_beats'`, la clé n'est pas coercée

- [ ] **Step 3 : Borner côté serveur**

Dans `webui.py`, ajouter `"blackout_beats"` au tuple `NUMERIC_OVERRIDE_KEYS` et l'entrée correspondante à `OVERRIDE_RANGES` :

```python
NUMERIC_OVERRIDE_KEYS = ("min_presence", "cut_every", "buildup", "strobe_beats",
                         "grain", "clip_speed", "blackout_beats")
```

```python
    "clip_speed": (0.5, 1.5),
    # En dessous de 0,25 beat le clignotement dépasse 4 Hz ; au-dessus de
    # 2 beats ce ne sont plus des éclairs.
    "blackout_beats": (0.25, 2.0),
```

- [ ] **Step 4 : Étendre le type TypeScript**

Dans `frontend/src/lib/api.ts`, ligne 46 :

```ts
  effects?: { zoom?: boolean; flash?: boolean; shake?: boolean; speed?: boolean; blackout?: boolean }
```

Et ajouter au même type, à côté des autres nombres :

```ts
  blackout_beats?: number
```

- [ ] **Step 5 : Ajouter la bascule à l'éditeur**

Dans `PresetEditor.tsx`, à côté des autres états d'effets :

```tsx
  const [blackout, setBlackout] = useState(o.effects?.blackout ?? false)
  const [blackoutBeats, setBlackoutBeats] = useState(o.blackout_beats ?? 0.5)
```

Dans `buildOverrides`, remplacer la ligne des effets et ajouter le nombre :

```tsx
    effects: { zoom, flash, shake, speed, blackout },
    blackout_beats: blackoutBeats,
```

Dans le rendu, après la dernière bascule d'effet :

```tsx
          <Toggle checked={blackout} onChange={setBlackout}>
            Strobe de build-up
          </Toggle>
```

Et, à côté des autres champs numériques :

```tsx
          <NumberField
            id="blackout-beats"
            label="Cadence du strobe (beats)"
            value={blackoutBeats}
            onChange={setBlackoutBeats}
            step={0.25}
            min={0.25}
            max={2}
            disabled={!blackout}
          />
```

Ajouter enfin, sous la bascule, une ligne d'aide dans le style des autres :

```tsx
          <p className="text-xs text-muted-foreground">
            Alterne éclairs d'image et écrans noirs jusqu'au drop. Double le nombre de
            segments : la génération est sensiblement plus longue.
          </p>
```

(`dirty` se calcule à partir d'un snapshot de `buildOverrides()` : les deux valeurs y entrent automatiquement, sans ligne dédiée.)

- [ ] **Step 6 : Lancer les tests et le build**

Run: `uv run pytest -q && cd frontend && npm run build`
Expected: PASS et build sans erreur TypeScript

- [ ] **Step 7 : Commit**

```bash
git add webui.py frontend/src/lib/api.ts frontend/src/features/presets/PresetEditor.tsx tests/test_webui_platform.py
git commit -m "feat(ui): expose le strobe de build-up dans les presets

Bascule « Strobe de build-up » à côté des autres effets, plus une cadence
bornée à [0.25, 2.0] beats côté serveur : sous 0,25 le clignotement
dépasse 4 Hz, au-dessus de 2 ce ne sont plus des éclairs."
```

---

### Task 5 : Documentation

**Files:**
- Modify: `CLAUDE.md` — puces `build_edl` et `render`

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: rien.

- [ ] **Step 1 : Vérifier chaque nom dans le code**

Avant d'écrire, relire dans `beatsync.py` et confirmer l'orthographe exacte de :
`blackout_boundaries`, `_caption_filter`, `effects.blackout`, `blackout_beats`,
et la valeur `kind: "black"`. Une doc qui ment est pire qu'une doc absente.

- [ ] **Step 2 : Documenter dans la puce `build_edl`**

Ajouter, dans le style dense des puces voisines :

> `effects.blackout` (opt-in) : le build-up devient une alternance éclair / écran noir au pas de `blackout_beats` (0,5 beat par défaut). `blackout_boundaries` (**pure et testée**) réécrit la grille en comptant **à rebours depuis le drop**, ce qui garantit que le segment s'y terminant est une image — l'impact tombe sur une image, jamais sur du noir. Le segment de tête est forcé en image. Les entrées `kind: "black"` n'ont ni clip ni cadrage : elles ne consomment ni catalogue ni tirage seedé. Double le nombre de segments du build-up, donc le temps de génération.

- [ ] **Step 3 : Documenter dans la puce `render`**

Ajouter :

> Écrans noirs : `_segment_input_args` retourne une source `lavfi` (`color=c=black`) — aucun fichier ouvert — et `_segment_filters` sort tôt sur une chaîne minimale (fps, punchline, normalisation, `tpad`). La punchline est **conservée** sur le noir : la faire clignoter à 2 Hz la rendrait illisible. `_caption_filter` est partagé par les deux chemins.

- [ ] **Step 4 : Contrôle final**

Run: `uv run pytest -q`
Expected: PASS (aucun changement de code)

- [ ] **Step 5 : Commit**

```bash
git add CLAUDE.md
git commit -m "docs: effet blackout, strobe de build-up entrelacé de noirs"
```

---

## Vérification finale

- [ ] `uv run pytest -q` — toute la suite passe
- [ ] `cd frontend && npm run build` — build sans erreur
- [ ] Un rendu réel avec l'effet activé sur une vraie niche : le build-up clignote, le drop tombe sur une image, la punchline reste lisible pendant les noirs
- [ ] Mesurer le temps de génération avec et sans l'effet, sur la même seed. Le spec annonce un doublement du nombre de segments ; consigner l'écart réel — c'est lui qui dira si l'effet est utilisable sur un lot de 20 variantes
- [ ] Regarder la vidéo produite en entier : dix secondes de clignotement à 2 Hz, c'est le point que seul l'œil peut juger
