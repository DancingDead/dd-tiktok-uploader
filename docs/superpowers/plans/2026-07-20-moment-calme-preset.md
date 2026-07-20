# Option « Moment de la track » (fort / calme) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permettre à un preset de cadrer le montage sur un **passage calme** du morceau (au lieu du drop), pour des vidéos plus douces.

**Architecture:** Sélection du passage au niveau moteur. Une fonction pure `find_calm` (miroir de `find_drop`) repère la fenêtre la plus calme ; `resolve_window` branche sur `config["section"]` et, en mode calme, met `drop_time = None` — ce qui fait prendre à `build_edl` son chemin « sans drop » existant (pas de strobo/gasp, coupes espacées). `build_edl` n'est **pas** modifié.

**Tech Stack:** Python 3.12 + numpy (moteur), Flask (validation serveur), React + TypeScript (UI preset), pytest.

## Global Constraints

- **uv obligatoire** : lancer les tests avec `uv run pytest` (le venv n'a pas de `pip`).
- **Reproductibilité** : `find_calm` doit être **déterministe** (aucun RNG). Défaut `section="drop"` → aucun changement pour l'existant.
- **`build_edl` intact** : le mode calme repose entièrement sur `drop_time = None`.
- **Rétrocompatibilité** : bases et presets existants doivent continuer en mode `"drop"`.
- **Défense XSS serveur** : toute valeur d'override doit être validée (`coerce_overrides`) — `section` ∈ {`"drop"`, `"calm"`}, sinon `ValueError`.
- **Collision de nom à documenter** : `config["section"]` (drop/calm) est distinct du champ `entry["section"]` des entrées d'EDL (buildup/drop, `beatsync.py:576`) — ajouter un commentaire là où on introduit la clé.

---

### Task 1 : `find_calm` + clé de config `section`

**Files:**
- Modify: `beatsync.py` (bloc `DEFAULT_CONFIG` ~ligne 21-53 ; nouvelle fonction après `find_drop`, ~ligne 347)
- Test: `tests/test_find_calm.py` (créer)

**Interfaces:**
- Produces: `find_calm(analysis: dict, config: dict, duration: float | str = 30.0) -> float | None` — début (calé sur un beat) de la fenêtre la plus calme ; `None` si le morceau est plus court que la fenêtre.
- Produces: clé `DEFAULT_CONFIG["section"] = "drop"`.

- [ ] **Step 1 : Écrire les tests qui échouent**

Créer `tests/test_find_calm.py` :

```python
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
```

- [ ] **Step 2 : Lancer les tests, vérifier l'échec**

Run: `uv run pytest tests/test_find_calm.py -v`
Expected: FAIL — `ImportError: cannot import name 'find_calm'`.

- [ ] **Step 3 : Ajouter la clé de config**

Dans `beatsync.py`, dans `DEFAULT_CONFIG`, juste après la ligne `"buildup": 10.0,` (~ligne 32), ajouter :

```python
    # Passage ciblé : "drop" = moment fort (build-up + drop) | "calm" = passage calme.
    # NB : distinct du champ "section" des entrées d'EDL (buildup/drop) construit dans build_edl.
    "section": "drop",
```

- [ ] **Step 4 : Implémenter `find_calm`**

Dans `beatsync.py`, juste **après** la fonction `find_drop` (après la ligne 347), ajouter :

```python
def find_calm(analysis: dict, config: dict, duration: float | str = 30.0) -> float | None:
    """Début (calé sur un beat) de la fenêtre la plus calme du morceau, ou None
    si le morceau est plus court que la fenêtre demandée.

    Miroir de `find_drop` : au lieu du contraste d'énergie maximal, on cherche la
    fenêtre de `duration` s à énergie moyenne minimale, en n'acceptant que les
    fenêtres SANS silence (leur minimum d'énergie reste au-dessus d'un seuil) —
    sinon on choisirait une intro/fade muet ou le bord du silence.

    Déterministe (aucun RNG) : ne casse pas la reproductibilité.
    NB : le "calm" ici concerne le CHOIX DU PASSAGE (config["section"]) ; à ne pas
    confondre avec le champ "section" (buildup/drop) des entrées d'EDL.
    """
    dt = 0.25
    grid = np.arange(0.0, float(analysis["duration"]), dt)
    energy = np.interp(
        grid,
        np.asarray(analysis["energy_times"], dtype=float),
        np.asarray(analysis["energy"], dtype=float),
    )
    kernel = np.ones(max(1, int(2.0 / dt)))
    energy = np.convolve(energy, kernel / len(kernel), mode="same")

    W = int(round(float(duration) / dt))
    if W < 1 or len(energy) < W:
        return None

    windows = np.lib.stride_tricks.sliding_window_view(energy, W)  # (N-W+1, W)
    means = windows.mean(axis=1)
    mins = windows.min(axis=1)

    silence = 0.05 * float(energy.max())
    musical = np.flatnonzero(mins >= silence)      # fenêtres sans silence
    if musical.size == 0:                          # morceau très faible partout
        musical = np.arange(len(means))
    best = int(musical[int(np.argmin(means[musical]))])

    beats = np.asarray(analysis["beats"], dtype=float)
    return float(beats[int(np.argmin(np.abs(beats - grid[best])))])
```

- [ ] **Step 5 : Lancer les tests, vérifier le succès**

Run: `uv run pytest tests/test_find_calm.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6 : Non-régression globale**

Run: `uv run pytest -q`
Expected: tous les tests passent (find_drop, build_edl, framing… inchangés).

- [ ] **Step 7 : Commit**

```bash
git add beatsync.py tests/test_find_calm.py
git commit -m "feat(beatsync): find_calm + clé config section (drop/calm)"
```

---

### Task 2 : `resolve_window` branche sur `section`

**Files:**
- Modify: `beatsync.py` — fonction `resolve_window` (lignes 366-386)
- Test: `tests/test_find_calm.py` (ajouter au fichier de Task 1)

**Interfaces:**
- Consumes: `find_calm(...)`, `DEFAULT_CONFIG["section"]` (Task 1).
- Produces: `resolve_window` pose `config["drop_time"] = None` et `config["start"]` dans la zone calme quand `config["section"] == "calm"`.

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajouter à la **fin** de `tests/test_find_calm.py` :

```python
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
```

- [ ] **Step 2 : Lancer les tests, vérifier l'échec**

Run: `uv run pytest tests/test_find_calm.py::test_resolve_window_calm_sets_no_drop -v`
Expected: FAIL — `config["drop_time"]` n'est pas `None` (le code actuel appelle toujours `find_drop`).

- [ ] **Step 3 : Modifier `resolve_window`**

Remplacer **entièrement** le corps de `resolve_window` (lignes 366-386) par :

```python
def resolve_window(analysis: dict, config: dict, start: float | None = None,
                   duration: float | str = 30.0) -> dict:
    """Résout drop_time / start / end dans config (et le retourne).

    config["section"] pilote le passage ciblé :
      - "drop" (défaut) : cadrage sur le drop détecté (buildup avant).
      - "calm" : cadrage sur la fenêtre la plus calme (find_calm), sans drop.
    start=None => cadrage auto ; start fourni (CLI --start) => prioritaire.
    duration="full" => tout le morceau ; sinon fin étendue à la frontière de phrase.
    """
    if config.get("section") == "calm":
        drop = None
        auto_start = (find_calm(analysis, config, duration)
                      if duration != "full" else None)
    else:
        drop = find_drop(analysis, config)
        auto_start = (max(0.0, drop - config["buildup"]) if drop is not None else 0.0)
    config["drop_time"] = drop
    if start is None:
        start = auto_start if auto_start is not None else 0.0
    config["start"] = float(start)
    if duration == "full":
        config["end"] = float(analysis["duration"])
    else:
        end = min(config["start"] + float(duration), float(analysis["duration"]))
        config["end"] = snap_end_to_phrase(
            end, drop, analysis["beats"], analysis["duration"], config["phrase_beats"]
        )
    return config
```

- [ ] **Step 4 : Lancer les tests, vérifier le succès**

Run: `uv run pytest tests/test_find_calm.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5 : Non-régression globale**

Run: `uv run pytest -q`
Expected: tout passe (test_framing, test_build_edl inchangés).

- [ ] **Step 6 : Commit**

```bash
git add beatsync.py tests/test_find_calm.py
git commit -m "feat(beatsync): resolve_window cadre sur section calme (drop_time=None)"
```

---

### Task 3 : Argument CLI `--section`

**Files:**
- Modify: `beatsync.py` — `main()` (argparse ~ligne 1054 ; application ~ligne 1074)

**Interfaces:**
- Consumes: `DEFAULT_CONFIG["section"]` (Task 1), branche `resolve_window` (Task 2).

- [ ] **Step 1 : Ajouter l'argument argparse**

Dans `main()`, juste après le bloc `--duration` (après la ligne 1054), ajouter :

```python
    parser.add_argument("--section", choices=["drop", "calm"], default=None,
                        help='passage ciblé : "drop" (moment fort, défaut) ou "calm" (passage calme)')
```

- [ ] **Step 2 : Appliquer l'argument à la config**

Dans `main()`, juste après le bloc `if args.cut_every is not None:` (après la ligne 1074), ajouter :

```python
    if args.section is not None:
        config["section"] = args.section
```

- [ ] **Step 3 : Vérifier l'aide CLI**

Run: `uv run python beatsync.py --help`
Expected: la ligne `--section {drop,calm}` apparaît dans l'aide, sans erreur.

- [ ] **Step 4 : Non-régression**

Run: `uv run pytest -q`
Expected: tout passe.

- [ ] **Step 5 : Commit**

```bash
git add beatsync.py
git commit -m "feat(cli): --section {drop,calm} dans beatsync"
```

---

### Task 4 : Validation serveur de `section`

**Files:**
- Modify: `webui.py` — constantes (~ligne 34) et `coerce_overrides` (lignes 37-53)
- Test: `tests/test_webui_platform.py` (ajouter)

**Interfaces:**
- Consumes: `coerce_overrides` reçoit les overrides de preset (endpoints `/api/presets`).
- Produces: `coerce_overrides` lève `ValueError` si `section` ∉ {`"drop"`, `"calm"`}.

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajouter à `tests/test_webui_platform.py` (imports en tête du fichier si absents) :

```python
import pytest

from webui import coerce_overrides


def test_coerce_accepts_valid_section():
    assert coerce_overrides({"section": "calm"})["section"] == "calm"
    assert coerce_overrides({"section": "drop"})["section"] == "drop"


def test_coerce_rejects_unknown_section():
    with pytest.raises(ValueError):
        coerce_overrides({"section": "chill"})
```

- [ ] **Step 2 : Lancer les tests, vérifier l'échec**

Run: `uv run pytest tests/test_webui_platform.py::test_coerce_rejects_unknown_section -v`
Expected: FAIL — `coerce_overrides` n'élève pas (la valeur passe telle quelle).

- [ ] **Step 3 : Ajouter la constante des valeurs autorisées**

Dans `webui.py`, juste après `ALLOWED_COLOR_GRADES = (...)` (ligne 34), ajouter :

```python
ALLOWED_SECTIONS = ("drop", "calm")
```

- [ ] **Step 4 : Valider `section` dans `coerce_overrides`**

Dans `webui.py`, dans `coerce_overrides`, juste après le bloc `color_grade` (après la ligne 45), ajouter :

```python
    if "section" in coerced and coerced["section"] not in ALLOWED_SECTIONS:
        raise ValueError(f"section inconnue : {coerced['section']!r}")
```

- [ ] **Step 5 : Lancer les tests, vérifier le succès**

Run: `uv run pytest tests/test_webui_platform.py -v`
Expected: PASS.

- [ ] **Step 6 : Commit**

```bash
git add webui.py tests/test_webui_platform.py
git commit -m "feat(webui): valide l'override section (drop|calm)"
```

---

### Task 5 : Sélecteur « Moment de la track » dans l'éditeur de preset

**Files:**
- Modify: `frontend/src/lib/api.ts` — type `Overrides` (~ligne 36-51)
- Modify: `frontend/src/features/presets/PresetEditor.tsx` — constantes (~ligne 32), state (~ligne 112), objet `overrides` (~ligne 131-145), JSX (carte « Cadrage & contenu », ~ligne 253)

**Interfaces:**
- Consumes: champ config `section` (Task 1), validation serveur (Task 4).
- Produces: le preset enregistré inclut `section: "drop" | "calm"`.

- [ ] **Step 1 : Ajouter `section` au type `Overrides`**

Dans `frontend/src/lib/api.ts`, dans le type `Overrides` (après `cut_mode?: string`, ligne 42), ajouter :

```typescript
  section?: string
```

- [ ] **Step 2 : Ajouter la liste d'options dans l'éditeur**

Dans `frontend/src/features/presets/PresetEditor.tsx`, à côté de `COLOR_GRADES` (après la ligne 32), ajouter :

```typescript
const TRACK_SECTIONS = [
  { value: "drop", label: "Fort (build-up + drop)" },
  { value: "calm", label: "Calme (passage planant)" },
]
```

- [ ] **Step 3 : Ajouter le state du champ**

Dans `PresetEditor.tsx`, avec les autres `useState` (après la ligne 112, `const [colorGrade, ...]`), ajouter :

```typescript
  const [section, setSection] = useState(o.section ?? "drop")
```

- [ ] **Step 4 : Inclure `section` dans l'objet `overrides` sauvegardé**

Dans `PresetEditor.tsx`, dans l'objet `overrides` de la fonction `save()` (après `cut_mode: cutMode,`, ligne 137), ajouter :

```typescript
      section,
```

- [ ] **Step 5 : Ajouter le sélecteur dans le JSX**

Dans `PresetEditor.tsx`, dans la carte « Cadrage & contenu », juste **avant** le bloc `<div className="grid gap-1.5">` de « Ambiance couleur » (avant la ligne 253), ajouter :

```tsx
          <div className="grid gap-1.5">
            <Label>Moment de la track</Label>
            <Select value={section} onValueChange={setSection}>
              <SelectTrigger className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TRACK_SECTIONS.map((s) => (
                  <SelectItem key={s.value} value={s.value}>
                    {s.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
```

- [ ] **Step 6 : Vérifier le build du front**

Run: `cd frontend && npm run build`
Expected: build OK (`tsc -b && vite build` sans erreur de type), puis `cd ..`.

- [ ] **Step 7 : Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/features/presets/PresetEditor.tsx
git commit -m "feat(front): sélecteur « Moment de la track » (fort/calme) dans le preset"
```

---

## Vérification finale (manuelle, après toutes les tâches)

- [ ] `uv run pytest -q` — toute la suite passe.
- [ ] `cd frontend && npm run build` — front OK.
- [ ] Sanity CLI (si un morceau + clips sont dispo en local) :
  `uv run python beatsync.py <track> <clips_dir> --section calm -o /tmp/calm.mp4`
  → le log affiche « pas de drop net » et une fenêtre située dans un passage calme.
- [ ] Dans l'UI (preset), le sélecteur « Moment de la track » s'affiche, se sauvegarde, et une génération en mode « Calme » produit un montage plus doux.

## Self-review (couverture spec)

- Champ config `section` (défaut "drop", rétrocompat) → Task 1.
- `find_calm` pur + garde anti-silence (min ≥ 5 % du max) → Task 1.
- Branche `resolve_window` (drop_time=None, fallback start) → Task 2.
- CLI `--section` → Task 3.
- Validation serveur → Task 4.
- Sélecteur UI preset → Task 5.
- `build_edl` non modifié ; reproductibilité (find_calm déterministe) → contraintes globales + tests.
