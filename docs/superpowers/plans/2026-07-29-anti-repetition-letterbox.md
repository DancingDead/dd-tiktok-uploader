# Anti-répétition et rognage des bandes noires — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Empêcher le montage de rejouer un passage déjà montré, et retirer les bandes noires des sources letterboxées avant tout cadrage.

**Architecture:** Deux volets indépendants sur les points d'extension existants de `beatsync.py`. Le volet A ajoute une fonction pure `free_windows` et une mémoire des portions consommées dans `build_edl` ; les trois points de décision qui choisissaient en aveugle (clips utilisables, mapping chrono, tirage libre) consomment tous la même fonction. Le volet B ajoute une fonction pure `content_rect` alimentée par les frames que le scan décode déjà, stocke un rectangle en fractions sur le clip, et rogne en tête de chaîne de filtres.

**Tech Stack:** Python 3 + uv, numpy, FFmpeg/ffprobe par `subprocess`, pytest.

**Spec:** `docs/superpowers/specs/2026-07-29-anti-repetition-letterbox-design.md`

**Branche :** `feat/anti-repetition-letterbox` (déjà créée, le spec y est commité). Partie de `8aa22d8` sur `master`.

## Global Constraints

- Commandes : `uv run pytest` (jamais `pip` — le venv n'a pas de module pip).
- **Reproductibilité** : même seed + même config → même vidéo. `load_clips` trie par nom ; `build_edl` n'utilise que son `random.Random(seed)` local, jamais le RNG global, et **l'ordre et le nombre des tirages font partie du contrat** ; le rendu passe `-bitexact`.
- Les timestamps de cut sont quantifiés sur la grille de frames **dans `build_edl`**, jamais dans `render`.
- `build_edl`, `free_windows`, `content_rect`, `frame_extract`, `find_final_scene` sont **pures** : aucun I/O, aucun réseau.
- L'usine ne casse jamais sur un cas dégradé : une fonction qui ne trouve rien retourne `None` ou une valeur neutre.
- Commentaires et messages en **français**.
- Pas de sur-ingénierie : aucun réglage exposé qui n'est pas dans le spec. Les seuils du volet B sont des **constantes de module**.
- Un commit par tâche, message en français.

---

# VOLET A — Ne jamais rejouer un passage déjà montré

### Task 1 : `free_windows`

**Files:**
- Modify: `beatsync.py` — nouvelle fonction juste avant `build_edl`
- Test: `tests/test_free_windows.py` (créer)

**Interfaces:**
- Consumes: la forme des plages produites par `usable_intervals` — `{"start", "end", "motion", "presence"}`.
- Produces: `beatsync.free_windows(intervals: list[dict], consumed: list[tuple[float, float]], source_needed: float, margin: float = 0.5) -> list[dict]` — retourne des dicts de **même forme** que les plages d'entrée, restreints aux portions encore libres et longues d'au moins `source_needed`.

- [ ] **Step 1 : Écrire le test qui échoue**

Créer `tests/test_free_windows.py` :

```python
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
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_free_windows.py -v`
Expected: FAIL — `ImportError: cannot import name 'free_windows' from 'beatsync'`

- [ ] **Step 3 : Implémenter**

Dans `beatsync.py`, juste avant `def build_edl` :

```python
def free_windows(intervals: list[dict], consumed: list[tuple[float, float]],
                 source_needed: float, margin: float = 0.5) -> list[dict]:
    """Portions des plages exploitables qui n'ont pas encore été montrées.

    Les portions consommées sont élargies de `margin` de chaque côté : sans
    cette marge, un nouvel extrait pourrait coller au précédent et rester
    visuellement identique — c'est précisément l'effet de répétition qu'on
    cherche à supprimer. Seules les fenêtres au moins aussi longues que
    `source_needed` sont retournées.

    Les dicts rendus ont la même forme que les plages d'entrée : `motion` et
    `presence` sont hérités du parent (ce sont déjà des moyennes, et on n'a pas
    les données par échantillon pour les recalculer sur une portion). Pure."""
    blocked = sorted((start - margin, end + margin) for start, end in consumed)
    windows: list[dict] = []
    for interval in intervals:
        cursor = interval["start"]
        for block_start, block_end in blocked:
            if block_end <= cursor or block_start >= interval["end"]:
                continue  # hors de cette plage
            if block_start - cursor >= source_needed:
                windows.append({**interval, "start": cursor, "end": block_start})
            cursor = max(cursor, block_end)
        if interval["end"] - cursor >= source_needed:
            windows.append({**interval, "start": cursor, "end": interval["end"]})
    return windows
```

- [ ] **Step 4 : Lancer les tests**

Run: `uv run pytest tests/test_free_windows.py -v && uv run pytest -q`
Expected: PASS partout (303 tests + 11 nouveaux)

- [ ] **Step 5 : Commit**

```bash
git add beatsync.py tests/test_free_windows.py
git commit -m "feat(montage): free_windows, portions d'un clip pas encore montrées

Soustrait des plages exploitables ce qui a déjà été monté, élargi d'une
marge : deux extraits qui se touchent restent visuellement identiques, ce
qui est l'effet de répétition qu'on veut supprimer. Pure et testée."
```

---

### Task 2 : Mémoire de consommation dans `build_edl`

**Files:**
- Modify: `beatsync.py` — `build_edl` : déclaration de `consumed` avant la réservation de la scène de fin (~ligne 750), amorçage par la scène de fin (~ligne 776), filtre `usable` (~ligne 869), choix de la plage en modes chrono et libre (~lignes 923-945), enregistrement de la consommation après l'entrée vidéo (~ligne 970)
- Test: `tests/test_free_windows.py` (ajouter une section d'intégration)

**Interfaces:**
- Consumes: `free_windows(intervals, consumed, source_needed, margin=0.5)` (Task 1).
- Produces: aucune nouvelle fonction publique. `build_edl` garde sa signature. La variable locale `last_clip_in` **disparaît**.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_free_windows.py` :

```python
# --- Intégration dans build_edl --------------------------------------------

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

from beatsync import DEFAULT_CONFIG, build_edl  # noqa: E402

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


def clip(name, duration=300.0, intervals=None):
    n = int(duration * 2) + 1
    return {"path": Path(f"/clips/{name}"), "kind": "video", "duration": duration,
            "width": 1920, "height": 1080, "ratio": 16 / 9,
            "intervals": intervals or [iv(1.0, duration - 1.0)],
            "interest_x": np.full(n, 0.5), "dual": np.zeros(n, dtype=bool),
            "scan_dt": 0.5}


def config(**overrides):
    return {**DEFAULT_CONFIG, "start": 0.0, "end": DURATION,
            "drop_time": 30.0, **overrides}


def overlaps(a, b):
    return a[0] < b[1] - 1e-9 and b[0] < a[1] - 1e-9


def used_ranges(edl, path_name):
    return [(e["clip_in"], e["clip_in"] + e["duration"] * e["speed"])
            for e in edl if e["clip_path"].name == path_name]


def test_two_extracts_of_the_same_clip_never_overlap():
    clips = [clip("a.mp4"), clip("b.mp4")]
    edl = build_edl(make_analysis(), clips, config(), seed=42)
    for name in ("a.mp4", "b.mp4"):
        ranges = used_ranges(edl, name)
        assert len(ranges) >= 2, f"{name} n'a servi qu'une fois, le test ne prouve rien"
        for i, first in enumerate(ranges):
            for second in ranges[i + 1:]:
                assert not overlaps(first, second), f"{name} : {first} recouvre {second}"


def test_chrono_never_goes_backwards():
    clips = [clip("a.mp4"), clip("b.mp4")]
    edl = build_edl(make_analysis(), clips, config(chrono=True), seed=42)
    for name in ("a.mp4", "b.mp4"):
        starts = [e["clip_in"] for e in edl if e["clip_path"].name == name]
        assert starts == sorted(starts), f"{name} : retour en arrière {starts}"


def test_non_chrono_also_avoids_repetition():
    clips = [clip("a.mp4"), clip("b.mp4")]
    edl = build_edl(make_analysis(), clips, config(chrono=False), seed=42)
    ranges = used_ranges(edl, "a.mp4")
    for i, first in enumerate(ranges):
        for second in ranges[i + 1:]:
            assert not overlaps(first, second)


def test_a_poor_catalog_degrades_to_reuse_instead_of_raising():
    """Un seul clip court : la consommation l'épuise vite. On doit rouvrir les
    plages plutôt que faire échouer le lot."""
    clips = [clip("a.mp4", duration=12.0, intervals=[iv(1.0, 11.0)])]
    edl = build_edl(make_analysis(), clips, config(), seed=42)
    assert len(edl) > 10


def test_the_end_scene_extract_is_reserved_before_the_loop():
    """Le climax ne doit pas avoir été montré par un segment antérieur."""
    clips = [clip("a.mp4"), clip("b.mp4")]
    cfg = config(end_scene={**DEFAULT_CONFIG["end_scene"], "enabled": True})
    edl = build_edl(make_analysis(), clips, cfg, seed=42)
    final = edl[-1]
    assert final.get("end_scene") is True
    scene_range = (final["clip_in"],
                   final["clip_in"] + (final["duration"] - final["freeze"]) * final["speed"])
    for entry in edl[:-1]:
        if entry["clip_path"] == final["clip_path"]:
            other = (entry["clip_in"], entry["clip_in"] + entry["duration"] * entry["speed"])
            assert not overlaps(other, scene_range)


def test_still_reproducible():
    clips = [clip("a.mp4"), clip("b.mp4")]
    a = build_edl(make_analysis(), clips, config(), seed=7)
    b = build_edl(make_analysis(), clips, config(), seed=7)
    assert a == b
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_free_windows.py -k "overlap or backwards or reserved" -v`
Expected: FAIL — des extraits d'un même clip se recouvrent (c'est le bug)

- [ ] **Step 3 : Déclarer la mémoire et amorcer la scène de fin**

Dans `build_edl`, juste **avant** le bloc `# --- Scène de fin : un seul segment sur les N derniers beats` :

```python
    # Portions déjà montrées, par clip : le montage ne rejoue jamais un passage
    # (l'effet de retour en arrière que ça produisait cassait la fluidité).
    consumed: dict = {}
```

Puis, dans ce bloc, remplacer :

```python
            scene = find_final_scene(clips, min_source=es_source)
            if scene is not None:
                end_scene, es_start = scene, candidate
```

par :

```python
            scene = find_final_scene(clips, min_source=es_source)
            if scene is not None:
                # Le climax est calculable dès maintenant : on le réserve pour
                # qu'aucun segment ordinaire ne le montre par avance.
                es_interval = scene["interval"]
                es_clip_in = max(es_interval["start"], es_interval["end"] - es_source)
                consumed.setdefault(scene["clip"]["path"], []).append(
                    (es_clip_in, es_clip_in + es_source))
                end_scene = {**scene, "clip_in": es_clip_in, "speed": es_speed,
                             "freeze": freeze, "source": es_source}
                es_start = candidate
```

- [ ] **Step 4 : Faire lire ses valeurs réservées à la branche de la scène de fin**

Dans la boucle, la branche `if end_scene is not None and seg_start >= es_start - 1e-9:` recalcule aujourd'hui vitesse, figé, source et point d'entrée. Remplacer ces quatre calculs par la lecture des valeurs réservées :

```python
            clip = end_scene["clip"]
            es_speed = end_scene["speed"]
            freeze = end_scene["freeze"]
            es_source = end_scene["source"]
            clip_in = end_scene["clip_in"]
            focus_x, layout = frame_extract(clip, clip_in, es_source, config)
```

Le reste de la branche (le `edl.append` et le `continue`) ne change pas.

- [ ] **Step 5 : Filtrer les clips sur leurs fenêtres libres**

Remplacer le bloc `usable = [...]` / `if not usable: raise` par :

```python
        source_needed = duration * speed
        free = {c["path"]: free_windows(intervals_of(c), consumed.get(c["path"], []),
                                        source_needed)
                for c in video_clips}
        usable = [c for c in video_clips if free[c["path"]]]
        if not usable:
            # Catalogue épuisé : plutôt que de faire échouer le lot, on rouvre
            # les plages entières. Mieux vaut un plan revu qu'une variante perdue.
            free = {c["path"]: [iv for iv in intervals_of(c)
                                if iv["end"] - iv["start"] >= source_needed]
                    for c in video_clips}
            usable = [c for c in video_clips if free[c["path"]]]
        if image_clips and duration <= IMAGE_MAX_DUR + 1e-9 \
                and seg_index - last_image_seg >= IMAGE_MIN_GAP:
            usable = usable + image_clips
        if not usable:
            raise ValueError(
                f"aucun clip n'a de plage exploitable de {source_needed:.2f}s "
                "(clips trop courts, trop de zones écartées par le scan, ou "
                "catalogue composé uniquement d'images — une image ne peut tenir "
                f"qu'un segment de {IMAGE_MAX_DUR:.2f}s au plus)"
            )
```

- [ ] **Step 6 : Choisir la plage parmi les fenêtres libres**

Remplacer le bloc qui va de `candidates = [iv for iv in intervals_of(clip) …]` jusqu'à `last_clip_in[clip["path"]] = clip_in` inclus, par :

```python
        candidates = free[clip["path"]]
        # Personnages à l'écran : écarte les plages quasi vides (fallback si toutes le sont).
        min_presence = config.get("min_presence", 0.0)
        candidates = [iv for iv in candidates if iv.get("presence", 1.0) >= min_presence] \
            or candidates

        if config.get("chrono", False):
            # Position dans la vidéo ≈ position dans l'histoire : le montage
            # avance dans le clip au rythme de la timeline (climax au drop).
            # Les fenêtres antérieures à ce qui a déjà servi sont écartées :
            # libres ou non, y revenir romprait la chronologie.
            floor = max((end for _, end in consumed.get(clip["path"], [])), default=0.0)
            ordered = [w for w in candidates
                       if w["end"] - source_needed >= floor - 1e-9] or candidates
            progress = seg_start / out_end if out_end > 0 else 0.0
            slacks = [w["end"] - w["start"] - source_needed for w in ordered]
            target = progress * sum(slacks)
            window, offset = ordered[-1], slacks[-1]
            for w, slack in zip(ordered, slacks):
                if target <= slack:
                    window, offset = w, target
                    break
                target -= slack
            clip_in = window["start"] + offset + rng.uniform(0.0, 1.0)
            clip_in = min(max(clip_in, window["start"]), window["end"] - source_needed)
        else:
            if (section == "drop" or tier == "intense") and len(candidates) > 1:
                # Les moments intenses piochent dans les plages les plus nerveuses.
                median_motion = float(np.median([iv["motion"] for iv in candidates]))
                candidates = [iv for iv in candidates if iv["motion"] >= median_motion] \
                    or candidates
            window = rng.choice(candidates)
            clip_in = rng.uniform(window["start"], window["end"] - source_needed)
        prev_path = clip["path"]
```

Supprimer la déclaration devenue inutile `last_clip_in: dict = {}  # par clip : dernier point d'entrée (mode chrono)`.

- [ ] **Step 7 : Enregistrer la consommation**

Juste après le `edl.append({...})` de la branche **vidéo** (le dernier de la fonction, avant `return edl`) :

```python
        consumed.setdefault(clip["path"], []).append((clip_in, clip_in + source_needed))
```

- [ ] **Step 8 : Lancer les tests**

Run: `uv run pytest tests/test_free_windows.py -v && uv run pytest -q`
Expected: PASS. Les tests existants de `tests/test_chrono.py`, `tests/test_build_edl.py` et `tests/test_build_edl_v2.py` portent sur des propriétés que ce changement préserve (bornes, contiguïté, cadence). Si l'un casse, lire son intention : un test qui vérifie que `clip_in` reste dans les bornes du clip doit rester vert ; un test qui figeait une valeur de `clip_in` précise est à mettre à jour, en le disant dans le rapport.

- [ ] **Step 9 : Commit**

```bash
git add beatsync.py tests/test_free_windows.py
git commit -m "fix(montage): ne rejoue plus un passage déjà montré

Trois causes se combinaient : en mode chrono la garantie de progression
ne valait que 0,1 s ; le clamp appliqué APRÈS elle pouvait faire reculer
le point d'entrée, donc un vrai retour en arrière ; et le mode libre
tirait sans aucune mémoire.

build_edl mémorise désormais les portions consommées par clip, et
free_windows n'offre plus que ce qui reste — aux trois points de décision
à la fois. La scène de fin réserve sa portion avant la boucle. Un
catalogue épuisé rouvre les plages plutôt que de faire échouer le lot."
```

---

# VOLET B — Rogner les bandes noires

### Task 3 : `content_rect`

**Files:**
- Modify: `beatsync.py` — constantes et fonction après `classify_frames` (~ligne 275)
- Test: `tests/test_letterbox.py` (créer)

**Interfaces:**
- Consumes: rien.
- Produces:
  - `beatsync.BAR_LUMA_MAX: float` = `16.0`
  - `beatsync.BAR_MIN_FRACTION: float` = `0.015`
  - `beatsync.BAR_MAX_TOTAL: float` = `0.30`
  - `beatsync.content_rect(frames: np.ndarray) -> dict | None` — `{"x", "y", "w", "h"}` en fractions du cadre, ou `None`.

- [ ] **Step 1 : Écrire le test qui échoue**

Créer `tests/test_letterbox.py` :

```python
"""Détection des bandes noires sur les frames du scan. Logique pure."""

import numpy as np
import pytest

from beatsync import content_rect


def frames(n=10, h=360, w=640, fill=120):
    return np.full((n, h, w, 3), fill, dtype=np.uint8)


def test_a_clean_frame_has_no_crop():
    assert content_rect(frames()) is None


def test_letterbox_is_detected():
    """Bandes de 60 px en haut et en bas d'un cadre de 360 : contenu 240/360."""
    f = frames()
    f[:, :60] = 0
    f[:, -60:] = 0
    rect = content_rect(f)
    assert rect["y"] == pytest.approx(60 / 360)
    assert rect["h"] == pytest.approx(240 / 360)
    assert rect["x"] == pytest.approx(0.0)
    assert rect["w"] == pytest.approx(1.0)


def test_pillarbox_is_detected():
    f = frames()
    f[:, :, :80] = 0
    f[:, :, -80:] = 0
    rect = content_rect(f)
    assert rect["x"] == pytest.approx(80 / 640)
    assert rect["w"] == pytest.approx(480 / 640)
    assert rect["h"] == pytest.approx(1.0)


def test_both_axes_at_once():
    f = frames()
    f[:, :40] = 0
    f[:, -40:] = 0
    f[:, :, :50] = 0
    f[:, :, -50:] = 0
    rect = content_rect(f)
    assert rect["y"] == pytest.approx(40 / 360)
    assert rect["x"] == pytest.approx(50 / 640)


def test_an_asymmetric_bar_is_detected():
    """Rien n'oblige les bandes à être symétriques."""
    f = frames()
    f[:, :90] = 0
    rect = content_rect(f)
    assert rect["y"] == pytest.approx(90 / 360)
    assert rect["h"] == pytest.approx(270 / 360)


def test_a_dark_scene_is_not_cropped():
    """Cadre uniformément sombre : les bandes dépasseraient 30 %, on refuse
    plutôt que de mutiler un plan de nuit."""
    assert content_rect(frames(fill=4)) is None


def test_a_one_pixel_dark_line_is_ignored():
    """Bruit de compression sur le bord : sous le seuil des 1,5 %."""
    f = frames()
    f[:, :1] = 0
    assert content_rect(f) is None


def test_a_bright_subtitle_in_the_bar_does_not_hide_it():
    """Un sous-titre incrusté dans la bande n'apparaît que sur une poignée de
    frames : le 95e percentile l'ignore, là où un maximum se ferait avoir.

    40 frames et une seule porteuse du sous-titre (2,5 %) : c'est l'ordre de
    grandeur réel du scan, qui tourne à 2 fps sur des clips de plusieurs
    minutes. Avec 10 frames, une seule ferait 10 % et remonterait au-dessus du
    95e percentile — le test échouerait pour une raison qui n'existe pas en
    production."""
    f = frames(n=40)
    f[:, :60] = 0
    f[:, -60:] = 0
    f[0, -40:-20, 200:400] = 255
    rect = content_rect(f)
    assert rect is not None
    assert rect["h"] == pytest.approx(240 / 360)


def test_a_dark_line_in_the_middle_is_not_a_bar():
    """Seuls les segments continus depuis un bord comptent."""
    f = frames()
    f[:, 150:170] = 0
    assert content_rect(f) is None


def test_no_frames_yields_none():
    assert content_rect(np.zeros((0, 360, 640, 3), dtype=np.uint8)) is None
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_letterbox.py -v`
Expected: FAIL — `ImportError: cannot import name 'content_rect' from 'beatsync'`

- [ ] **Step 3 : Implémenter**

Dans `beatsync.py`, après `classify_frames` :

```python
# Détection des bandes noires (letterbox / pillarbox). Un extrait de film
# récupéré sur YouTube arrive souvent avec deux bandes : sans rognage, elles
# survivent au cadrage 9:16 et le ratio du conteneur fausse le choix du layout.
BAR_LUMA_MAX = 16.0        # une bande reste sous cette luminance (0-255)
BAR_MIN_FRACTION = 0.015   # en dessous, c'est du bruit de bord, pas une bande
BAR_MAX_TOTAL = 0.30       # au-delà, c'est une scène sombre : on ne rogne pas


def _edge_runs(profile: np.ndarray) -> tuple[int, int]:
    """Longueurs des segments SOMBRES CONTINUS en tête et en queue d'un profil.
    Une ligne sombre isolée au milieu n'est pas une bande et ne compte pas."""
    lit = np.flatnonzero(profile >= BAR_LUMA_MAX)
    if not len(lit):
        return len(profile), len(profile)   # tout est sombre : refusé en amont
    return int(lit[0]), int(len(profile) - 1 - lit[-1])


def content_rect(frames: np.ndarray) -> dict | None:
    """Rectangle utile d'un clip, en fractions du cadre, ou None si aucune bande.

    Le 95e percentile sur l'ensemble des frames — et non le maximum — évite
    qu'un sous-titre incrusté dans la bande ou un flash isolé masque la
    détection. Pure."""
    if not len(frames):
        return None
    luma = np.asarray(frames, dtype=np.float32).mean(axis=3)
    rows = np.percentile(luma.mean(axis=2), 95, axis=0)
    cols = np.percentile(luma.mean(axis=1), 95, axis=0)
    height, width = len(rows), len(cols)

    def keep(run: int, size: int) -> int:
        return run if run >= BAR_MIN_FRACTION * size else 0

    top, bottom = (keep(r, height) for r in _edge_runs(rows))
    left, right = (keep(c, width) for c in _edge_runs(cols))
    if top + bottom > BAR_MAX_TOTAL * height or left + right > BAR_MAX_TOTAL * width:
        return None
    if not (top or bottom or left or right):
        return None
    return {
        "x": left / width,
        "y": top / height,
        "w": (width - left - right) / width,
        "h": (height - top - bottom) / height,
    }
```

- [ ] **Step 4 : Lancer les tests**

Run: `uv run pytest tests/test_letterbox.py -v && uv run pytest -q`
Expected: PASS partout

- [ ] **Step 5 : Commit**

```bash
git add beatsync.py tests/test_letterbox.py
git commit -m "feat(scan): content_rect détecte les bandes noires

Profil de luminance par ligne et par colonne, 95e percentile sur les
frames — pas le maximum, sinon un sous-titre incrusté dans la bande
suffirait à masquer la détection. Deux garde-fous : une bande sous 1,5 %
est du bruit de bord, des bandes au-delà de 30 % sont une scène de nuit."
```

---

### Task 4 : Stocker le rectangle et corriger le ratio

**Files:**
- Modify: `beatsync.py` — `_scan_one` (~ligne 449), `_scan_payload` / `_apply_scan_payload` (~ligne 474), `scan_clips` (validation du cache, ~ligne 495)
- Test: `tests/test_letterbox.py` (ajouter), `tests/test_scan_cache.py` (ajouter)

**Interfaces:**
- Consumes: `content_rect(frames) -> dict | None` (Task 3).
- Produces:
  - `beatsync.SCAN_CACHE_VERSION: int` = `2`
  - Après `scan_clips`, un clip vidéo porte `clip["crop"]` (fractions ou `None`) et, si un rognage est détecté, un `clip["ratio"]` décrivant le **contenu**. La Task 5 lit `clip["crop"]`, la Task 6 aussi.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_letterbox.py` :

```python
# --- Intégration au scan ----------------------------------------------------

from pathlib import Path  # noqa: E402

import beatsync  # noqa: E402
from beatsync import SCAN_CACHE_VERSION, _scan_payload, scan_clips  # noqa: E402


def letterboxed_clip(tmp_path):
    return {"path": tmp_path / "film.mp4", "kind": "video", "duration": 100.0,
            "width": 1920, "height": 1080, "ratio": 1920 / 1080}


def stub_scan(monkeypatch, rect):
    """Remplace le décodage réel par un scan qui pose un rectangle donné."""
    def fake(clip):
        clip["intervals"] = [{"start": 1.0, "end": 99.0, "motion": 0.5, "presence": 0.9}]
        clip["interest_x"] = np.full(200, 0.5)
        clip["dual"] = np.zeros(200, dtype=bool)
        clip["scan_dt"] = 0.5
        clip["crop"] = rect
        if rect is not None:
            clip["ratio"] = (rect["w"] * clip["width"]) / (rect["h"] * clip["height"])
    monkeypatch.setattr(beatsync, "_scan_one", fake)


def test_scan_stores_the_crop_and_fixes_the_ratio(tmp_path, monkeypatch):
    """Un film 2.35:1 letterboxé dans du 16:9 doit être vu comme du 2.35:1,
    sinon les règles de layout du format carré restent fausses."""
    rect = {"x": 0.0, "y": 0.118, "w": 1.0, "h": 0.764}
    stub_scan(monkeypatch, rect)
    clip = letterboxed_clip(tmp_path)
    scan_clips([clip])
    assert clip["crop"] == rect
    assert clip["ratio"] == pytest.approx(2.33, abs=0.05)


def test_payload_carries_the_crop_and_a_version():
    clip = {"intervals": [], "interest_x": np.array([0.5]), "dual": np.array([False]),
            "scan_dt": 0.5, "crop": {"x": 0.0, "y": 0.1, "w": 1.0, "h": 0.8}}
    payload = _scan_payload(clip)
    assert payload["crop"] == clip["crop"]
    assert payload["version"] == SCAN_CACHE_VERSION
```

Ajouter à `tests/test_scan_cache.py` :

```python
def test_a_cache_entry_without_version_is_a_miss(tmp_path, monkeypatch):
    """Les caches écrits avant la détection des bandes n'ont pas de `crop` :
    il faut re-scanner, pas les lire à moitié."""
    import json

    from beatsync import SCAN_CACHE_VERSION

    path = tmp_path / "a.mp4"
    path.write_bytes(b"x")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    digest = beatsync.hashlib.md5(str(path).encode()).hexdigest()
    (cache_dir / f"{digest}.json").write_text(json.dumps({
        "mtime": path.stat().st_mtime,
        "intervals": [{"start": 0.0, "end": 5.0, "motion": 0.5, "presence": 1.0}],
        "interest_x": [0.5], "dual": [False], "scan_dt": 0.5,
    }))

    scanned = []
    monkeypatch.setattr(beatsync, "_scan_one",
                        lambda clip: (scanned.append(clip["path"].name),
                                      clip.update(intervals=[], interest_x=np.array([0.5]),
                                                  dual=np.array([False]), scan_dt=0.5,
                                                  crop=None)))
    clip = {"path": path, "kind": "video", "duration": 10.0,
            "width": 1920, "height": 1080, "ratio": 16 / 9}
    beatsync.scan_clips([clip], cache_dir=cache_dir)
    assert scanned == ["a.mp4"], "l'entrée sans version aurait dû être ignorée"
    assert SCAN_CACHE_VERSION >= 2
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `uv run pytest tests/test_letterbox.py tests/test_scan_cache.py -k "crop or version" -v`
Expected: FAIL — `ImportError: cannot import name 'SCAN_CACHE_VERSION' from 'beatsync'`

- [ ] **Step 3 : Implémenter**

Dans `beatsync.py`, à côté de `SCAN_FPS` :

```python
# Version du cache de scan : incrémentée quand le format du payload change,
# pour que les entrées antérieures soient re-calculées et non lues à moitié.
SCAN_CACHE_VERSION = 2
```

Dans `_scan_one`, après `clip["scan_dt"] = 1.0 / SCAN_FPS` :

```python
    # Bandes noires : un extrait de film letterboxé doit être rogné avant tout
    # cadrage, et son ratio décrire le contenu, pas le conteneur.
    clip["crop"] = content_rect(frames)
    if clip["crop"] is not None:
        clip["ratio"] = ((clip["crop"]["w"] * clip["width"])
                         / (clip["crop"]["h"] * clip["height"]))
```

Dans `_scan_payload`, ajouter deux clés :

```python
        "crop": clip.get("crop"),
        "version": SCAN_CACHE_VERSION,
```

Dans `_apply_scan_payload`, ajouter :

```python
    clip["crop"] = payload.get("crop")
    if clip["crop"] is not None:
        clip["ratio"] = ((clip["crop"]["w"] * clip["width"])
                         / (clip["crop"]["h"] * clip["height"]))
```

Dans `scan_clips`, la validation du cache devient :

```python
                    cached = json.loads(cache_path.read_text())
                    if cached.get("version") == SCAN_CACHE_VERSION \
                            and cached.get("mtime") == clip["path"].stat().st_mtime:
                        _apply_scan_payload(clip, cached)
                        continue
```

- [ ] **Step 4 : Lancer les tests**

Run: `uv run pytest tests/test_letterbox.py tests/test_scan_cache.py -v && uv run pytest -q`
Expected: PASS partout

- [ ] **Step 5 : Commit**

```bash
git add beatsync.py tests/test_letterbox.py tests/test_scan_cache.py
git commit -m "feat(scan): stocke le rectangle utile et corrige le ratio

Un film 2.35:1 letterboxé dans du 16:9 était vu comme du 16:9, ce qui
faussait les règles de layout — le fond flouté ne se déclenchait jamais
en carré. Le cache gagne un numéro de version : les entrées antérieures
n'ont pas de crop et doivent être recalculées, pas lues à moitié."
```

---

### Task 5 : Rogner au rendu

**Files:**
- Modify: `beatsync.py` — `build_edl` (dimensions et clé `crop` de l'entrée vidéo et de la scène de fin), `_segment_filters` (tête de la chaîne `pre`, ~ligne 1344)
- Test: `tests/test_letterbox.py` (ajouter)

**Interfaces:**
- Consumes: `clip["crop"]` en fractions (Task 4).
- Produces: une entrée d'EDL vidéo porte `crop` en **pixels** (`{"x", "y", "w", "h"}`) ou `None`, et ses `clip_w`/`clip_h` sont les dimensions **du contenu**. La Task 6 n'en dépend pas.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_letterbox.py` :

```python
# --- Rendu ------------------------------------------------------------------

from beatsync import DEFAULT_CONFIG, _segment_filters  # noqa: E402


def cropped_entry(**overrides):
    return {"timeline_start": 0.0, "duration": 1.0, "clip_path": Path("/clips/f.mp4"),
            "kind": "video", "clip_in": 5.0, "speed": 1.0, "effects": [],
            "layout": "crop", "focus_x": 0.5,
            "crop": {"x": 0, "y": 132, "w": 1920, "h": 816},
            "clip_w": 1920, "clip_h": 816, **overrides}


def test_crop_comes_first_in_the_filter_chain():
    args = _segment_filters(cropped_entry(), DEFAULT_CONFIG)
    # Le graphe est le dernier argument, quel que soit le drapeau (-vf ou
    # -filter_complex) : on travaille dessus, pas sur la liste aplatie.
    graph = args[1]
    assert "crop=1920:816:0:132" in graph
    assert graph.index("crop=1920:816:0:132") < graph.index("delogo="), \
        "le delogo compte en fractions du contenu : il doit venir après le rognage"


def test_an_entry_without_crop_is_unchanged():
    entry = cropped_entry(crop=None)
    joined = " ".join(_segment_filters(entry, DEFAULT_CONFIG))
    assert "crop=1920:816" not in joined


def test_the_crop_applies_to_every_layout():
    for layout in ("crop", "split", "blur"):
        joined = " ".join(_segment_filters(cropped_entry(layout=layout), DEFAULT_CONFIG))
        assert "crop=1920:816:0:132" in joined


def test_build_edl_puts_content_dimensions_on_the_entry(monkeypatch):
    """clip_w/clip_h décrivent le contenu : c'est ce qui recale le delogo."""
    from tests.test_free_windows import clip as make_clip, config, make_analysis

    from beatsync import build_edl

    c = make_clip("f.mp4")
    c["crop"] = {"x": 0.0, "y": 132 / 1080, "w": 1.0, "h": 816 / 1080}
    edl = build_edl(make_analysis(), [c], config(), seed=42)
    entry = next(e for e in edl if e["kind"] == "video")
    assert entry["clip_h"] == 816
    assert entry["clip_w"] == 1920
    assert entry["crop"] == {"x": 0, "y": 132, "w": 1920, "h": 816}
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `uv run pytest tests/test_letterbox.py -k "chain or layout or dimensions" -v`
Expected: FAIL — `crop=1920:816:0:132` absent de la chaîne de filtres

- [ ] **Step 3 : Calculer le rectangle en pixels dans `build_edl`**

Dans `build_edl`, juste avant le `edl.append` de la branche **vidéo** (après le calcul de `focus_x, layout`) :

```python
        # Rectangle utile en pixels. Les dimensions passées à l'entrée sont
        # celles du CONTENU : le delogo, exprimé en fractions de clip_w/clip_h,
        # se recale ainsi tout seul sur le vrai coin de l'image.
        entry_crop, clip_w, clip_h = None, clip["width"], clip["height"]
        rect = clip.get("crop")
        if rect is not None:
            clip_w = int(clip["width"] * rect["w"]) & ~1   # dimensions paires
            clip_h = int(clip["height"] * rect["h"]) & ~1
            entry_crop = {"x": int(clip["width"] * rect["x"]),
                          "y": int(clip["height"] * rect["y"]),
                          "w": clip_w, "h": clip_h}
```

Puis, dans ce `edl.append`, remplacer les deux dernières clés :

```python
                "clip_w": clip_w,
                "clip_h": clip_h,
                "crop": entry_crop,
```

La branche de la **scène de fin** construit sa propre entrée et a besoin du même traitement. Juste après son `focus_x, layout = frame_extract(clip, clip_in, es_source, config)`, insérer :

```python
            entry_crop, clip_w, clip_h = None, clip["width"], clip["height"]
            rect = clip.get("crop")
            if rect is not None:
                clip_w = int(clip["width"] * rect["w"]) & ~1
                clip_h = int(clip["height"] * rect["h"]) & ~1
                entry_crop = {"x": int(clip["width"] * rect["x"]),
                              "y": int(clip["height"] * rect["y"]),
                              "w": clip_w, "h": clip_h}
```

et remplacer, dans son `edl.append`, les deux dernières clés par :

```python
                    "clip_w": clip_w,
                    "clip_h": clip_h,
                    "crop": entry_crop,
```

(L'indentation est plus profonde d'un niveau que dans la branche vidéo : la scène de fin est imbriquée dans un `if`.)

- [ ] **Step 4 : Rogner en tête de la chaîne de filtres**

Dans `_segment_filters`, remplacer `pre = ""` par :

```python
    pre = ""
    crop = entry.get("crop")
    if crop:
        # Bandes noires retirées AVANT tout le reste : sans ça elles survivent
        # au cadrage 9:16 et se retrouvent dans la vidéo finale.
        pre += f"crop={crop['w']}:{crop['h']}:{crop['x']}:{crop['y']},"
```

- [ ] **Step 5 : Lancer les tests**

Run: `uv run pytest tests/test_letterbox.py -v && uv run pytest -q`
Expected: PASS partout

- [ ] **Step 6 : Vérifier sur un rendu réel**

Fabriquer un clip letterboxé de test à partir d'un clip existant :

```bash
ffmpeg -v error -y -i clips/<un clip>.mp4 -t 20 \
  -vf "scale=1920:816,pad=1920:1080:0:132:black" -c:v libx264 -crf 20 /tmp/letterbox.mp4
```

Copier ce fichier dans `clips/`, lancer un rendu court, puis extraire une frame et vérifier objectivement que ses bords haut et bas ne sont **pas** noirs (contrairement à la source). Supprimer ensuite le fichier de `clips/` — c'est le catalogue réel de l'utilisateur — et vider `data/cache/scan` si le test l'a pollué. Consigner dans le rapport la commande, le rectangle détecté et ce que montre la frame.

- [ ] **Step 7 : Commit**

```bash
git add beatsync.py tests/test_letterbox.py
git commit -m "feat(rendu): rogne les bandes noires en tête de chaîne

L'entrée d'EDL porte le rectangle en pixels et des dimensions décrivant
le CONTENU. Le delogo, exprimé en fractions de clip_w/clip_h, se recale
donc au passage — il visait à côté sur un clip letterboxé."
```

---

### Task 6 : Recaler le centre d'intérêt

**Files:**
- Modify: `beatsync.py` — `frame_extract` (~ligne 624)
- Test: `tests/test_letterbox.py` (ajouter)

**Interfaces:**
- Consumes: `clip["crop"]` en fractions (Task 4).
- Produces: rien de nouveau — `frame_extract` garde sa signature.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_letterbox.py` :

```python
# --- Recalage du centre d'intérêt -------------------------------------------

from beatsync import apply_format, frame_extract  # noqa: E402


def scanned(interest, crop=None):
    n = 40
    clip = {"path": Path("/clips/f.mp4"), "kind": "video", "duration": 100.0,
            "width": 1920, "height": 1080, "ratio": 16 / 9,
            "interest_x": np.full(n, interest), "dual": np.zeros(n, dtype=bool),
            "scan_dt": 0.5}
    if crop is not None:
        clip["crop"] = crop
    return clip


def test_focus_x_is_remapped_onto_the_content():
    """Bandes latérales de 25 % : le centre du cadre entier (0.5) est aussi le
    centre du contenu (0.5), mais un point à 0.375 du cadre est à 0.25 du
    contenu. Sans remappage, le cadrage viserait à côté après rognage."""
    crop = {"x": 0.25, "y": 0.0, "w": 0.5, "h": 1.0}
    focus_x, _ = frame_extract(scanned(0.375, crop), 1.0, 2.0, apply_format(DEFAULT_CONFIG))
    assert focus_x == pytest.approx(0.25)


def test_focus_x_is_clamped_when_the_point_falls_in_a_bar():
    crop = {"x": 0.25, "y": 0.0, "w": 0.5, "h": 1.0}
    focus_x, _ = frame_extract(scanned(0.05, crop), 1.0, 2.0, apply_format(DEFAULT_CONFIG))
    assert focus_x == pytest.approx(0.0)


def test_focus_x_is_untouched_without_a_crop():
    focus_x, _ = frame_extract(scanned(0.3), 1.0, 2.0, apply_format(DEFAULT_CONFIG))
    assert focus_x == pytest.approx(0.3)


def test_a_letterbox_does_not_move_focus_x():
    """Bandes haut/bas : rien ne change horizontalement."""
    crop = {"x": 0.0, "y": 0.12, "w": 1.0, "h": 0.76}
    focus_x, _ = frame_extract(scanned(0.3, crop), 1.0, 2.0, apply_format(DEFAULT_CONFIG))
    assert focus_x == pytest.approx(0.3)
```

- [ ] **Step 2 : Lancer les tests pour vérifier qu'ils échouent**

Run: `uv run pytest tests/test_letterbox.py -k remapped -v`
Expected: FAIL — `assert 0.375 == 0.25`

- [ ] **Step 3 : Implémenter**

Dans `frame_extract`, juste après le calcul de `focus_x` (la ligne `focus_x = float(np.clip(window_x.mean(), 0.0, 1.0))`) :

```python
    # `interest_x` est mesuré sur le cadre ENTIER, bandes comprises. Après
    # rognage, un clip à bandes latérales verrait son centre d'intérêt pointer
    # à côté : on remappe vers le contenu.
    crop = clip.get("crop")
    if crop and crop["w"] > 0:
        focus_x = float(np.clip((focus_x - crop["x"]) / crop["w"], 0.0, 1.0))
```

- [ ] **Step 4 : Lancer les tests**

Run: `uv run pytest tests/test_letterbox.py -v && uv run pytest -q`
Expected: PASS partout

- [ ] **Step 5 : Commit**

```bash
git add beatsync.py tests/test_letterbox.py
git commit -m "fix(cadrage): recale focus_x sur le contenu après rognage

interest_x est mesuré sur le cadre entier. Sans ce remappage, rogner les
bandes latérales aurait décalé le centre d'intérêt — on aurait corrigé un
défaut en en créant un autre."
```

---

### Task 7 : Documentation

**Files:**
- Modify: `CLAUDE.md` — puces `scan_clips`, `build_edl`, `render`

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: rien.

- [ ] **Step 1 : Vérifier chaque nom dans le code**

Avant d'écrire, relire dans `beatsync.py` et confirmer l'orthographe exacte de :
`free_windows`, `content_rect`, `_edge_runs`, `BAR_LUMA_MAX`, `BAR_MIN_FRACTION`,
`BAR_MAX_TOTAL`, `SCAN_CACHE_VERSION`, et la clé `crop`. Une doc qui ment est pire
qu'une doc absente.

- [ ] **Step 2 : Documenter l'anti-répétition dans la puce `build_edl`**

Ajouter, dans le style dense des puces voisines :

> Anti-répétition : `build_edl` mémorise par clip les portions déjà montrées, et `free_windows` (**pure et testée**) n'offre plus que ce qui reste, élargi d'une marge de 0,5 s — deux extraits qui se touchent restent visuellement identiques. La mémoire alimente les trois points de décision (clips utilisables, mapping chrono, tirage libre) ; le mode `chrono` y ajoute un plancher, la fin de la dernière portion consommée. La scène de fin réserve sa portion avant la boucle. Catalogue épuisé : les plages sont rouvertes plutôt que de faire échouer le lot.

- [ ] **Step 3 : Documenter la détection des bandes dans la puce `scan_clips`**

Ajouter :

> `content_rect` (**pure**) détecte les bandes noires sur les frames déjà décodées : profil de luminance par ligne et par colonne, 95ᵉ percentile sur les frames (pas le maximum — un sous-titre incrusté dans la bande masquerait la détection), segments continus depuis les bords seulement. Une bande sous `BAR_MIN_FRACTION` (1,5 %) est du bruit ; des bandes au-delà de `BAR_MAX_TOTAL` (30 %) sont une scène de nuit, pas un letterbox → `None`. Le rectangle est stocké en fractions dans `clip["crop"]`, et `ratio` est corrigé pour décrire le **contenu** — sans quoi un film 2.35:1 letterboxé passe pour du 16:9 et les règles de layout du carré restent fausses. `SCAN_CACHE_VERSION` invalide les caches antérieurs.

- [ ] **Step 4 : Documenter le rognage dans la puce `render`**

Ajouter :

> Bandes noires : l'entrée porte `crop` en pixels et des `clip_w`/`clip_h` décrivant le contenu ; `_segment_filters` rogne en **tête** de `pre`, avant le `delogo` — qui se recale donc au passage, puisqu'il compte en fractions de ces dimensions.

- [ ] **Step 5 : Contrôle final**

Run: `uv run pytest -q`
Expected: PASS (aucun changement de code)

- [ ] **Step 6 : Commit**

```bash
git add CLAUDE.md
git commit -m "docs: anti-répétition des extraits et rognage des bandes noires"
```

---

## Vérification finale

- [ ] `uv run pytest -q` — toute la suite passe
- [ ] Un rendu réel sur le vrai catalogue, **cache de scan vidé** (`rm -rf data/cache/scan`) pour que la détection des bandes tourne : la génération aboutit, et le premier scan est plus long que d'habitude — c'est attendu, il recalcule tout.
- [ ] Regarder la vidéo produite : plus d'impression de retour en arrière, et aucune bande noire sur les clips letterboxés du catalogue
- [ ] Noter le temps du premier scan complet après invalidation du cache — c'est ce que l'équipe subira une fois à la prochaine mise à jour
