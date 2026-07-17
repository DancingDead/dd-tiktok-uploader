# Presets d'ambiance + leviers moteur — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rendre l'usine à vidéos adaptable à tout type de niche en ajoutant 4 leviers moteur d'ambiance (étalonnage couleur, grain/VHS, intensité de glitch, slow-mo global) et en portant les modèles de preset de 2 à 5.

**Architecture:** Chaque levier est piloté par une clé de `DEFAULT_CONFIG` (dans `beatsync.py`) et implémenté soit par une fonction pure construisant un fragment de filtre FFmpeg (couleur, grain), soit dans la logique pure `build_edl` (glitch, slow-mo). Le serveur Flask (`webui.py`) coerce/valide les nouvelles clés. Le front React (`frontend/src/features/presets/`) expose les nouveaux réglages et 5 modèles de pré-remplissage. L'UI vanilla de fallback (`templates/index.html`) n'est pas touchée.

**Tech Stack:** Python 3 (uv, pytest), FFmpeg (subprocess), React + TypeScript (Vite), SQLite.

## Global Constraints

- **uv obligatoire** : lancer Python via `uv run` (le venv n'a pas de pip). Tests : `uv run pytest`.
- **Reproductibilité** : même seed + même config → EDL identique. `build_edl` utilise UNIQUEMENT son `random.Random(seed)` local (`rng`), jamais le RNG global. Aucun `Date`/horloge dans la logique pure.
- **merge_settings ne conserve que les clés présentes dans `DEFAULT_CONFIG`** : toute nouvelle clé de config DOIT être ajoutée à `DEFAULT_CONFIG` sinon elle est silencieusement ignorée à la fusion.
- **Fonctions pures = zéro I/O** : les constructeurs de filtres ne font aucun accès disque ni subprocess.
- **Compat ascendante** : les presets existants stockent `accents.glitch: true`. Cette valeur bool doit continuer de fonctionner (coercée en `0.6`).
- **Défense XSS/serveur** : tout champ numérique d'override est coercé en nombre côté serveur ; `color_grade` est validé contre une enum → 400 si inconnu.
- **Français** : messages, libellés UI et commits en français, cohérents avec l'existant.

---

## File Structure

- `beatsync.py` — MODIFIER : `DEFAULT_CONFIG` (nouvelles clés) ; nouvelles fonctions pures `color_grade_filter`, `grain_filter`, `glitch_amount` ; `build_edl` (slow-mo global + proportion glitch) ; `_segment_filters` (injection couleur + grain).
- `webui.py` — MODIFIER : `coerce_overrides` (grain, clip_speed, accents.glitch, validation color_grade).
- `frontend/src/lib/api.ts` — MODIFIER : type `Overrides` (color_grade, grain, clip_speed, glitch:number).
- `frontend/src/features/presets/PresetEditor.tsx` — MODIFIER : nouveaux contrôles.
- `frontend/src/features/presets/PresetsTab.tsx` — MODIFIER : 5 modèles.
- `tests/test_beatsync_ambiance.py` — CRÉER : tests des helpers purs + slow-mo + glitch.
- `tests/test_webui_platform.py` — MODIFIER : tests coercion des nouvelles clés.

---

### Task 1 : Nouvelles clés dans DEFAULT_CONFIG

**Files:**
- Modify: `beatsync.py:21-50` (dict `DEFAULT_CONFIG`)
- Test: `tests/test_beatsync_ambiance.py` (créer)

**Interfaces:**
- Produces: clés `DEFAULT_CONFIG["color_grade"] = "neutre"`, `DEFAULT_CONFIG["grain"] = 0.0`, `DEFAULT_CONFIG["clip_speed"] = 1.0`. `accents.glitch` reste présent (défaut `True`).

- [ ] **Step 1: Write the failing test**

Créer `tests/test_beatsync_ambiance.py` :

```python
"""Tests des leviers d'ambiance : config par défaut, filtres couleur/grain,
coercion glitch, slow-mo global. Logique pure, aucun média requis."""

from beatsync import DEFAULT_CONFIG


def test_default_config_has_ambiance_keys():
    assert DEFAULT_CONFIG["color_grade"] == "neutre"
    assert DEFAULT_CONFIG["grain"] == 0.0
    assert DEFAULT_CONFIG["clip_speed"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_beatsync_ambiance.py::test_default_config_has_ambiance_keys -v`
Expected: FAIL avec `KeyError: 'color_grade'`

- [ ] **Step 3: Add the keys**

Dans `beatsync.py`, dans le dict `DEFAULT_CONFIG`, juste après la ligne `"accents": {"rgb": True, "glitch": True}, ...` (ligne 37), ajouter :

```python
    "color_grade": "neutre",            # ambiance couleur : neutre|chaud|froid|delave
    "grain": 0.0,                       # texture film/VHS, 0.0–1.0
    "clip_speed": 1.0,                  # slow-mo global par segment, 0.5–1.5
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_beatsync_ambiance.py::test_default_config_has_ambiance_keys -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add beatsync.py tests/test_beatsync_ambiance.py
git commit -m "feat(config): clés d'ambiance color_grade/grain/clip_speed dans DEFAULT_CONFIG"
```

---

### Task 2 : Fonction pure `color_grade_filter`

**Files:**
- Modify: `beatsync.py` (ajouter la fonction près des autres helpers de rendu, avant `_segment_filters` à la ligne 839)
- Test: `tests/test_beatsync_ambiance.py`

**Interfaces:**
- Produces: `color_grade_filter(grade: str) -> str` — renvoie un fragment FFmpeg `eq=…` ou `""` pour `neutre`/valeur inconnue.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_beatsync_ambiance.py` :

```python
from beatsync import color_grade_filter


def test_color_grade_neutre_and_unknown_are_empty():
    assert color_grade_filter("neutre") == ""
    assert color_grade_filter("inconnu") == ""


def test_color_grade_known_values_return_eq_fragment():
    for grade in ("chaud", "froid", "delave"):
        frag = color_grade_filter(grade)
        assert frag.startswith("eq=")
    # chaud et froid diffèrent
    assert color_grade_filter("chaud") != color_grade_filter("froid")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_beatsync_ambiance.py -k color_grade -v`
Expected: FAIL avec `ImportError: cannot import name 'color_grade_filter'`

- [ ] **Step 3: Implement**

Dans `beatsync.py`, juste avant `def _segment_filters` (ligne 839) :

```python
def color_grade_filter(grade: str) -> str:
    """Fragment FFmpeg d'étalonnage couleur pour un segment. '' si neutre/inconnu."""
    return {
        "chaud": "eq=gamma_r=1.06:gamma_b=0.94:saturation=1.05",
        "froid": "eq=gamma_b=1.06:gamma_r=0.94:saturation=0.98",
        "delave": "eq=saturation=0.72:contrast=0.94:brightness=0.03",
    }.get(grade, "")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_beatsync_ambiance.py -k color_grade -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add beatsync.py tests/test_beatsync_ambiance.py
git commit -m "feat(beatsync): fonction pure color_grade_filter"
```

---

### Task 3 : Fonction pure `grain_filter`

**Files:**
- Modify: `beatsync.py` (juste après `color_grade_filter`)
- Test: `tests/test_beatsync_ambiance.py`

**Interfaces:**
- Produces: `grain_filter(amount: float) -> str` — `""` si `amount <= 0` ; sinon `noise=alls=<n>:allf=t`, avec dérive chroma ajoutée si `amount >= 0.6`.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_beatsync_ambiance.py` :

```python
from beatsync import grain_filter


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_beatsync_ambiance.py -k grain -v`
Expected: FAIL avec `ImportError: cannot import name 'grain_filter'`

- [ ] **Step 3: Implement**

Dans `beatsync.py`, juste après `color_grade_filter` :

```python
def grain_filter(amount: float) -> str:
    """Fragment FFmpeg de grain/VHS pour un segment. '' si amount <= 0.
    Bruit temporel proportionnel ; dérive chroma permanente au-delà de 0.6 (VHS)."""
    if amount <= 0:
        return ""
    frag = f"noise=alls={round(amount * 24)}:allf=t"
    if amount >= 0.6:
        frag += ",rgbashift=rh=2:bh=-2"
    return frag
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_beatsync_ambiance.py -k grain -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add beatsync.py tests/test_beatsync_ambiance.py
git commit -m "feat(beatsync): fonction pure grain_filter (grain + VHS chroma)"
```

---

### Task 4 : Fonction pure `glitch_amount` (coercion bool → float)

**Files:**
- Modify: `beatsync.py` (juste après `grain_filter`)
- Test: `tests/test_beatsync_ambiance.py`

**Interfaces:**
- Produces: `glitch_amount(accents: dict) -> float` — lit `accents["glitch"]` : `True`→`0.6`, `False`/absent→`0.0`, nombre → clampé `[0.0, 1.0]`.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_beatsync_ambiance.py` :

```python
from beatsync import glitch_amount


def test_glitch_amount_bool_and_missing():
    assert glitch_amount({"glitch": True}) == 0.6
    assert glitch_amount({"glitch": False}) == 0.0
    assert glitch_amount({}) == 0.0


def test_glitch_amount_number_is_clamped():
    assert glitch_amount({"glitch": 0.35}) == 0.35
    assert glitch_amount({"glitch": 2.0}) == 1.0
    assert glitch_amount({"glitch": -1.0}) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_beatsync_ambiance.py -k glitch_amount -v`
Expected: FAIL avec `ImportError: cannot import name 'glitch_amount'`

- [ ] **Step 3: Implement**

Dans `beatsync.py`, juste après `grain_filter` :

```python
def glitch_amount(accents: dict) -> float:
    """Intensité de glitch 0.0–1.0 depuis accents['glitch'].
    Compat : bool True→0.6, False/absent→0.0 ; nombre clampé."""
    value = accents.get("glitch", False)
    if isinstance(value, bool):
        return 0.6 if value else 0.0
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
```

Note : `isinstance(value, bool)` DOIT précéder le `float()` car `bool` est un sous-type de `int` en Python (`float(True) == 1.0`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_beatsync_ambiance.py -k glitch_amount -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add beatsync.py tests/test_beatsync_ambiance.py
git commit -m "feat(beatsync): fonction pure glitch_amount (bool→float, clamp)"
```

---

### Task 5 : Slow-mo global dans `build_edl`

**Files:**
- Modify: `beatsync.py:480` (dans `build_edl`, initialisation de `speed`)
- Test: `tests/test_beatsync_ambiance.py`

**Interfaces:**
- Consumes: `DEFAULT_CONFIG["clip_speed"]` (Task 1).
- Produces: chaque `entry["speed"]` de l'EDL vaut `config["clip_speed"]` par défaut ; le gasp pré-drop force toujours `0.5`.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_beatsync_ambiance.py` (réutilise les fixtures du module — importer depuis test_build_edl serait fragile ; construire une fixture locale minimale) :

```python
import numpy as np
from beatsync import DEFAULT_CONFIG, build_edl

BPM = 128.0
BEAT = 60.0 / BPM
DURATION = 60.0


def _analysis():
    beats = np.arange(0.0, DURATION, BEAT)
    times = np.linspace(0.0, DURATION, 601)
    energy = np.where(times < 30.0, 0.1, 0.9)
    return {"duration": DURATION, "bpm": BPM, "beats": beats,
            "energy": energy, "energy_times": times}


def _clips():
    return [
        {"path": f"/fake/clip{i}.mp4", "duration": 60.0,
         "width": 1920, "height": 1080} for i in range(3)
    ]


def test_clip_speed_propagates_to_all_segments():
    config = {**DEFAULT_CONFIG, "clip_speed": 0.85, "start": 0.0, "end": 20.0,
              "drop_time": None, "effects": {"zoom": False, "flash": False,
              "shake": False, "speed": False}}
    edl = build_edl(_analysis(), _clips(), config, seed=1)
    assert edl, "EDL non vide"
    assert all(abs(e["speed"] - 0.85) < 1e-9 for e in edl)
```

Note : vérifier les clés exactes attendues par `build_edl` pour l'analyse (`energy_times` vs autre nom) en lisant `tests/test_build_edl.py::make_analysis` et aligner la fixture `_analysis()` dessus avant de lancer.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_beatsync_ambiance.py::test_clip_speed_propagates_to_all_segments -v`
Expected: FAIL — `speed` vaut `1.0` partout (assert échoue).

- [ ] **Step 3: Implement**

Dans `beatsync.py`, ligne 480, remplacer :

```python
        speed = 1.0
```

par :

```python
        speed = config.get("clip_speed", 1.0)
```

La condition du gasp qui suit (lignes 481-483) écrase toujours `speed = 0.5` quand elle s'applique — inchangée.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_beatsync_ambiance.py::test_clip_speed_propagates_to_all_segments -v`
Expected: PASS

- [ ] **Step 5: Run full build_edl suite (non-régression reproductibilité)**

Run: `uv run pytest tests/test_build_edl.py tests/test_build_edl_v2.py -v`
Expected: PASS (aucune régression — `clip_speed` défaut 1.0 ne change rien).

- [ ] **Step 6: Commit**

```bash
git add beatsync.py tests/test_beatsync_ambiance.py
git commit -m "feat(edl): slow-mo global clip_speed appliqué à chaque segment"
```

---

### Task 6 : Proportion de glitch pilotée par l'intensité dans `build_edl`

**Files:**
- Modify: `beatsync.py:498-500` (dans `build_edl`, condition d'ajout de l'effet `glitch`)
- Test: `tests/test_beatsync_ambiance.py`

**Interfaces:**
- Consumes: `glitch_amount` (Task 4).
- Produces: l'effet `"glitch"` est ajouté quand `rng.random() < glitch_amount(accents)` ; `amount=0` → jamais, `amount=1` → sur tous les segments intenses éligibles du drop.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_beatsync_ambiance.py` :

```python
def _count_glitch(config, seed=1):
    edl = build_edl(_analysis(), _clips(), config, seed=seed)
    return sum(1 for e in edl if "glitch" in e.get("effects", []))


def test_glitch_proportion_scales_with_amount():
    base = {**DEFAULT_CONFIG, "start": 0.0, "end": 30.0,
            "drop_time": 15.0, "buildup": 5.0}
    none = {**base, "accents": {"rgb": True, "glitch": 0.0}}
    full = {**base, "accents": {"rgb": True, "glitch": 1.0}}
    assert _count_glitch(none) == 0
    assert _count_glitch(full) >= _count_glitch(none)
    # à amount=1, au moins un glitch attendu sur la section drop intense
    assert _count_glitch(full) > 0
```

Note : ajuster `drop_time`/`buildup`/`end` si la fixture ne produit pas de segment `tier == "intense"` sur la section drop — lire `build_edl` (lignes 470-503) pour comprendre quand `tier == "intense"` (percentile d'énergie haut) et caler l'énergie de `_analysis()` en conséquence.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_beatsync_ambiance.py -k glitch_proportion -v`
Expected: FAIL — `accents.glitch: 0.0` est falsy MAIS `1.0` déclenche le seuil fixe `< 0.25` (comportement non piloté), l'assertion `_count_glitch(none) == 0` peut passer par chance ; l'assertion de scaling échoue car le seuil est figé à 0.25.

- [ ] **Step 3: Implement**

Dans `beatsync.py`, remplacer les lignes 498-500 :

```python
            if accents.get("glitch") and drop_seg_count > 0 and tier == "intense" \
                    and rng.random() < 0.25:
                effects.append("glitch")
```

par :

```python
            if drop_seg_count > 0 and tier == "intense" \
                    and rng.random() < glitch_amount(accents):
                effects.append("glitch")
```

(`accents` est déjà défini ligne 486 ; `glitch_amount` est défini dans le même module.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_beatsync_ambiance.py -k glitch_proportion -v`
Expected: PASS

- [ ] **Step 5: Non-régression**

Run: `uv run pytest tests/test_build_edl.py tests/test_build_edl_v2.py -v`
Expected: PASS. Si un test verrouillait l'ancien seuil 0.25 avec `glitch: True`, il reste vert car `glitch_amount({"glitch": True}) == 0.6 > 0.25` — vérifier ; sinon ajuster le test existant.

- [ ] **Step 6: Commit**

```bash
git add beatsync.py tests/test_beatsync_ambiance.py
git commit -m "feat(edl): proportion de glitch pilotée par accents.glitch (intensité)"
```

---

### Task 7 : Injection couleur + grain dans `_segment_filters`

**Files:**
- Modify: `beatsync.py:869-882` (dans `_segment_filters`, chaîne `post`, après les accents rgb/glitch et avant le drawtext)
- Test: `tests/test_beatsync_ambiance.py`

**Interfaces:**
- Consumes: `color_grade_filter` (Task 2), `grain_filter` (Task 3).
- Produces: `_segment_filters(entry, config)` inclut le fragment couleur puis le fragment grain dans la chaîne de filtres quand `config["color_grade"]`/`config["grain"]` sont actifs.

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_beatsync_ambiance.py` :

```python
from beatsync import _segment_filters


def _entry():
    return {"clip": "/fake/clip0.mp4", "clip_in": 0.0, "duration": 1.0,
            "speed": 1.0, "effects": [], "layout": "crop", "focus_x": 0.5,
            "clip_w": 1920, "clip_h": 1080}


def test_segment_filters_inject_color_and_grain():
    config = {**DEFAULT_CONFIG, "color_grade": "froid", "grain": 0.8}
    args = _segment_filters(_entry(), config)
    joined = " ".join(args)
    assert "eq=gamma_b=1.06" in joined       # étalonnage froid
    assert "noise=alls=" in joined           # grain
    assert "rgbashift" in joined             # dérive VHS (grain 0.8)


def test_segment_filters_neutral_config_has_no_grade_or_grain():
    config = {**DEFAULT_CONFIG, "color_grade": "neutre", "grain": 0.0}
    joined = " ".join(_segment_filters(_entry(), config))
    assert "eq=gamma" not in joined
    assert "noise=alls=" not in joined
```

Note : lire `_segment_filters` (lignes 839-923) pour confirmer les clés exactes attendues dans `entry` (ex. `clip_w`/`clip_h` pour delogo) et ajuster `_entry()` afin que l'appel n'échoue pas. `delogo` est `True` par défaut → fournir `clip_w`/`clip_h`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_beatsync_ambiance.py -k segment_filters -v`
Expected: FAIL — `noise=alls=` / `eq=gamma_b` absents de la sortie.

- [ ] **Step 3: Implement**

Dans `beatsync.py`, dans `_segment_filters`, juste après le bloc glitch/rgb (après la ligne 872, le `elif "rgb" in effects:` ... `post.append("rgbashift=rh=8:bh=-8:...")`) et AVANT le bloc `cap = entry.get("caption")` (ligne 874), ajouter :

```python
    grade = color_grade_filter(config.get("color_grade", "neutre"))
    if grade:
        post.append(grade)
    grain = grain_filter(config.get("grain", 0.0))
    if grain:
        post.append(grain)
```

Ordre : les punchlines (drawtext) restent posées APRÈS, donc nettes (couleur/grain ne les dégradent pas).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_beatsync_ambiance.py -k segment_filters -v`
Expected: PASS

- [ ] **Step 5: Non-régression framing**

Run: `uv run pytest tests/test_framing.py -v`
Expected: PASS (les layouts split/blur/crop restent valides ; couleur/grain sont dans `post_chain` commun).

- [ ] **Step 6: Commit**

```bash
git add beatsync.py tests/test_beatsync_ambiance.py
git commit -m "feat(render): injection étalonnage couleur + grain par segment"
```

---

### Task 8 : Coercion/validation serveur des nouvelles clés

**Files:**
- Modify: `webui.py:31` (`NUMERIC_OVERRIDE_KEYS`) et `webui.py:34-40` (`coerce_overrides`)
- Test: `tests/test_webui_platform.py`

**Interfaces:**
- Consumes: rien de nouveau.
- Produces: `coerce_overrides` force `grain`/`clip_speed` en float, coerce `accents["glitch"]` en float s'il est présent et non-bool, valide `color_grade` contre `{"neutre","chaud","froid","delave"}` (ValueError si inconnu).

- [ ] **Step 1: Write the failing test**

Ajouter à `tests/test_webui_platform.py` (repérer d'abord les imports en tête du fichier ; `coerce_overrides` est dans `webui`) :

```python
import pytest
from webui import coerce_overrides


def test_coerce_overrides_numeric_ambiance_keys():
    out = coerce_overrides({"grain": "0.5", "clip_speed": "0.85"})
    assert out["grain"] == 0.5
    assert out["clip_speed"] == 0.85


def test_coerce_overrides_glitch_number_in_accents():
    out = coerce_overrides({"accents": {"rgb": True, "glitch": "0.35"}})
    assert out["accents"]["glitch"] == 0.35


def test_coerce_overrides_glitch_bool_preserved():
    out = coerce_overrides({"accents": {"glitch": True}})
    assert out["accents"]["glitch"] is True  # bool inchangé, coercé plus tard côté moteur


def test_coerce_overrides_rejects_unknown_color_grade():
    with pytest.raises(ValueError):
        coerce_overrides({"color_grade": "arc-en-ciel"})


def test_coerce_overrides_accepts_known_color_grade():
    assert coerce_overrides({"color_grade": "froid"})["color_grade"] == "froid"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_webui_platform.py -k "ambiance or glitch or color_grade" -v`
Expected: FAIL — `grain`/`clip_speed` non coercés (restent str), pas de validation `color_grade`.

- [ ] **Step 3: Implement**

Dans `webui.py`, ligne 31, étendre :

```python
NUMERIC_OVERRIDE_KEYS = ("min_presence", "cut_every", "buildup", "strobe_beats",
                         "grain", "clip_speed")
ALLOWED_COLOR_GRADES = ("neutre", "chaud", "froid", "delave")
```

Puis remplacer le corps de `coerce_overrides` (lignes 34-40) par :

```python
def coerce_overrides(overrides: dict) -> dict:
    """Force les clés numériques connues en nombres, coerce l'intensité de glitch
    et valide color_grade. ValueError/TypeError si non convertible/inconnu."""
    coerced = dict(overrides)
    for key in NUMERIC_OVERRIDE_KEYS:
        if key in coerced and not isinstance(coerced[key], (int, float)):
            coerced[key] = float(coerced[key])
    if "color_grade" in coerced and coerced["color_grade"] not in ALLOWED_COLOR_GRADES:
        raise ValueError(f"color_grade inconnu : {coerced['color_grade']!r}")
    accents = coerced.get("accents")
    if isinstance(accents, dict) and "glitch" in accents \
            and not isinstance(accents["glitch"], bool) \
            and not isinstance(accents["glitch"], (int, float)):
        accents = dict(accents)
        accents["glitch"] = float(accents["glitch"])
        coerced["accents"] = accents
    return coerced
```

Note : le bool `glitch` est laissé tel quel (le moteur le coerce via `glitch_amount`) ; seuls les glitch en string sont convertis en float. `isinstance(True, int)` étant vrai, l'ordre des `not isinstance` garantit qu'un bool n'est jamais transformé.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_webui_platform.py -k "ambiance or glitch or color_grade" -v`
Expected: PASS

- [ ] **Step 5: Non-régression webui**

Run: `uv run pytest tests/test_webui_platform.py tests/test_webui_pure.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add webui.py tests/test_webui_platform.py
git commit -m "feat(webui): coercion grain/clip_speed/glitch + validation color_grade"
```

---

### Task 9 : Type `Overrides` étendu (front)

**Files:**
- Modify: `frontend/src/lib/api.ts:36-47` (type `Overrides`)

**Interfaces:**
- Produces: `Overrides.color_grade?: string`, `Overrides.grain?: number`, `Overrides.clip_speed?: number`, `accents.glitch?: boolean | number`.

- [ ] **Step 1: Modify the type**

Dans `frontend/src/lib/api.ts`, remplacer le type `Overrides` (lignes 36-47) :

```typescript
export type Overrides = {
  effects?: { zoom?: boolean; flash?: boolean; shake?: boolean; speed?: boolean }
  accents?: { rgb?: boolean; glitch?: boolean | number }
  delogo?: boolean
  chrono?: boolean
  min_presence?: number
  cut_mode?: string
  cut_every?: number
  buildup?: number
  strobe_beats?: number
  color_grade?: string
  grain?: number
  clip_speed?: number
  subtitles?: { font?: string }
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cd frontend && npm run build` (ou `npx tsc --noEmit`)
Expected: aucune erreur de type liée à `Overrides`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(front): type Overrides étendu (color_grade/grain/clip_speed/glitch)"
```

---

### Task 10 : Contrôles d'ambiance dans `PresetEditor`

**Files:**
- Modify: `frontend/src/features/presets/PresetEditor.tsx`

**Interfaces:**
- Consumes: type `Overrides` (Task 9).
- Produces: l'objet `overrides` envoyé par `save()` inclut `color_grade`, `grain`, `clip_speed`, et `accents.glitch` en number.

- [ ] **Step 1: Add the color-grade constant and state**

Dans `PresetEditor.tsx`, après la constante `CAPTION_FONTS` (ligne 26), ajouter :

```typescript
const COLOR_GRADES = [
  { value: "neutre", label: "Neutre" },
  { value: "chaud", label: "Chaud" },
  { value: "froid", label: "Froid" },
  { value: "delave", label: "Délavé" },
] as const
```

Dans le composant, à côté des autres `useState` (après la ligne 105 `const [font, setFont] = ...`), ajouter :

```typescript
  const [colorGrade, setColorGrade] = useState(o.color_grade ?? "neutre")
  const [grain, setGrain] = useState(o.grain ?? 0)
  const [clipSpeed, setClipSpeed] = useState(o.clip_speed ?? 1)
  const glitchInit =
    typeof o.accents?.glitch === "number"
      ? o.accents.glitch
      : o.accents?.glitch
        ? 0.6
        : 0
  const [glitch, setGlitch] = useState(glitchInit)
```

Note : cela REMPLACE le `const [glitch, setGlitch] = useState(o.accents?.glitch ?? false)` existant (ligne 97). Supprimer l'ancienne ligne.

- [ ] **Step 2: Update the `save()` payload**

Dans `save()`, dans l'objet `overrides` (lignes 115-126), remplacer `accents: { rgb, glitch }` et ajouter les clés :

```typescript
    const overrides: Overrides = {
      effects: { zoom, flash, shake, speed },
      accents: { rgb, glitch },
      delogo,
      chrono,
      min_presence: minPresence,
      cut_mode: cutMode,
      cut_every: cutEvery,
      buildup,
      strobe_beats: strobeBeats,
      color_grade: colorGrade,
      grain,
      clip_speed: clipSpeed,
      subtitles: { font },
    }
```

(`glitch` est désormais un number → `accents: { rgb, glitch }` envoie bien un number.)

- [ ] **Step 3: Replace the glitch Toggle with a NumberField**

Dans la carte « Accents » (lignes 194-206), remplacer le `<Toggle checked={glitch} ...>Micro-glitch</Toggle>` par :

```tsx
          <NumberField
            id="glitch"
            label="Intensité glitch"
            value={glitch}
            onChange={setGlitch}
            step={0.05}
            min={0}
            max={1}
          />
```

- [ ] **Step 4: Add an "Ambiance couleur" select + grain + clip_speed**

Dans la carte « Cadrage & contenu » (lignes 208-229), après le `NumberField` de `min-presence`, ajouter le grain et la vitesse ; et ajouter un select couleur. Insérer dans le `<CardContent>` :

```tsx
          <div className="grid gap-1.5">
            <Label>Ambiance couleur</Label>
            <Select value={colorGrade} onValueChange={setColorGrade}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {COLOR_GRADES.map((g) => (
                  <SelectItem key={g.value} value={g.value}>
                    {g.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <NumberField
            id="grain"
            label="Grain / VHS"
            value={grain}
            onChange={setGrain}
            step={0.05}
            min={0}
            max={1}
          />
          <NumberField
            id="clip-speed"
            label="Vitesse clip (slow-mo)"
            value={clipSpeed}
            onChange={setClipSpeed}
            step={0.05}
            min={0.5}
            max={1.5}
          />
```

- [ ] **Step 5: Verify it compiles**

Run: `cd frontend && npm run build`
Expected: build OK, aucune erreur de type.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/presets/PresetEditor.tsx
git commit -m "feat(front): contrôles ambiance (couleur, grain, vitesse, intensité glitch)"
```

---

### Task 11 : Cinq modèles dans `PresetsTab`

**Files:**
- Modify: `frontend/src/features/presets/PresetsTab.tsx:9-25` (PRESET_TEMPLATES) et `72-92` (boutons)

**Interfaces:**
- Consumes: type `Overrides` (Task 9), état/props existants.
- Produces: 5 boutons de modèle pré-remplissant l'éditeur.

- [ ] **Step 1: Replace PRESET_TEMPLATES**

Dans `PresetsTab.tsx`, remplacer le bloc `const PRESET_TEMPLATES` (lignes 9-25) :

```typescript
// Modèles d'ambiance : pré-remplissent l'éditeur à la création.
const PRESET_TEMPLATES: Record<string, Overrides> = {
  doux: {
    cut_mode: "fixed", cut_every: 4, strobe_beats: 0,
    effects: { zoom: false, flash: false, shake: false, speed: false },
    accents: { rgb: false, glitch: 0 },
    color_grade: "chaud", grain: 0.1, clip_speed: 0.9,
    subtitles: { font: "douce" },
  },
  chill: {
    cut_mode: "energy", strobe_beats: 0,
    effects: { zoom: true, flash: false, shake: false, speed: false },
    accents: { rgb: false, glitch: 0 },
    color_grade: "delave", grain: 0.2, clip_speed: 0.85,
    subtitles: { font: "elegante" },
  },
  energique: {
    cut_mode: "energy", strobe_beats: 16,
    effects: { zoom: true, flash: true, shake: true, speed: true },
    accents: { rgb: true, glitch: 0.35 },
    color_grade: "froid", grain: 0, clip_speed: 1.0,
    subtitles: { font: "impact" },
  },
  cinematique: {
    cut_mode: "energy", strobe_beats: 0,
    effects: { zoom: true, flash: false, shake: false, speed: true },
    accents: { rgb: false, glitch: 0 },
    color_grade: "froid", grain: 0.1, clip_speed: 0.9,
    subtitles: { font: "elegante" },
  },
  retro: {
    cut_mode: "fixed", cut_every: 2, strobe_beats: 0,
    effects: { zoom: false, flash: false, shake: false, speed: false },
    accents: { rgb: true, glitch: 0.7 },
    color_grade: "delave", grain: 0.8, clip_speed: 1.0,
    subtitles: { font: "sobre" },
  },
}

const TEMPLATE_BUTTONS: { key: keyof typeof PRESET_TEMPLATES; label: string }[] = [
  { key: "doux", label: "Doux" },
  { key: "chill", label: "Chill / Lo-fi" },
  { key: "energique", label: "Énergique / Phonk" },
  { key: "cinematique", label: "Cinématique" },
  { key: "retro", label: "Rétro / VHS" },
]
```

- [ ] **Step 2: Replace the two hard-coded buttons with a mapped list**

Dans `PresetsTab.tsx`, remplacer les deux `<Button variant="outline">Doux</Button>` / `Énergique` (lignes 73-92) par :

```tsx
          {TEMPLATE_BUTTONS.map((t) => (
            <Button
              key={t.key}
              variant="outline"
              className="w-full"
              onClick={() => {
                setSelectedId(null)
                setTemplate(PRESET_TEMPLATES[t.key])
              }}
            >
              {t.label}
            </Button>
          ))}
```

- [ ] **Step 3: Verify it compiles**

Run: `cd frontend && npm run build`
Expected: build OK.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/presets/PresetsTab.tsx
git commit -m "feat(front): cinq modèles d'ambiance (Doux/Chill/Énergique/Cinématique/Rétro)"
```

---

### Task 12 : Vérification end-to-end + suite complète

**Files:** aucun (validation).

- [ ] **Step 1: Suite Python complète**

Run: `uv run pytest`
Expected: tout PASS.

- [ ] **Step 2: Build front complet**

Run: `cd frontend && npm run build`
Expected: build OK.

- [ ] **Step 3: Smoke test moteur (optionnel si des médias de test existent)**

Si un dossier de clips + un morceau de test sont disponibles, générer une vidéo avec un preset « Rétro » (grain 0.8, glitch 0.7, color_grade délavé) et une avec « Doux », et vérifier visuellement le contraste d'ambiance. Sinon, s'appuyer sur les tests unitaires des fragments FFmpeg (Tasks 2-3-7) — les fragments sont vérifiés, FFmpeg les accepte (syntaxe `eq=`/`noise=`/`rgbashift=` standard).

- [ ] **Step 4: Commit (si des ajustements ont été faits)**

```bash
git add -A
git commit -m "test: vérification end-to-end presets d'ambiance"
```

---

## Self-Review

**Spec coverage :**
- Volet 1.1 Étalonnage couleur → Tasks 1, 2, 7 ✓
- Volet 1.2 Grain/VHS → Tasks 1, 3, 7 ✓
- Volet 1.3 Intensité glitch (bool→float, compat) → Tasks 4, 6, 8 ✓
- Volet 1.4 Slow-mo global → Tasks 1, 5 ✓
- Compat serveur (coerce/validation) → Task 8 ✓
- Volet 2 Cinq modèles → Task 11 ✓
- Volet 3 UI React (éditeur + api.ts) → Tasks 9, 10 ✓
- Volet 3 UI vanilla NON touchée → respecté (aucune task ne modifie `templates/index.html`) ✓
- Volet 4 Tests (helpers, build_edl, reproductibilité, webui) → Tasks 2-8 + Task 12 ✓
- Invariants (RNG local, tri clips, bitexact, quantification) → préservés : Task 5 note la non-régression reproductibilité, Task 6 passe par `rng` local ✓

**Placeholders :** aucun TODO/TBD ; chaque étape de code montre le code exact.

**Type consistency :** `color_grade_filter(grade:str)->str`, `grain_filter(amount:float)->str`, `glitch_amount(accents:dict)->float` cohérents entre définition (Tasks 2-4) et usage (Tasks 6-7). Clés config `color_grade`/`grain`/`clip_speed` identiques partout (Python et TS). `accents.glitch: boolean | number` cohérent front (Task 9) / coercion serveur (Task 8) / moteur (Task 4).
