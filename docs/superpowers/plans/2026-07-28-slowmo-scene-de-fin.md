# Ralentis plus longs, scène de fin, format carré — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendre les ralentis assez longs pour se voir, conclure le montage par un ralenti figé sur le climax narratif, et permettre une sortie carrée en plus du 9:16.

**Architecture:** Trois volets sur les points d'extension existants de `beatsync.py`. Volet A post-traite la liste des beats de coupe avant la construction des frontières. Volet B ajoute une fonction pure de sélection (`find_final_scene`) et réserve les derniers beats de la fenêtre à un segment unique ; le figé réutilise le `tpad=stop_mode=clone` déjà en place. Volet C introduit un champ `format` qui pose `width`/`height`, et rend les deux cadrages de secours fonction du ratio de sortie. Un bloc de cadrage aujourd'hui inline dans `build_edl` est extrait en fonction pure, parce que deux appelants en ont désormais besoin.

**Tech Stack:** Python 3 + uv, numpy, FFmpeg/ffprobe par `subprocess`, Flask (`webui.py`), React + TypeScript + shadcn/ui (`frontend/`), pytest.

**Spec:** `docs/superpowers/specs/2026-07-28-slowmo-scene-de-fin-design.md`

**Branche :** `feat/slowmo-scene-de-fin` (déjà créée, le spec y est commité). Partie de `3a00e22` sur `master`.

## Global Constraints

- Commandes : `uv run pytest` (jamais `pip` — le venv n'a pas de module pip), `cd frontend && npm run build`.
- **Reproductibilité** : même seed + même config → même vidéo. `load_clips` trie par nom ; `build_edl` n'utilise que son `random.Random(seed)` local, jamais le RNG global, et **l'ordre des tirages fait partie du contrat** ; le rendu passe `-bitexact`.
- Les timestamps de cut sont quantifiés sur la grille de frames **dans `build_edl`**, jamais dans `render`.
- **Nombre de frames exact par segment** (`tpad` + `-frames:v`) : sans lui, les sources à 23,976 fps rendent quelques ms de moins et le concat accumule une dérive audio/vidéo.
- `build_edl`, `ramp_speed`, `is_impact`, `merge_boundaries_before_impacts`, `find_final_scene`, `apply_format`, `frame_extract` sont **pures** : aucun I/O, aucun réseau.
- La vitesse d'un segment reste bornée à `[0.5, 1.5]` par `_clamp_speed`.
- L'usine ne casse jamais sur un cas dégradé : une fonction qui ne trouve rien retourne `None` ou une valeur neutre, elle ne lève pas.
- Commentaires, messages et libellés en **français**.
- Pas de sur-ingénierie : aucun réglage exposé qui n'est pas dans le spec.
- Un commit par tâche, message en français.

## Écart assumé par rapport au spec

Le spec déclare `find_final_scene(clips, config)`. Le plan retient
`find_final_scene(clips)` : aucun champ de config n'entre dans la sélection (les
poids et la fraction de queue sont des constantes de module), et un paramètre
inutilisé est un mensonge sur la signature. Rien d'autre ne change.

---

# VOLET A — Les ralentis prennent leur temps

### Task 1 : Fusion des coupes avant un impact

**Files:**
- Modify: `beatsync.py` — `DEFAULT_CONFIG["speed_ramp"]` (~ligne 44), nouvelle fonction après `ramp_speed` (~ligne 134), `build_edl` (après la boucle de `cut_beats`, ~ligne 576)
- Test: `tests/test_speed_ramp.py` (ajouter une section)

**Interfaces:**
- Consumes: `is_impact(beat_index, anchor, impact_beats) -> bool` (existant).
- Produces: `beatsync.merge_boundaries_before_impacts(cut_beats: list[tuple[float, int]], anchor: int, config: dict) -> list[tuple[float, int]]` ; `DEFAULT_CONFIG["speed_ramp"]["slow_beats"]: int`.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à la fin de `tests/test_speed_ramp.py` :

```python
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
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_speed_ramp.py -k "merge or slow_window or slowed_segment" -v`
Expected: FAIL — `ImportError: cannot import name 'merge_boundaries_before_impacts' from 'beatsync'`

- [ ] **Step 3 : Ajouter la clé de config**

Dans `beatsync.py`, `DEFAULT_CONFIG["speed_ramp"]`, après la ligne `"impact_beats": 8,` :

```python
        "slow_beats": 2,                # beats fusionnés avant un impact ; 0 ou 1 = pas de fusion
```

- [ ] **Step 4 : Écrire la fonction pure**

Dans `beatsync.py`, juste après `ramp_speed` :

```python
def merge_boundaries_before_impacts(cut_beats: list[tuple[float, int]], anchor: int,
                                    config: dict) -> list[tuple[float, int]]:
    """Retire les coupes situées dans les `slow_beats` beats qui précèdent un
    impact : les segments concernés fusionnent en un seul, plus long, qui se
    termine sur l'impact et recevra le ralenti. Sans ça le ralenti subit la
    grille de coupe et dure un demi-beat après le drop — invisible.

    Le beat d'impact lui-même n'est jamais retiré (c'est lui qui porte le motif,
    et pour le drop c'est une coupe garantie). Pure, sans RNG."""
    ramp = config.get("speed_ramp") or {}
    slow_beats = int(ramp.get("slow_beats", 0))
    impact_beats = int(ramp.get("impact_beats", 8))
    if slow_beats < 1 or impact_beats <= 0 or not config.get("effects", {}).get("speed"):
        return cut_beats

    def distance_to_next_impact(beat_index: int) -> int:
        return (anchor - beat_index) % impact_beats  # 0 si le beat EST un impact

    # `slow_beats` est la LONGUEUR voulue du segment ralenti : pour qu'il couvre
    # N beats, il faut retirer les N-1 coupes intermédiaires, donc les distances
    # 1..N-1. À 1, rien n'est retiré — c'est la grille actuelle.
    kept = [(t, b) for t, b in cut_beats
            if not (0 < distance_to_next_impact(b) < slow_beats)]
    # Une fenêtre sans aucun impact verrait tout disparaître : on préfère la
    # grille d'origine à un montage d'un seul plan.
    return kept or cut_beats
```

- [ ] **Step 5 : Brancher dans `build_edl`**

Dans `beatsync.py`, `build_edl`, l'ancre des impacts est aujourd'hui calculée **après** les frontières (~ligne 592). Elle doit remonter avant la fusion. Déplacer le bloc :

```python
    # Ancre des impacts : le drop quand il existe, sinon le premier beat de la
    # fenêtre (mode calme) — le motif de vitesse reste actif dans les deux cas.
    impact_anchor = drop_idx if drop_idx is not None else (
        int(in_window[0]) if len(in_window) else 0)
```

juste **après** la boucle `while i <= last:` qui remplit `cut_beats`, et y ajouter à la suite :

```python
    # Les ralentis prennent leur temps : on retire les coupes qui morcelleraient
    # le segment d'anticipation. À faire AVANT la quantification, pour que
    # l'exemption `min_dur` juge la durée fusionnée et non celle d'origine.
    cut_beats = merge_boundaries_before_impacts(cut_beats, impact_anchor, config)
```

Supprimer l'ancien bloc `impact_anchor = …` resté après `drop_out`.

- [ ] **Step 6 : Lancer les tests**

Run: `uv run pytest tests/test_speed_ramp.py -v`
Expected: PASS

- [ ] **Step 7 : Lancer toute la suite**

Run: `uv run pytest -q`
Expected: PASS. Si un test de `tests/test_build_edl*.py` casse, lire son intention avant de le toucher : les tests de cadence de coupe (`test_energy_drives_cut_density`, `test_fixed_mode_cuts_every_n_beats`) utilisent `DEFAULT_CONFIG`, donc `slow_beats: 2` s'y applique. S'ils échouent, la correction est d'ajouter `speed_ramp={**DEFAULT_CONFIG["speed_ramp"], "slow_beats": 0}` à **leur** config — ils portent sur la grille de coupe, pas sur les ramps — et non de changer l'implémentation.

- [ ] **Step 8 : Commit**

```bash
git add beatsync.py tests/test_speed_ramp.py tests/test_build_edl.py tests/test_build_edl_v2.py
git commit -m "feat(montage): les ralentis prennent leur temps

Un ralenti subissait la grille de coupe : après le drop, le strobo coupe
à chaque beat, donc l'effet durait un demi-beat et ne se voyait pas. On
retire les coupes des slow_beats beats précédant un impact, ce qui
fusionne les segments en un seul, plus long, qui porte le ralenti.

Fait avant la quantification pour que l'exemption min_dur juge la durée
fusionnée. slow_beats=0 ou 1 reproduit le comportement précédent."
```

---

# VOLET B — Scène de fin

### Task 2 : Trouver le climax (`find_final_scene`)

**Files:**
- Modify: `beatsync.py` — nouvelles constantes et fonction après `usable_intervals` (~ligne 264)
- Test: `tests/test_end_scene.py` (créer)

**Interfaces:**
- Consumes: la forme des plages produites par `usable_intervals` (`{"start", "end", "motion", "presence"}`) et les clés de scan `clip["dual"]` (tableau numpy de booléens par échantillon) et `clip["scan_dt"]` (float).
- Produces:
  - `beatsync.FINAL_SCENE_TAIL: float` = `1/3` (fraction de queue du clip)
  - `beatsync.interval_dual_ratio(clip: dict, interval: dict) -> float`
  - `beatsync.find_final_scene(clips: list[dict]) -> dict | None` — retourne `{"clip": <dict>, "interval": <dict>}` ou `None`.

- [ ] **Step 1 : Écrire le test qui échoue**

Créer `tests/test_end_scene.py` :

```python
"""Scène de fin : sélection du climax (pure) puis montage dans l'EDL.
Aucun fichier média requis — les clips sont des dicts factices."""

from pathlib import Path

import numpy as np
import pytest

from beatsync import (DEFAULT_CONFIG, build_edl, find_final_scene,
                      interval_dual_ratio)


def scanned_clip(name, duration=100.0, intervals=None, dual_ranges=(), ratio=16 / 9):
    """Clip scanné factice. `dual_ranges` = liste de (début, fin) en secondes
    où le flag duel est vrai ; scan_dt = 0.5 s comme le scan réel (2 fps)."""
    dt = 0.5
    dual = np.zeros(int(duration / dt) + 1, dtype=bool)
    for a, b in dual_ranges:
        dual[int(a / dt):int(b / dt)] = True
    return {
        "path": Path(f"/clips/{name}"),
        "kind": "video",
        "duration": duration,
        "width": 1920, "height": 1080, "ratio": ratio,
        "intervals": intervals or [],
        "interest_x": np.full(len(dual), 0.5),
        "dual": dual,
        "scan_dt": dt,
    }


def iv(start, end, motion=0.5, presence=0.8):
    return {"start": start, "end": end, "motion": motion, "presence": presence}


# --- Fraction de duel d'une plage -------------------------------------------


def test_dual_ratio_counts_only_the_interval_samples():
    clip = scanned_clip("a.mp4", dual_ranges=[(90.0, 100.0)])
    assert interval_dual_ratio(clip, iv(90.0, 100.0)) == pytest.approx(1.0)
    assert interval_dual_ratio(clip, iv(10.0, 20.0)) == pytest.approx(0.0)


def test_dual_ratio_is_zero_without_scan_data():
    clip = scanned_clip("a.mp4")
    del clip["dual"]
    assert interval_dual_ratio(clip, iv(90.0, 100.0)) == pytest.approx(0.0)


# --- Sélection --------------------------------------------------------------


def test_only_the_last_third_is_considered():
    """Une plage du début, même parfaite, n'est jamais la scène de fin."""
    clip = scanned_clip("a.mp4", intervals=[iv(5.0, 15.0, motion=1.0, presence=1.0),
                                            iv(80.0, 90.0, motion=0.1, presence=0.1)],
                        dual_ranges=[(5.0, 15.0)])
    scene = find_final_scene([clip])
    assert scene is not None
    assert scene["interval"]["start"] == pytest.approx(80.0)


def test_duel_wins_at_equal_motion_and_presence():
    duel = scanned_clip("a.mp4", intervals=[iv(80.0, 90.0)], dual_ranges=[(80.0, 90.0)])
    plain = scanned_clip("b.mp4", intervals=[iv(80.0, 90.0)])
    scene = find_final_scene([duel, plain])
    assert scene["clip"]["path"].name == "a.mp4"


def test_motion_and_presence_break_a_tie_without_duel():
    weak = scanned_clip("a.mp4", intervals=[iv(80.0, 90.0, motion=0.1, presence=0.1)])
    strong = scanned_clip("b.mp4", intervals=[iv(80.0, 90.0, motion=1.0, presence=1.0)])
    assert find_final_scene([weak, strong])["clip"]["path"].name == "b.mp4"


def test_images_are_ignored():
    image = {"path": Path("/clips/x.png"), "kind": "image", "duration": None,
             "width": 1920, "height": 1080, "ratio": 16 / 9}
    clip = scanned_clip("a.mp4", intervals=[iv(80.0, 90.0)])
    assert find_final_scene([image, clip])["clip"]["path"].name == "a.mp4"


def test_unscanned_catalog_yields_no_scene():
    """Sans scan, pas de plages : on dégrade en None plutôt que d'inventer."""
    raw = {"path": Path("/clips/a.mp4"), "kind": "video", "duration": 100.0,
           "width": 1920, "height": 1080, "ratio": 16 / 9}
    assert find_final_scene([raw]) is None


def test_no_interval_in_the_last_third_yields_no_scene():
    clip = scanned_clip("a.mp4", intervals=[iv(5.0, 15.0)])
    assert find_final_scene([clip]) is None


def test_empty_catalog_yields_no_scene():
    assert find_final_scene([]) is None


def test_tie_break_is_deterministic():
    """Scores strictement égaux : le clip dont le nom vient en premier, puis
    la plage la plus tardive. Deux appels donnent le même résultat."""
    a = scanned_clip("a.mp4", intervals=[iv(70.0, 80.0), iv(85.0, 95.0)])
    b = scanned_clip("b.mp4", intervals=[iv(70.0, 80.0)])
    first = find_final_scene([a, b])
    assert first["clip"]["path"].name == "a.mp4"
    assert first["interval"]["start"] == pytest.approx(85.0)
    assert find_final_scene([a, b])["interval"] == first["interval"]
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_end_scene.py -v`
Expected: FAIL — `ImportError: cannot import name 'find_final_scene' from 'beatsync'`

- [ ] **Step 3 : Implémenter**

Dans `beatsync.py`, après `usable_intervals` :

```python
# Scène de fin : on ne cherche le climax que dans la queue du clip — en mode
# chrono, la fin de la timeline correspond à la fin de l'histoire.
FINAL_SCENE_TAIL = 1 / 3
# Poids du score : le duel prime (deux personnages face à face = l'affrontement),
# la présence et le mouvement départagent.
FINAL_SCENE_WEIGHTS = {"dual": 1.0, "presence": 0.6, "motion": 0.6}


def interval_dual_ratio(clip: dict, interval: dict) -> float:
    """Fraction d'échantillons « duel » (deux personnages face à face) sur une
    plage. `clip["dual"]` est un tableau par échantillon, pas un agrégat par
    plage : on le découpe sur scan_dt, comme le fait le cadrage. 0.0 si le clip
    n'a pas été scanné."""
    dual = clip.get("dual")
    if dual is None or not len(dual):
        return 0.0
    dt = clip.get("scan_dt") or (1.0 / SCAN_FPS)
    window = np.asarray(dual, dtype=bool)[int(interval["start"] / dt):
                                          math.ceil(interval["end"] / dt)]
    return float(window.mean()) if len(window) else 0.0


def find_final_scene(clips: list[dict]) -> dict | None:
    """Plage la plus « badass » de la queue des clips : le climax de l'histoire.
    Retourne {"clip", "interval"} ou None si rien d'exploitable — l'usine ne
    casse pas sur un catalogue non scanné, elle se termine normalement.

    Pure et sans RNG : à catalogue égal la scène est la même, donc la
    reproductibilité ne dépend pas du tirage seedé."""
    candidates: list[tuple[dict, dict, float, float]] = []
    for clip in clips:
        if clip.get("kind") == "image" or not clip.get("duration"):
            continue
        tail_start = clip["duration"] * (1.0 - FINAL_SCENE_TAIL)
        for interval in clip.get("intervals", []):
            if interval["start"] < tail_start:
                continue
            candidates.append((clip, interval, interval_dual_ratio(clip, interval),
                               float(interval.get("motion", 0.0))))
    if not candidates:
        return None

    # Le mouvement n'est pas borné a priori : on le normalise sur les candidats
    # pour qu'un clip très agité n'écrase pas le critère de présence.
    max_motion = max(motion for _, _, _, motion in candidates) or 1.0
    weights = FINAL_SCENE_WEIGHTS

    def score(clip, interval, dual, motion) -> float:
        return (weights["dual"] * dual
                + weights["presence"] * float(interval.get("presence", 1.0))
                + weights["motion"] * (motion / max_motion))

    # Départage déterministe : meilleur score, puis nom de clip, puis plage la
    # plus tardive.
    best = min(candidates,
               key=lambda c: (-score(*c), str(c[0]["path"].name), -c[1]["start"]))
    return {"clip": best[0], "interval": best[1]}
```

- [ ] **Step 4 : Lancer les tests**

Run: `uv run pytest tests/test_end_scene.py -v && uv run pytest -q`
Expected: PASS partout

- [ ] **Step 5 : Commit**

```bash
git add beatsync.py tests/test_end_scene.py
git commit -m "feat(montage): find_final_scene repère le climax narratif

Note les plages du dernier tiers de chaque clip sur duel + présence +
mouvement, et retourne la meilleure. Le duel pèse le plus : c'est le
signal le plus proche de « l'affrontement final ». Pure et sans RNG,
donc la scène ne dépend pas de la seed. None si rien d'exploitable."
```

---

### Task 3 : Monter la scène de fin dans l'EDL

**Files:**
- Modify: `beatsync.py` — `DEFAULT_CONFIG` (après `speed_ramp`), extraction de `frame_extract` depuis `build_edl` (~lignes 700-718), réservation des derniers beats et branche de la scène dans `build_edl`
- Test: `tests/test_end_scene.py` (ajouter une section)

**Interfaces:**
- Consumes: `find_final_scene(clips) -> dict | None` (Task 2) ; `_clamp_speed(value) -> float` (existant).
- Produces:
  - `beatsync.DEFAULT_CONFIG["end_scene"]` : `{"enabled": bool, "beats": int, "freeze": float, "speed": float}`
  - `beatsync.frame_extract(clip: dict, clip_in: float, source_needed: float, config: dict) -> tuple[float, str]` — retourne `(focus_x, layout)`. La Task 6 modifie ses règles de layout.
  - L'entrée d'EDL de la scène de fin porte `end_scene: True` et `freeze: float`, en plus des clés habituelles. La Task 4 lit `freeze`.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_end_scene.py` :

```python
# --- Montage dans l'EDL -----------------------------------------------------

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


def catalog():
    """Deux clips scannés, exploitables de bout en bout, avec un duel dans la
    queue du second."""
    return [
        scanned_clip("a.mp4", intervals=[iv(1.0, 95.0)]),
        scanned_clip("b.mp4", intervals=[iv(1.0, 40.0), iv(70.0, 95.0)],
                     dual_ranges=[(70.0, 95.0)]),
    ]


def end_cfg(**end_scene):
    return {
        **DEFAULT_CONFIG,
        "start": 0.0, "end": DURATION, "drop_time": 30.0,
        "end_scene": {**DEFAULT_CONFIG["end_scene"], "enabled": True, **end_scene},
    }


def test_end_scene_is_a_single_segment_of_the_configured_length():
    edl = build_edl(make_analysis(), catalog(), end_cfg(beats=8), seed=42)
    final = [e for e in edl if e.get("end_scene")]
    assert len(final) == 1
    assert final[0]["duration"] == pytest.approx(8 * BEAT, abs=BEAT / 2)
    assert final[0] is edl[-1]


def test_end_scene_carries_speed_freeze_and_ramp_slow():
    edl = build_edl(make_analysis(), catalog(), end_cfg(speed=0.5, freeze=1.0), seed=42)
    final = edl[-1]
    assert final["speed"] == pytest.approx(0.5)
    assert final["freeze"] == pytest.approx(1.0)
    assert final["ramp_slow"] is True, "la scène de fin mérite le flux optique"


def test_end_scene_enters_at_the_end_of_the_chosen_interval():
    """Le climax est la conclusion du plan : on cale l'entrée sur la fin de la
    plage, pas sur son début."""
    edl = build_edl(make_analysis(), catalog(), end_cfg(beats=8, freeze=1.0, speed=0.5),
                    seed=42)
    final = edl[-1]
    source = (final["duration"] - final["freeze"]) * final["speed"]
    assert final["clip_in"] + source == pytest.approx(95.0, abs=0.1)


def test_end_scene_picks_the_duel_clip():
    edl = build_edl(make_analysis(), catalog(), end_cfg(), seed=42)
    assert edl[-1]["clip_path"].name == "b.mp4"


def test_disabled_end_scene_changes_nothing():
    analysis = make_analysis()
    off = {**DEFAULT_CONFIG, "start": 0.0, "end": DURATION, "drop_time": 30.0}
    a = build_edl(analysis, catalog(), off, seed=42)
    b = build_edl(analysis, catalog(),
                  {**off, "end_scene": {**DEFAULT_CONFIG["end_scene"], "enabled": False}},
                  seed=42)
    assert a == b
    assert all("end_scene" not in e for e in a)


def test_no_scene_found_falls_back_to_a_normal_montage():
    """Catalogue non scanné : find_final_scene rend None, le montage se termine
    normalement au lieu de casser."""
    raw = [{"path": Path("/clips/a.mp4"), "kind": "video", "duration": 100.0,
            "width": 1920, "height": 1080, "ratio": 16 / 9}]
    edl = build_edl(make_analysis(), raw, end_cfg(), seed=42)
    assert edl
    assert all(not e.get("end_scene") for e in edl)


def test_end_scene_is_reproducible():
    a = build_edl(make_analysis(), catalog(), end_cfg(), seed=7)
    b = build_edl(make_analysis(), catalog(), end_cfg(), seed=7)
    assert a == b
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_end_scene.py -k end_scene -v`
Expected: FAIL — `KeyError: 'end_scene'` sur `DEFAULT_CONFIG["end_scene"]`

- [ ] **Step 3 : Ajouter le bloc de config**

Dans `beatsync.py`, `DEFAULT_CONFIG`, juste après le bloc `speed_ramp` :

```python
    "end_scene": {                      # conclusion du montage : ralenti long figé
        "enabled": False,               # opt-in : aucun preset existant ne change
        "beats": 8,                     # durée totale de la scène, en beats
        "freeze": 1.0,                  # s de figé à la toute fin
        "speed": 0.5,
    },
```

- [ ] **Step 4 : Extraire le cadrage en fonction pure**

Le bloc de cadrage est aujourd'hui inline dans `build_edl` (« Cadrage : centre d'intérêt et layout, moyennés sur l'extrait choisi »). Deux appelants en ont désormais besoin. L'extraire tel quel, **sans changer sa logique**, juste avant `build_edl` :

```python
def frame_extract(clip: dict, clip_in: float, source_needed: float,
                  config: dict) -> tuple[float, str]:
    """Cadrage d'un extrait : centre d'intérêt horizontal et layout, moyennés
    sur la fenêtre consommée. Pure. Sans données de scan, on centre.

    Au moins 3 échantillons (1,5 s) : un extrait d'un beat n'en couvre parfois
    qu'un seul, trop peu pour juger dispersion et duel."""
    if "interest_x" not in clip:
        return 0.5, "crop"
    dt = clip.get("scan_dt", 1.0 / SCAN_FPS)
    i0 = int(clip_in / dt)
    i1 = max(i0 + 3, math.ceil((clip_in + source_needed) / dt))
    window_x = np.asarray(clip["interest_x"], dtype=float)[i0:i1]
    if not len(window_x):
        return 0.5, "crop"
    focus_x = float(np.clip(window_x.mean(), 0.0, 1.0))
    dual = np.asarray(clip.get("dual", []), dtype=bool)[i0:i1]
    if len(dual) and float(dual.mean()) >= 0.5:
        return focus_x, "split"   # duel : deux moitiés empilées haut/bas
    if float(window_x.std()) >= 0.18:
        return focus_x, "blur"    # action sur toute la largeur : fond flouté
    return focus_x, "crop"
```

Dans `build_edl`, remplacer le bloc inline par :

```python
        focus_x, layout = frame_extract(clip, clip_in, source_needed, config)
```

- [ ] **Step 5 : Réserver les derniers beats**

Dans `build_edl`, juste après le calcul de `drop_out` et avant `intervals_of` :

```python
    # --- Scène de fin : un seul segment sur les N derniers beats -------------
    es_cfg = config.get("end_scene") or {}
    end_scene, es_start = None, None
    if es_cfg.get("enabled"):
        scene = find_final_scene(clips)
        if scene is not None and len(beats) >= 2:
            beat_dur = float(np.median(np.diff(beats)))
            raw_start = out_end - int(es_cfg.get("beats", 8)) * beat_dur
            candidate = round(max(0.0, raw_start) * fps) / fps
            # Une scène qui avalerait toute la fenêtre n'est plus une conclusion.
            if candidate >= frame and candidate <= out_end - frame:
                end_scene, es_start = scene, candidate
                # On POSE la frontière : sans elle le segment final commencerait
                # à la dernière coupe existante, d'une durée arbitraire.
                boundaries = [b for b in boundaries if b[0] < es_start - 1e-9]
                boundaries.append((es_start, -1))
                boundaries.append((out_end, -1))
```

- [ ] **Step 6 : Monter la scène**

Dans la boucle de segments de `build_edl`, juste après le calcul de `speed, ramp_slow = _ramp_decision(...)`, insérer :

```python
        if end_scene is not None and seg_start >= es_start - 1e-9:
            # Conclusion : ralenti long sur le climax, figé sur la dernière
            # image. Pas de tirage — la scène a été choisie hors du rng.
            clip = end_scene["clip"]
            interval = end_scene["interval"]
            es_speed = _clamp_speed(es_cfg.get("speed", 0.5))
            freeze = max(0.0, min(duration, float(es_cfg.get("freeze", 1.0))))
            es_source = (duration - freeze) * es_speed
            # Le climax est la conclusion du plan : on cale l'entrée sur sa fin.
            clip_in = max(interval["start"], interval["end"] - es_source)
            focus_x, layout = frame_extract(clip, clip_in, es_source, config)
            edl.append(
                {
                    "timeline_start": seg_start,
                    "duration": duration,
                    "clip_path": clip["path"],
                    "kind": "video",
                    "clip_in": clip_in,
                    "beat_index": beat_index,
                    "section": section,
                    "speed": es_speed,
                    "ramp_slow": True,   # ralenti voulu : il mérite le flux optique
                    "end_scene": True,
                    "freeze": freeze,
                    "effects": [],       # la scène se suffit ; pas de shake ni de glitch
                    "focus_x": focus_x,
                    "layout": layout,
                    "clip_w": clip["width"],
                    "clip_h": clip["height"],
                }
            )
            continue
```

- [ ] **Step 7 : Lancer les tests**

Run: `uv run pytest tests/test_end_scene.py -v && uv run pytest -q`
Expected: PASS partout. Les tests de cadrage existants (`tests/test_framing.py`) doivent rester verts : l'extraction du Step 4 est un refactor pur, sans changement de logique.

- [ ] **Step 8 : Commit**

```bash
git add beatsync.py tests/test_end_scene.py
git commit -m "feat(montage): option scène de fin

Réserve les N derniers beats de la fenêtre à un segment unique portant le
climax trouvé par find_final_scene, au ralenti, avec un figé sur la
dernière image. La frontière est POSÉE à N beats de la fin : sans ça le
segment aurait hérité d'une durée arbitraire de la coupe précédente.

Le bloc de cadrage devient frame_extract (pure) : la scène de fin et le
chemin ordinaire en ont tous deux besoin."
```

---

### Task 4 : Le figé au rendu

**Files:**
- Modify: `beatsync.py` — `_segment_input_args` (~ligne 1058), le `tpad` de `_segment_filters` (~ligne 1181)
- Test: `tests/test_end_scene.py` (ajouter une section)

**Interfaces:**
- Consumes: `entry["freeze"]` et `entry["end_scene"]` (Task 3).
- Produces: rien de nouveau — les deux fonctions gardent leur signature.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_end_scene.py` :

```python
# --- Rendu du figé ----------------------------------------------------------

from beatsync import _segment_filters, _segment_input_args  # noqa: E402


def final_entry(**overrides):
    return {"timeline_start": 0.0, "duration": 4.0, "clip_path": Path("/clips/b.mp4"),
            "kind": "video", "clip_in": 10.0, "speed": 0.5, "ramp_slow": True,
            "end_scene": True, "freeze": 1.0, "effects": [], "layout": "crop",
            "focus_x": 0.5, "clip_w": 1920, "clip_h": 1080, **overrides}


def test_freeze_shortens_the_source_consumed():
    """4 s de timeline dont 1 s figée, à 0.5x → (4-1)*0.5 = 1.5 s de source.
    tpad clone la dernière image pendant la seconde restante."""
    args = _segment_input_args(final_entry())
    assert float(args[3]) == pytest.approx(1.5 + 0.5)  # + le rab de seek


def test_no_freeze_leaves_the_source_untouched():
    args = _segment_input_args(final_entry(freeze=0.0))
    assert float(args[3]) == pytest.approx(4.0 * 0.5 + 0.5)


def test_ordinary_entries_are_unaffected():
    entry = final_entry()
    del entry["freeze"]
    del entry["end_scene"]
    args = _segment_input_args(entry)
    assert float(args[3]) == pytest.approx(4.0 * 0.5 + 0.5)


def test_tpad_gets_room_for_the_freeze():
    joined = " ".join(_segment_filters(final_entry(freeze=1.5), DEFAULT_CONFIG))
    assert "tpad=stop_mode=clone:stop_duration=2.5" in joined


def test_tpad_default_is_unchanged_without_freeze():
    entry = final_entry()
    del entry["freeze"]
    joined = " ".join(_segment_filters(entry, DEFAULT_CONFIG))
    assert "tpad=stop_mode=clone:stop_duration=1" in joined
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_end_scene.py -k "freeze or tpad" -v`
Expected: FAIL — la source consommée vaut encore `4.0 * 0.5 + 0.5`

- [ ] **Step 3 : Retirer le figé de la source consommée**

Dans `beatsync.py`, `_segment_input_args`, remplacer le calcul :

```python
    source_needed = entry["duration"] * entry.get("speed", 1.0)
```

par :

```python
    # Le figé de fin ne consomme pas de source : `tpad=stop_mode=clone` clone la
    # dernière image et `-frames:v` garde le compte exact. Pas de filtre dédié.
    freeze = float(entry.get("freeze", 0.0))
    source_needed = max(0.0, entry["duration"] - freeze) * entry.get("speed", 1.0)
```

- [ ] **Step 4 : Donner à `tpad` la marge du figé**

Dans `_segment_filters`, remplacer :

```python
    post.append("tpad=stop_mode=clone:stop_duration=1")
```

par :

```python
    # 1 s de marge pour l'imprécision de seek, plus la durée du figé de fin.
    freeze = float(entry.get("freeze", 0.0))
    post.append(f"tpad=stop_mode=clone:stop_duration={1 + freeze:g}")
```

- [ ] **Step 5 : Lancer les tests**

Run: `uv run pytest tests/test_end_scene.py -v && uv run pytest -q`
Expected: PASS partout

- [ ] **Step 6 : Vérifier sur un rendu réel**

Le figé est exactement le genre d'effet qu'un test de chaîne de caractères ne prouve pas. Construire une EDL synthétique de deux segments dont le dernier porte `end_scene: True`, `freeze: 1.0`, `speed: 0.5`, appeler `render()` dessus avec un clip réel de `clips/`, puis vérifier objectivement :

```bash
ffprobe -v error -count_frames -select_streams v:0 \
  -show_entries stream=nb_read_frames -of csv=p=0 /tmp/freeze-test.mp4
```

Expected: le compte de frames correspond à `durée_totale × fps`. Extraire ensuite deux frames de la dernière seconde et confirmer qu'elles sont **identiques** (md5 égaux) — c'est ça, le figé — alors que deux frames prises avant le figé diffèrent. Consigner les md5 dans le rapport.

- [ ] **Step 7 : Commit**

```bash
git add beatsync.py tests/test_end_scene.py
git commit -m "feat(rendu): figé de fin sans filtre supplémentaire

On consomme moins de source (duration - freeze) et tpad=stop_mode=clone
cloner la dernière image pour combler. -frames:v garde le compte exact,
donc l'invariant de dérive audio/vidéo tient tel quel."
```

---

# VOLET C — Format vertical ou carré

### Task 5 : Le champ `format`

**Files:**
- Modify: `beatsync.py` — `DEFAULT_CONFIG` (~ligne 27), nouvelle constante et fonction après `merge_settings` (~ligne 90), `generate_video` (~ligne 1020), `main()` (argparse + application)
- Test: `tests/test_format.py` (créer)

**Interfaces:**
- Consumes: rien.
- Produces:
  - `beatsync.FORMATS: dict[str, tuple[int, int]]` = `{"vertical": (1080, 1920), "carre": (1080, 1080)}`
  - `beatsync.apply_format(config: dict) -> dict` — retourne une **nouvelle** config avec `width`/`height` posés d'après `format`. La Task 6 lit `config["width"]` / `config["height"]`.
  - `DEFAULT_CONFIG["format"] = "vertical"`

- [ ] **Step 1 : Écrire le test qui échoue**

Créer `tests/test_format.py` :

```python
"""Format de sortie : dérivation des dimensions et cadrage sensible au ratio."""

import pytest

from beatsync import DEFAULT_CONFIG, FORMATS, apply_format


def test_vertical_is_the_default():
    assert DEFAULT_CONFIG["format"] == "vertical"
    out = apply_format(dict(DEFAULT_CONFIG))
    assert (out["width"], out["height"]) == (1080, 1920)


def test_square_gives_square_dimensions():
    out = apply_format({**DEFAULT_CONFIG, "format": "carre"})
    assert (out["width"], out["height"]) == (1080, 1080)


def test_unknown_format_degrades_to_vertical():
    """Dégradation sûre, comme section et subtitles.mode."""
    out = apply_format({**DEFAULT_CONFIG, "format": "n'importe quoi"})
    assert (out["width"], out["height"]) == (1080, 1920)


def test_input_config_is_not_mutated():
    config = {**DEFAULT_CONFIG, "format": "carre"}
    apply_format(config)
    assert config["height"] == 1920


def test_nested_dicts_are_copied_not_shared():
    """Sans copie des dicts imbriqués, modifier la config de sortie polluerait
    celle de l'appelant — et donc la variante suivante du lot."""
    config = {**DEFAULT_CONFIG, "format": "carre", "effects": {"zoom": True}}
    out = apply_format(config)
    out["effects"]["zoom"] = False
    assert config["effects"]["zoom"] is True


def test_every_format_has_even_dimensions():
    """H.264 refuse les dimensions impaires."""
    for width, height in FORMATS.values():
        assert width % 2 == 0 and height % 2 == 0
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_format.py -v`
Expected: FAIL — `ImportError: cannot import name 'FORMATS' from 'beatsync'`

- [ ] **Step 3 : Ajouter le champ et la fonction**

Dans `beatsync.py`, `DEFAULT_CONFIG`, juste avant `"width": 1080,` :

```python
    "format": "vertical",               # "vertical" = 1080x1920 | "carre" = 1080x1080
```

(`width` et `height` restent dans `DEFAULT_CONFIG` : ce sont eux que lit le rendu, `format` est seulement ce qui les pose.)

Après `merge_settings` :

```python
# Formats de sortie. Le carré recadre beaucoup moins un rush 16:9 (44 % de la
# largeur jetée contre 68 % en vertical), ce qui sert notamment l'animé.
FORMATS = {"vertical": (1080, 1920), "carre": (1080, 1080)}


def apply_format(config: dict) -> dict:
    """Pose `width`/`height` d'après `format`, sans muter l'entrée. Un format
    inconnu retombe sur vertical — dégradation sûre, comme `section`."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in config.items()}
    out["width"], out["height"] = FORMATS.get(out.get("format", "vertical"),
                                              FORMATS["vertical"])
    return out
```

- [ ] **Step 4 : Brancher dans `generate_video`**

Dans `generate_video`, remplacer :

```python
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in config.items()}
```

par :

```python
    cfg = apply_format(config)   # copie interne + dimensions dérivées du format
```

- [ ] **Step 5 : Ajouter l'option CLI**

Dans `main()`, après l'argument `--section` :

```python
    parser.add_argument("--format", choices=["vertical", "carre"], default=None,
                        help="format de sortie : vertical 9:16 (défaut) ou carré 1:1")
```

Puis, dans le bloc qui applique les arguments à la config :

```python
    if args.format is not None:
        config["format"] = args.format
```

Et corriger la description du parser, qui annonce un format unique :

```python
        description="Montage vidéo 9:16 ou 1:1 synchronisé sur les beats d'un morceau."
```

- [ ] **Step 6 : Lancer les tests**

Run: `uv run pytest tests/test_format.py -v && uv run pytest -q`
Expected: PASS partout

- [ ] **Step 7 : Vérifier sur un rendu réel**

Run: `uv run python beatsync.py <un morceau de tracks/> clips/ --duration 6 --format carre --output /tmp/carre-test.mp4`
Puis :
```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 /tmp/carre-test.mp4
```
Expected: `1080,1080`. Refaire avec `--format vertical` → `1080,1920`.

- [ ] **Step 8 : Commit**

```bash
git add beatsync.py tests/test_format.py
git commit -m "feat(rendu): format de sortie vertical ou carré

Un champ `format` pose width/height dans generate_video, le point de
passage unique du CLI et de l'usine par niche. Le 9:16 jette 68 % de la
largeur d'un rush 16:9, le 1:1 seulement 44 % — ce qui sert l'animé.
Format inconnu = vertical, comme section retombe sur drop."
```

---

### Task 6 : Le cadrage suit le format

**Files:**
- Modify: `beatsync.py` — `frame_extract` (créée en Task 3), la ligne de layout de la branche image de `build_edl`
- Test: `tests/test_format.py` (ajouter une section)

**Interfaces:**
- Consumes: `frame_extract(clip, clip_in, source_needed, config) -> tuple[float, str]` (Task 3) ; `config["width"]`, `config["height"]` (Task 5).
- Produces: rien de nouveau — mêmes signatures, règles internes changées.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_format.py` :

```python
# --- Cadrage sensible au format ---------------------------------------------

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

from beatsync import frame_extract  # noqa: E402


def duel_clip(ratio=16 / 9):
    """Clip dont la fenêtre de scan est un duel franc (visages aux deux bords)."""
    n = 40
    return {"path": Path("/clips/a.mp4"), "kind": "video", "duration": 100.0,
            "width": 1920, "height": 1080, "ratio": ratio,
            "interest_x": np.full(n, 0.5), "dual": np.ones(n, dtype=bool),
            "scan_dt": 0.5}


def spread_clip(ratio=16 / 9):
    """Action sur toute la largeur : forte dispersion, pas de duel."""
    n = 40
    return {"path": Path("/clips/b.mp4"), "kind": "video", "duration": 100.0,
            "width": 1920, "height": 1080, "ratio": ratio,
            "interest_x": np.tile([0.1, 0.9], n // 2), "dual": np.zeros(n, dtype=bool),
            "scan_dt": 0.5}


def cfg(fmt):
    return apply_format({**DEFAULT_CONFIG, "format": fmt})


def test_duel_splits_in_vertical():
    _, layout = frame_extract(duel_clip(), 1.0, 2.0, cfg("vertical"))
    assert layout == "split"


def test_duel_is_cropped_in_square():
    """En 1:1 l'empilement donnerait deux bandes 2:1 ; le crop tient déjà les
    deux personnages."""
    _, layout = frame_extract(duel_clip(), 1.0, 2.0, cfg("carre"))
    assert layout == "crop"


def test_wide_source_gets_a_blurred_background_in_vertical():
    _, layout = frame_extract(spread_clip(16 / 9), 1.0, 2.0, cfg("vertical"))
    assert layout == "blur"


def test_sixteen_nine_is_cropped_in_square():
    """Seuil en carré : ratio >= 2.0 ; un 16:9 (1.78) passe donc en crop."""
    _, layout = frame_extract(spread_clip(16 / 9), 1.0, 2.0, cfg("carre"))
    assert layout == "crop"


def test_scope_source_still_blurs_in_square():
    _, layout = frame_extract(spread_clip(2.35), 1.0, 2.0, cfg("carre"))
    assert layout == "blur"


def test_focus_x_is_unchanged_by_the_format():
    fx_v, _ = frame_extract(spread_clip(), 1.0, 2.0, cfg("vertical"))
    fx_c, _ = frame_extract(spread_clip(), 1.0, 2.0, cfg("carre"))
    assert fx_v == pytest.approx(fx_c)


# --- Images : même règle que les vidéos -------------------------------------

from beatsync import build_edl  # noqa: E402


def image_clip(ratio):
    width = int(1080 * ratio)
    return {"path": Path("/clips/x.png"), "kind": "image", "duration": None,
            "width": width, "height": 1080, "ratio": ratio}


def test_image_layout_follows_the_same_rule_as_videos():
    """Effet de bord assumé du volet C : en vertical le seuil des images passe
    de 1.2 à 1.125 (= 2.0 x 0.5625), donc un 4:3 (1.33) gagne un fond flouté."""
    from tests.test_images import make_analysis, video

    clips = [video("a.mp4"), image_clip(4 / 3)]
    config = apply_format({**DEFAULT_CONFIG, "format": "vertical",
                           "start": 0.0, "end": 60.0, "drop_time": 30.0})
    edl = build_edl(make_analysis(), clips, config, seed=42)
    images = [e for e in edl if e.get("kind") == "image"]
    assert images and all(e["layout"] == "blur" for e in images)

    square = apply_format({**DEFAULT_CONFIG, "format": "carre",
                           "start": 0.0, "end": 60.0, "drop_time": 30.0})
    edl = build_edl(make_analysis(), clips, square, seed=42)
    images = [e for e in edl if e.get("kind") == "image"]
    assert images and all(e["layout"] == "crop" for e in images)
```

Si l'import `from tests.test_images import …` échoue (pas de `__init__.py` dans
`tests/`), utiliser `from test_images import make_analysis, video` — pytest ajoute
le dossier des tests au `sys.path`.

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_format.py -k "duel or square or scope or image_layout" -v`
Expected: FAIL — `assert 'split' == 'crop'` sur le duel en carré

- [ ] **Step 3 : Rendre `frame_extract` sensible au ratio de sortie**

Dans `frame_extract`, remplacer le bloc de décision de layout :

```python
    # Les deux cadrages de secours dépendent du format de sortie. En 9:16 un
    # duel empilé fonctionne ; en 1:1 chaque moitié deviendrait une bande 2:1,
    # et le crop tient déjà les deux personnages.
    out_ratio = float(config["width"]) / float(config["height"])
    dual = np.asarray(clip.get("dual", []), dtype=bool)[i0:i1]
    if len(dual) and float(dual.mean()) >= 0.5 and out_ratio <= 0.75:
        return focus_x, "split"
    # Fond flouté seulement si la source est vraiment plus large que la sortie :
    # seuil 1,125 en vertical (tout 16:9 y a droit), 2,0 en carré (seul un scope).
    if float(window_x.std()) >= 0.18 and clip.get("ratio", 1.0) >= 2.0 * out_ratio:
        return focus_x, "blur"
    return focus_x, "crop"
```

- [ ] **Step 4 : Aligner la branche image**

Dans `build_edl`, la branche image, remplacer :

```python
                    "layout": "blur" if clip["ratio"] >= 1.2 else "crop",
```

par :

```python
                    # Même règle que les vidéos : le scan n'a pas tourné, mais le
                    # rapport source/sortie décide pareil.
                    "layout": ("blur"
                               if clip["ratio"] >= 2.0 * (config["width"] / config["height"])
                               else "crop"),
```

- [ ] **Step 5 : Lancer les tests**

Run: `uv run pytest tests/test_format.py -v && uv run pytest -q`
Expected: PASS. Si `tests/test_images.py::test_wide_image_falls_back_to_the_blurred_background_layout` ou `test_portrait_image_is_cropped` échouent, vérifier les ratios qu'ils utilisent : 1920×1080 (1,78) reste `blur` en vertical, 1080×1920 (0,5625) reste `crop`. Les deux doivent passer sans modification — si ce n'est pas le cas, c'est l'implémentation qu'il faut corriger.

- [ ] **Step 6 : Commit**

```bash
git add beatsync.py tests/test_format.py
git commit -m "feat(cadrage): les layouts de secours suivent le format

Le split (duel empilé) demande une sortie nettement plus haute que large
(ratio <= 0.75) : vrai en vertical, faux en carré. Le blur demande une
source au moins 2x plus large que la sortie — seuil 1,125 en vertical
(comportement inchangé), 2,0 en carré.

Le seuil des images, codé en dur à 1.2 pour le 9:16, rejoint la règle."
```

---

### Task 7 : Validation serveur et interface

**Files:**
- Modify: `webui.py` — constantes (~lignes 32-45), `coerce_overrides` (~ligne 55)
- Modify: `frontend/src/lib/api.ts` — type `Overrides`
- Modify: `frontend/src/features/presets/PresetEditor.tsx` — constantes, state, `buildOverrides`, rendu
- Test: `tests/test_webui_platform.py` (ajouter)

**Interfaces:**
- Consumes: `format` et `end_scene` tels que définis en Tasks 3 et 5.
- Produces: `webui.ALLOWED_FORMATS = ("vertical", "carre")` ; `coerce_overrides` borne `end_scene.beats` dans `[2, 32]`, `end_scene.freeze` dans `[0.0, 3.0]`, `end_scene.speed` dans `[0.5, 1.5]`, et refuse un `format` inconnu.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_webui_platform.py` :

```python
def test_coerce_overrides_accepts_known_formats():
    for fmt in ("vertical", "carre"):
        assert coerce_overrides({"format": fmt})["format"] == fmt


def test_coerce_overrides_rejects_unknown_format():
    with pytest.raises(ValueError):
        coerce_overrides({"format": "panoramique"})


def test_coerce_overrides_clamps_end_scene_values():
    out = coerce_overrides({"end_scene": {"enabled": True, "beats": "64",
                                          "freeze": "9.0", "speed": "0.1"}})
    assert out["end_scene"]["beats"] == 32
    assert out["end_scene"]["freeze"] == pytest.approx(3.0)
    assert out["end_scene"]["speed"] == pytest.approx(0.5)
    assert out["end_scene"]["enabled"] is True


def test_coerce_overrides_rejects_non_numeric_end_scene():
    with pytest.raises(ValueError):
        coerce_overrides({"end_scene": {"beats": "beaucoup"}})


def test_coerce_overrides_leaves_end_scene_absent_alone():
    assert "end_scene" not in coerce_overrides({"grain": 0.2})


def test_preset_with_format_and_end_scene_round_trips(client):
    created = client.post("/api/presets", json={
        "name": "Carré cinéma",
        "overrides": {"format": "carre",
                      "end_scene": {"enabled": True, "beats": 8,
                                    "freeze": 1.0, "speed": 0.5}}})
    assert created.status_code == 200
    presets = client.get("/api/state").get_json()["presets"]
    saved = next(p for p in presets if p["name"] == "Carré cinéma")
    assert saved["overrides"]["format"] == "carre"
    assert saved["overrides"]["end_scene"]["beats"] == 8


def test_preset_with_unknown_format_is_rejected(client):
    bad = client.post("/api/presets", json={
        "name": "Cassé", "overrides": {"format": "panoramique"}})
    assert bad.status_code == 400
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_webui_platform.py -k "format or end_scene" -v`
Expected: FAIL — `coerce_overrides({"format": "panoramique"})` ne lève pas

- [ ] **Step 3 : Implémenter la coercition**

Dans `webui.py`, à côté de `ALLOWED_COLOR_GRADES` :

```python
ALLOWED_FORMATS = ("vertical", "carre")
# Bornes des champs de la scène de fin : au-delà, la scène avale la vidéo ou
# le figé dépasse le segment.
END_SCENE_RANGES = {"beats": (2, 32), "freeze": (0.0, 3.0), "speed": (0.5, 1.5)}
```

Dans `coerce_overrides`, avant le `return coerced` :

```python
    if "format" in coerced and coerced["format"] not in ALLOWED_FORMATS:
        raise ValueError(f"format inconnu : {coerced['format']!r}")
    end_scene = coerced.get("end_scene")
    if isinstance(end_scene, dict):
        end_scene = dict(end_scene)
        for key, (lo, hi) in END_SCENE_RANGES.items():
            if key in end_scene:
                value = end_scene[key]
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    value = float(value)
                end_scene[key] = max(lo, min(hi, value))
        if "beats" in end_scene:
            end_scene["beats"] = int(end_scene["beats"])
        coerced["end_scene"] = end_scene
```

- [ ] **Step 4 : Étendre le type TypeScript**

Dans `frontend/src/lib/api.ts`, ajouter au type `Overrides` :

```ts
  format?: "vertical" | "carre"
  end_scene?: { enabled?: boolean; beats?: number; freeze?: number; speed?: number }
```

- [ ] **Step 5 : Ajouter les contrôles à l'éditeur de preset**

Dans `PresetEditor.tsx`, à côté des autres listes de constantes :

```tsx
const OUTPUT_FORMATS = [
  { value: "vertical", label: "Vertical 9:16" },
  { value: "carre", label: "Carré 1:1" },
] as const
```

Dans le state, à côté de `const [section, setSection] = …` :

```tsx
  const [format, setFormat] = useState(o.format ?? "vertical")
  const [endScene, setEndScene] = useState(o.end_scene?.enabled ?? false)
  const [endBeats, setEndBeats] = useState(o.end_scene?.beats ?? 8)
  const [endFreeze, setEndFreeze] = useState(o.end_scene?.freeze ?? 1)
  const [endSpeed, setEndSpeed] = useState(o.end_scene?.speed ?? 0.5)
```

Dans `buildOverrides`, ajouter les deux entrées :

```tsx
    format,
    end_scene: { enabled: endScene, beats: endBeats, freeze: endFreeze, speed: endSpeed },
```

(`dirty` est calculé à partir de `buildOverrides` via le `snapshot` : les cinq
valeurs y entrent donc automatiquement, sans ligne supplémentaire.)

Dans le rendu, à côté du `Select` « Ambiance couleur » :

```tsx
          <div className="grid gap-1.5">
            <Label>Format de sortie</Label>
            <Select value={format} onValueChange={setFormat}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {OUTPUT_FORMATS.map((f) => (
                  <SelectItem key={f.value} value={f.value}>
                    {f.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Le carré recadre beaucoup moins un rush 16:9 — souvent meilleur pour l'animé.
            </p>
          </div>
```

Et, après le dernier `NumberField` existant :

```tsx
          <div className="grid gap-3 border-t pt-4">
            <Label>Scène de fin</Label>
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={endScene}
                onCheckedChange={(v) => setEndScene(v === true)}
              />
              Terminer par un ralenti figé sur le climax
            </label>
            <p className="text-xs text-muted-foreground">
              Cherche le plan le plus intense de la fin de chaque clip (duel, personnages,
              mouvement) et le monte au ralenti, figé sur la dernière image. Prend son sens
              avec le mode chronologique.
            </p>
            {endScene && (
              <div className="grid gap-3 sm:grid-cols-3">
                <NumberField id="end-beats" label="Durée (beats)" value={endBeats}
                  onChange={setEndBeats} step={1} min={2} max={32} />
                <NumberField id="end-freeze" label="Figé (s)" value={endFreeze}
                  onChange={setEndFreeze} step={0.5} min={0} max={3} />
                <NumberField id="end-speed" label="Vitesse" value={endSpeed}
                  onChange={setEndSpeed} step={0.05} min={0.5} max={1.5} />
              </div>
            )}
          </div>
```

- [ ] **Step 6 : Lancer les tests et le build**

Run: `uv run pytest -q && cd frontend && npm run build`
Expected: PASS et build sans erreur TypeScript

- [ ] **Step 7 : Commit**

```bash
git add webui.py frontend/src/lib/api.ts frontend/src/features/presets/PresetEditor.tsx tests/test_webui_platform.py
git commit -m "feat(ui): format de sortie et scène de fin dans les presets

coerce_overrides refuse un format inconnu et borne les trois valeurs de
end_scene (une scène de 64 beats avalerait la vidéo). L'éditeur de preset
expose le choix vertical/carré et la scène de fin ; les valeurs entrent
dans le snapshot dirty par buildOverrides, sans ligne dédiée."
```

---

### Task 8 : Documentation

**Files:**
- Modify: `CLAUDE.md` — puces `build_edl`, `render`, `webui.py`

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: rien.

- [ ] **Step 1 : Vérifier chaque nom dans le code**

Avant d'écrire quoi que ce soit, relire dans `beatsync.py` les noms suivants et
confirmer leur orthographe exacte : `merge_boundaries_before_impacts`,
`find_final_scene`, `interval_dual_ratio`, `frame_extract`, `apply_format`,
`FORMATS`, `FINAL_SCENE_TAIL`, `speed_ramp.slow_beats`, `end_scene`. Une doc qui
ment est pire qu'une doc absente.

- [ ] **Step 2 : Mettre à jour la puce `build_edl`**

Ajouter à la puce `build_edl` de `CLAUDE.md`, dans le style dense des puces voisines :

> `speed_ramp.slow_beats` : les coupes des N beats précédant un impact sont retirées (`merge_boundaries_before_impacts`, **pure et testée**) pour que le segment ralenti dure assez longtemps pour se voir — sinon le strobo le réduit à un demi-beat. Fait avant la quantification, pour que l'exemption `min_dur` juge la durée fusionnée.
>
> `end_scene` (opt-in) : les N derniers beats de la fenêtre sont réservés à un segment unique portant le climax narratif. `find_final_scene` (**pure**) note les plages du dernier tiers de chaque clip sur duel (`interval_dual_ratio`) + présence + mouvement ; elle retourne `None` si rien n'est exploitable, et le montage se termine alors normalement. L'entrée porte `end_scene: True`, `ramp_slow: True` et `freeze`.
>
> `frame_extract` (**pure**) : cadrage d'un extrait (centre d'intérêt + layout), partagé par le chemin ordinaire et la scène de fin. Les deux layouts de secours dépendent du **format de sortie** : `split` seulement si `width/height <= 0.75`, `blur` seulement si `ratio_clip >= 2 × (width/height)` — soit 1,125 en vertical (tout 16:9) et 2,0 en carré (un scope seulement). Les images suivent la même règle.

- [ ] **Step 3 : Mettre à jour la puce `render`**

Ajouter :

> Figé de fin : une entrée portant `freeze` consomme `(duration − freeze) × speed` de source (`_segment_input_args`) et `tpad=stop_mode=clone:stop_duration=1+freeze` clone la dernière image pour combler. Aucun filtre dédié, et `-frames:v` garde le compte exact.

- [ ] **Step 4 : Documenter le format**

Dans la puce `beatsync.py` (ou celle de `main()`), ajouter :

> `format` (`"vertical"` 1080×1920 | `"carre"` 1080×1080) pose `width`/`height` via `apply_format`, appelée en tête de `generate_video` — le point de passage unique du CLI et de l'usine par niche. Format inconnu = vertical. CLI : `--format`.

Dans la puce `webui.py`, préciser que l'éditeur de preset expose le format de
sortie et la scène de fin, et que `coerce_overrides` refuse un format inconnu et
borne `end_scene`.

- [ ] **Step 5 : Contrôle final**

Run: `uv run pytest -q`
Expected: PASS (aucun changement de code)

- [ ] **Step 6 : Commit**

```bash
git add CLAUDE.md
git commit -m "docs: ralentis allongés, scène de fin et format carré"
```

---

## Vérification finale

- [ ] `uv run pytest -q` — toute la suite passe
- [ ] `cd frontend && npm run build` — build sans erreur
- [ ] Un rendu réel en **carré** avec la scène de fin activée et `slow_beats: 2` : la vidéo fait 1080×1080, se termine par un ralenti long figé, et aucun duel n'est empilé
- [ ] Le même rendu en **vertical** pour comparer côte à côte — c'est le jugement à l'œil qui décidera des valeurs par défaut de `slow_beats` et du poids du duel
- [ ] Mesurer le temps de génération avec `slow_beats: 2` contre `slow_beats: 0`, `interpolate` actif : les segments ralentis étant deux fois plus longs, le surcoût du flux optique augmente d'autant. Consigner l'écart — c'est lui qui dira si `interpolate` reste activable par défaut sur la tour.
