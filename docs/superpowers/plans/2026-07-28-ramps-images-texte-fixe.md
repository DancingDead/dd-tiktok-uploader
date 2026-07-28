# Ramps de vitesse, images fixes, texte fixe — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter au moteur de montage trois leviers — des ralentis/accélérés calés sur les beats avec interpolation de frames, des images fixes montées en flash court, et une caption unique écrite à la main dont on règle position et taille.

**Architecture:** Tout passe par les points d'extension existants. `build_edl` (pure, seedée) décide de la vitesse et du choix d'asset par segment ; `_segment_filters` / `render` traduisent l'EDL en FFmpeg ; `DEFAULT_CONFIG` s'empile via `db.effective_config` (`DEFAULT_CONFIG ← settings.json ← preset`) et le bloc `subtitles` de la niche. Aucun nouveau module : la logique nouvelle prend la forme de petites fonctions pures placées à côté de leurs consommateurs, testables sans FFmpeg.

**Tech Stack:** Python 3 + uv, numpy, librosa (import paresseux), FFmpeg/ffprobe par `subprocess`, Flask (`webui.py`), SQLite (`db.py`), React + TypeScript + shadcn/ui (`frontend/`), pytest.

**Spec:** `docs/superpowers/specs/2026-07-28-ramps-images-texte-fixe-design.md`

## Global Constraints

- Commandes : `uv run pytest` (jamais `pip` — le venv n'a pas de module pip), `uv run python beatsync.py …`.
- **Reproductibilité** : même seed + même config → même vidéo. Trois garde-fous à ne pas casser : `load_clips` trie par nom, `build_edl` n'utilise que son `random.Random(seed)` local (jamais le RNG global), le rendu passe `-bitexact`.
- Les timestamps de cut sont quantifiés sur la grille de frames **dans `build_edl`**, jamais dans `render`.
- Commentaires et messages en **français**, comme tout le dépôt.
- `build_edl`, `ramp_speed`, `apply_subtitles`, `assign_caption_slots` restent **pures** : aucun I/O, aucun appel réseau.
- Pas de sur-ingénierie : aucun réglage exposé qui n'est pas dans le spec (en particulier **pas** de quota d'images, **pas** de RIFE).
- Branche de travail : `feat/ramps-images-texte-fixe` (déjà créée, le spec y est commité).
- Un commit par tâche, message en français, préfixe `feat:` / `test:` / `refactor:` selon le cas.

---

# VOLET 1 — Ramps de vitesse synchronisés au beat

### Task 1 : Règle de vitesse par segment (`ramp_speed`)

**Files:**
- Modify: `beatsync.py` — `DEFAULT_CONFIG` (~ligne 21-56), nouvelles fonctions après `merge_settings` (~ligne 73), `build_edl` (lignes 526 et 534-539)
- Test: `tests/test_speed_ramp.py` (créer)
- Modify (tests existants à mettre à jour) : `tests/test_build_edl_v2.py:90-104`, `tests/test_build_edl_v2.py:129-136`, `tests/test_beatsync_ambiance.py:164-199`

**Interfaces:**
- Consumes: rien (première tâche).
- Produces:
  - `beatsync.DEFAULT_CONFIG["speed_ramp"]` : `dict` avec les clés `slow` (float), `fast` (float), `impact_beats` (int), `min_dur` (float), `interpolate` (bool).
  - `beatsync.is_impact(beat_index: int, anchor: int, impact_beats: int) -> bool`
  - `beatsync.ramp_speed(start_beat: int, end_beat: int, duration: float, anchor: int, config: dict) -> float`
  - Chaque entrée d'EDL conserve sa clé `speed: float` (inchangée en nom et en type) ; la Task 2 lit `entry["speed"]` et `config["speed_ramp"]["interpolate"]`.

- [ ] **Step 1 : Écrire le test qui échoue**

Créer `tests/test_speed_ramp.py` :

```python
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
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_speed_ramp.py -v`
Expected: FAIL — `ImportError: cannot import name 'is_impact' from 'beatsync'`

- [ ] **Step 3 : Ajouter le bloc de config**

Dans `beatsync.py`, `DEFAULT_CONFIG`, juste après la ligne `"clip_speed": 1.0,` :

```python
    "speed_ramp": {                     # ramps calés sur les beats (interrupteur : effects.speed)
        "slow": 0.5,                    # segment d'anticipation (finit sur un impact), 0.5–1.0
        "fast": 1.4,                    # segment de relance (commence sur un impact), 1.0–1.5
        "impact_beats": 8,              # périodicité des impacts en beats ; 0 = pas de ramps
        "min_dur": 0.25,                # s : en dessous (strobo), pas de ramp
        "interpolate": True,            # flux optique sur les segments ralentis
    },
```

- [ ] **Step 4 : Écrire les fonctions pures**

Dans `beatsync.py`, après `merge_settings` (avant `SETTINGS_PATH`) :

```python
def _clamp_speed(value: float) -> float:
    """Borne moteur du slow-mo/accéléré. Défensif : l'UI n'impose pas de borne."""
    return max(0.5, min(1.5, float(value)))


def is_impact(beat_index: int, anchor: int, impact_beats: int) -> bool:
    """Un « impact » est un beat qui porte le motif de vitesse : l'ancre (le drop
    quand il existe) et tous les beats espacés d'un multiple d'`impact_beats`,
    avant comme après. `beat_index` négatif = borne de fenêtre, jamais un impact."""
    if beat_index < 0 or impact_beats <= 0:
        return False
    return (beat_index - anchor) % impact_beats == 0


def ramp_speed(start_beat: int, end_beat: int, duration: float,
               anchor: int, config: dict) -> float:
    """Vitesse d'un segment : ralenti d'anticipation s'il FINIT sur un impact,
    accéléré de relance s'il COMMENCE sur un impact, sinon `clip_speed`. Quand les
    deux s'appliquent, le ralenti gagne (l'anticipation prime). Pure, sans RNG :
    la reproductibilité ne dépend pas d'elle."""
    base = _clamp_speed(config.get("clip_speed", 1.0))
    ramp = config.get("speed_ramp") or {}
    if not config.get("effects", {}).get("speed"):
        return base
    if duration < float(ramp.get("min_dur", 0.25)):
        return base
    impact_beats = int(ramp.get("impact_beats", 8))
    if is_impact(end_beat, anchor, impact_beats):
        return _clamp_speed(ramp.get("slow", 0.5))
    if is_impact(start_beat, anchor, impact_beats):
        return _clamp_speed(ramp.get("fast", 1.4))
    return base
```

- [ ] **Step 5 : Brancher dans `build_edl`**

Dans `beatsync.py`, juste avant le commentaire `# --- Attribution des clips` (après le calcul de `drop_out`, ~ligne 514), ajouter :

```python
    # Ancre des impacts : le drop quand il existe, sinon le premier beat de la
    # fenêtre (mode calme) — le motif de vitesse reste actif dans les deux cas.
    impact_anchor = drop_idx if drop_idx is not None else (
        int(in_window[0]) if len(in_window) else 0)
```

Remplacer la ligne 526 (l'en-tête de boucle) par :

```python
    for (seg_start, beat_index), (seg_end, end_beat) in zip(boundaries, boundaries[1:]):
```

Remplacer le bloc « Gasp » (lignes 534-539) par :

```python
        # Ramps : ralenti avant un impact, accéléré après. Le « gasp » historique
        # avant le drop en est un cas particulier — le drop est un impact.
        speed = ramp_speed(beat_index, end_beat, duration, impact_anchor, config)
```

- [ ] **Step 6 : Lancer le test pour vérifier qu'il passe**

Run: `uv run pytest tests/test_speed_ramp.py -v`
Expected: PASS (16 tests)

- [ ] **Step 7 : Constater les régressions attendues sur la suite existante**

Run: `uv run pytest -q`
Expected: FAIL sur exactement 5 tests, tous parce que le motif de vitesse ne se limite plus au seul gasp :
`test_build_edl_v2.py::test_gasp_slowmo_on_last_buildup_segment`,
`test_build_edl_v2.py::test_no_drop_time_means_no_drop_section`,
`test_beatsync_ambiance.py::test_clip_speed_propagates_to_all_segments`,
`test_beatsync_ambiance.py::test_clip_speed_too_high_is_clamped_to_1_5`,
`test_beatsync_ambiance.py::test_clip_speed_negative_is_clamped_to_0_5`.

Si un **autre** test échoue, ne pas le « réparer » : c'est un vrai bug d'implémentation, revenir au Step 5.

- [ ] **Step 8 : Mettre à jour les tests existants**

Dans `tests/test_build_edl_v2.py`, remplacer `test_gasp_slowmo_on_last_buildup_segment` par :

```python
def test_gasp_slowmo_on_last_buildup_segment():
    """Le gasp avant le drop survit aux ramps : le drop est un impact, donc le
    segment qui s'y termine ralentit. Avec impact_beats très grand, il est même
    le SEUL segment ralenti (comportement historique)."""
    config = make_config(speed_ramp={**DEFAULT_CONFIG["speed_ramp"], "impact_beats": 10_000})
    edl = build_edl(make_analysis(), make_clips(), config, seed=42)
    before_drop = [e for e in edl if e["section"] == "buildup"]
    gasp = before_drop[-1]
    assert gasp["speed"] == pytest.approx(0.5)
    for entry in edl:
        if entry is not gasp:
            assert entry["speed"] == pytest.approx(1.0)
```

Vérifier que `DEFAULT_CONFIG` est importé en tête de `tests/test_build_edl_v2.py` ; si l'import est `from beatsync import build_edl`, l'étendre en `from beatsync import DEFAULT_CONFIG, build_edl`.

Dans le même fichier, `test_no_drop_time_means_no_drop_section` : remplacer la ligne
`config = make_config(drop_time=None)` par

```python
    # Ramps neutralisés : ce test porte sur les sections, pas sur la vitesse.
    config = make_config(drop_time=None,
                         speed_ramp={**DEFAULT_CONFIG["speed_ramp"], "impact_beats": 0})
```

Dans `tests/test_beatsync_ambiance.py`, les trois tests `test_clip_speed_*` : ajouter dans chacun des trois dicts de config, après la ligne `"drop_time": None,` :

```python
        # Ramps neutralisés : ces tests portent sur clip_speed et son clamp.
        "speed_ramp": {**DEFAULT_CONFIG["speed_ramp"], "impact_beats": 0},
```

- [ ] **Step 9 : Lancer toute la suite**

Run: `uv run pytest -q`
Expected: PASS, 0 échec

- [ ] **Step 10 : Commit**

```bash
git add beatsync.py tests/test_speed_ramp.py tests/test_build_edl_v2.py tests/test_beatsync_ambiance.py
git commit -m "feat(montage): ramps de vitesse calés sur les beats

Le gasp avant le drop devient un cas particulier d'une règle générale :
un segment qui finit sur un impact ralentit, celui qui commence dessus
accélère. Impacts = le drop (ou le premier beat de la fenêtre) et ses
multiples d'impact_beats. Segments plus courts que min_dur exemptés."
```

---

### Task 2 : Interpolation de frames (flux optique) sur les ralentis

**Files:**
- Modify: `beatsync.py` — `_segment_filters` (~ligne 976, la construction de `post`)
- Test: `tests/test_speed_ramp.py` (ajouter une section)

**Interfaces:**
- Consumes: `entry["speed"]` et `config["speed_ramp"]["interpolate"]` (Task 1).
- Produces: rien de nouveau — `_segment_filters(entry, config) -> list[str]` garde sa signature.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à la fin de `tests/test_speed_ramp.py` :

```python
# --- Flux optique au rendu --------------------------------------------------

from beatsync import _segment_filters  # noqa: E402


def seg(speed):
    return {"timeline_start": 0.0, "duration": 1.0, "clip_path": "/clips/a.mp4",
            "clip_in": 0.0, "speed": speed, "effects": [], "layout": "crop",
            "focus_x": 0.5, "clip_w": 1920, "clip_h": 1080}


def test_slowed_segment_gets_optical_flow_interpolation():
    joined = " ".join(_segment_filters(seg(0.5), DEFAULT_CONFIG))
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
    joined = " ".join(_segment_filters(seg(0.5), config))
    assert "minterpolate" not in joined
    assert "fps=30" in joined


def test_interpolation_applies_to_every_layout():
    for layout in ("crop", "split", "blur"):
        entry = {**seg(0.5), "layout": layout}
        assert "minterpolate" in " ".join(_segment_filters(entry, DEFAULT_CONFIG))
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_speed_ramp.py -k interpolation -v`
Expected: FAIL — `assert 'minterpolate=fps=30:mi_mode=mci' in ...`

- [ ] **Step 3 : Implémenter**

Dans `beatsync.py`, `_segment_filters`, remplacer la ligne :

```python
    post = [f"fps={fps}"]
```

par :

```python
    # Flux optique sur les ralentis : minterpolate invente les images manquantes
    # entre les images réelles. Placé ici — donc APRÈS le setpts (dans `pre`) et
    # APRÈS le scale/crop — il travaille sur du 1080x1920 déjà cadré plutôt que
    # sur la source, et sert les trois layouts sans duplication. Coûteux (5 à 15x
    # le temps d'encodage du segment) : réservé aux segments ralentis, désactivable.
    if speed < 1.0 and (config.get("speed_ramp") or {}).get("interpolate"):
        post = [f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"]
    else:
        post = [f"fps={fps}"]
```

- [ ] **Step 4 : Lancer les tests**

Run: `uv run pytest tests/test_speed_ramp.py -v && uv run pytest -q`
Expected: PASS partout

- [ ] **Step 5 : Vérifier sur un rendu réel**

Run: `uv run python beatsync.py --help`
Puis un rendu court sur des médias existants du dépôt (`tracks/` et `clips/` sont remplis en local) :
`uv run python beatsync.py --duration 8 --output /tmp/ramp-test.mp4`
Expected: le rendu aboutit ; noter le temps écoulé. Relancer avec un `settings.json` où `speed_ramp.interpolate` est `false` pour comparer — l'écart de durée confirme que l'interpolation tourne bien. Consigner les deux durées dans le message de commit.

- [ ] **Step 6 : Commit**

```bash
git add beatsync.py tests/test_speed_ramp.py
git commit -m "feat(rendu): flux optique sur les segments ralentis

minterpolate (mci/aobmc/bidir/vsbmc) remplace le fps= simple quand
speed < 1, après le scale/crop pour travailler sur du 1080x1920 déjà
cadré. Réservé aux ralentis et désactivable : le filtre coûte 5 à 15x
le temps d'encodage du segment."
```

---

# VOLET 2 — Images fixes dans le catalogue de clips

### Task 3 : `load_clips` accepte les images, `scan_clips` les ignore

**Files:**
- Modify: `beatsync.py` — `VIDEO_EXTENSIONS` (ligne 19), `load_clips` (lignes 103-131), `scan_clips` (ligne 297)
- Test: `tests/test_images.py` (créer)

**Interfaces:**
- Consumes: rien.
- Produces:
  - `beatsync.IMAGE_EXTENSIONS: set[str]` = `{".jpg", ".jpeg", ".png", ".webp"}`
  - `beatsync.IMAGE_MAX_DUR: float` = `0.6` (constante de module, **pas** un champ de config)
  - Chaque dict retourné par `load_clips` porte désormais `"kind": "video" | "image"` ; pour une image, `"duration"` vaut `None`.
  - `scan_clips` laisse les images **sans** clé `intervals` (sémantique existante : clé absente = asset entièrement utilisable).

- [ ] **Step 1 : Écrire le test qui échoue**

Créer `tests/test_images.py` :

```python
"""Images fixes dans le catalogue de clips : chargement, scan, sélection au
montage, rendu. ffprobe est mocké — aucun média réel requis."""

import json
import subprocess
from pathlib import Path

import pytest

import beatsync
from beatsync import IMAGE_EXTENSIONS, IMAGE_MAX_DUR, load_clips, scan_clips


@pytest.fixture
def fake_ffprobe(monkeypatch):
    """ffprobe rendu déterministe : 1920x1080, 90 s pour tout le monde. La durée
    de format est absente pour une image, comme le vrai ffprobe sur un PNG."""
    def run(cmd, **kwargs):
        path = Path(cmd[-1])
        payload = {"streams": [{"width": 1920, "height": 1080}], "format": {}}
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            payload["format"]["duration"] = "90.0"
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
    monkeypatch.setattr(beatsync.subprocess, "run", run)


def make_catalog(tmp_path, names):
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    return tmp_path


def test_load_clips_accepts_images_alongside_videos(tmp_path, fake_ffprobe):
    folder = make_catalog(tmp_path, ["a.mp4", "b.png", "c.jpg", "notes.txt"])
    clips = load_clips(folder)
    assert [c["path"].name for c in clips] == ["a.mp4", "b.png", "c.jpg"]  # trié par nom


def test_images_are_tagged_and_have_no_duration(tmp_path, fake_ffprobe):
    clips = load_clips(make_catalog(tmp_path, ["a.mp4", "b.png"]))
    by_name = {c["path"].name: c for c in clips}
    assert by_name["a.mp4"]["kind"] == "video"
    assert by_name["a.mp4"]["duration"] == pytest.approx(90.0)
    assert by_name["b.png"]["kind"] == "image"
    assert by_name["b.png"]["duration"] is None
    assert by_name["b.png"]["ratio"] == pytest.approx(1920 / 1080)


def test_empty_folder_still_raises(tmp_path, fake_ffprobe):
    with pytest.raises(ValueError, match="aucun clip ni image"):
        load_clips(make_catalog(tmp_path, ["notes.txt"]))


def test_scan_skips_images(tmp_path, fake_ffprobe, monkeypatch):
    """Une image n'a rien à décoder : elle ressort SANS clé `intervals`, ce qui
    la rend entièrement utilisable (sémantique d'un clip non scanné)."""
    scanned = []
    monkeypatch.setattr(beatsync, "_scan_one",
                        lambda clip: (scanned.append(clip["path"].name),
                                      clip.update(intervals=[{"start": 0.0, "end": 9.0,
                                                              "motion": 0.5, "presence": 1.0}])))
    clips = load_clips(make_catalog(tmp_path, ["a.mp4", "b.png"]))
    scan_clips(clips)
    by_name = {c["path"].name: c for c in clips}
    assert scanned == ["a.mp4"]
    assert "intervals" not in by_name["b.png"]
    assert "intervals" in by_name["a.mp4"]


def test_image_max_dur_is_a_short_flash():
    assert 0.0 < IMAGE_MAX_DUR <= 1.0
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_images.py -v`
Expected: FAIL — `ImportError: cannot import name 'IMAGE_EXTENSIONS' from 'beatsync'`

- [ ] **Step 3 : Implémenter**

Dans `beatsync.py`, sous `VIDEO_EXTENSIONS` (ligne 19) :

```python
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
# Plafond de durée d'une image au montage : au-delà, un fixe casse le rythme.
# Constante de module, pas un réglage : le catalogue d'images ne s'expose pas.
IMAGE_MAX_DUR = 0.6
```

Dans `load_clips`, remplacer le filtre d'extension et la construction du dict :

```python
    for path in sorted(Path(folder).iterdir()):
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            kind = "image"
        elif suffix in VIDEO_EXTENSIONS:
            kind = "video"
        else:
            continue
```

puis, après le `json.loads` :

```python
        clips.append(
            {
                "path": path,
                "kind": kind,
                # Une image n'a pas de durée : ffprobe n'en donne pas et le
                # montage la boucle sur la durée du segment.
                "duration": None if kind == "image" else float(info["format"]["duration"]),
                "width": width,
                "height": height,
                "ratio": width / height,
            }
        )
```

et le message d'erreur final :

```python
        raise ValueError(
            f"aucun clip ni image ({', '.join(sorted(VIDEO_EXTENSIONS | IMAGE_EXTENSIONS))}) "
            f"dans {folder}"
        )
```

Dans `scan_clips`, en tête de la boucle `for clip in clips:` :

```python
        if clip.get("kind") == "image":
            continue  # rien à décoder ; sans clé `intervals` l'image est utilisable en entier
```

- [ ] **Step 4 : Lancer les tests**

Run: `uv run pytest tests/test_images.py -v && uv run pytest -q`
Expected: PASS partout

- [ ] **Step 5 : Commit**

```bash
git add beatsync.py tests/test_images.py
git commit -m "feat(catalogue): load_clips accepte les images fixes

Les images du catalogue clips/ sont chargées avec kind=image et
duration=None, et sautées par scan_clips : sans clé intervals, elles
sont entièrement utilisables, comme un clip non scanné."
```

---

### Task 4 : Sélection des images dans `build_edl`

**Files:**
- Modify: `beatsync.py` — `build_edl` (bloc « Attribution des clips », lignes 521-637)
- Test: `tests/test_images.py` (ajouter une section)

**Interfaces:**
- Consumes: `clip["kind"]`, `IMAGE_MAX_DUR` (Task 3) ; `ramp_speed` (Task 1).
- Produces: une entrée d'EDL d'image porte `kind: "image"`, `clip_in: 0.0`, `speed: 1.0`, `"kenburns"` dans `effects`, et une clé `kenburns: {"zoom_dir": 1 | -1, "pan_dir": 1 | -1}`. Les entrées vidéo portent `kind: "video"`. La Task 5 lit ces clés.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_images.py` :

```python
# --- Sélection au montage ---------------------------------------------------

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


def video(name, duration=90.0):
    return {"path": Path(f"/clips/{name}"), "kind": "video", "duration": duration,
            "width": 1920, "height": 1080, "ratio": 1920 / 1080}


def image(name, width=1920, height=1080):
    return {"path": Path(f"/clips/{name}"), "kind": "image", "duration": None,
            "width": width, "height": height, "ratio": width / height}


def mixed_config(**overrides):
    return {**DEFAULT_CONFIG, "start": 0.0, "end": DURATION,
            "drop_time": 30.0, **overrides}


def build(clips, seed=42, **overrides):
    return build_edl(make_analysis(), clips, mixed_config(**overrides), seed=seed)


def test_images_are_used_only_on_short_segments():
    edl = build([video("a.mp4"), image("b.png")])
    used = [e for e in edl if e.get("kind") == "image"]
    assert used, "aucune image montée"
    assert all(e["duration"] <= IMAGE_MAX_DUR + 1e-9 for e in used)


def test_images_never_slow_down_or_speed_up():
    edl = build([video("a.mp4"), image("b.png")])
    assert all(e["speed"] == pytest.approx(1.0)
               for e in edl if e.get("kind") == "image")


def test_images_start_at_zero_and_get_kenburns():
    edl = build([video("a.mp4"), image("b.png")])
    for entry in edl:
        if entry.get("kind") == "image":
            assert entry["clip_in"] == pytest.approx(0.0)
            assert "kenburns" in entry["effects"]
            assert "zoom" not in entry["effects"], "kenburns fait déjà le zoom"
            assert entry["kenburns"]["zoom_dir"] in (1, -1)
            assert entry["kenburns"]["pan_dir"] in (1, -1)


def test_images_keep_a_minimum_gap_of_three_segments():
    """Sans garde-fou, un passage de strobo devient un diaporama."""
    edl = build([video("a.mp4"), image("b.png"), image("c.png"), image("d.png")])
    positions = [i for i, e in enumerate(edl) if e.get("kind") == "image"]
    assert positions, "aucune image montée"
    assert all(b - a >= 3 for a, b in zip(positions, positions[1:]))


def test_wide_image_falls_back_to_the_blurred_background_layout():
    edl = build([video("a.mp4"), image("wide.png", 1920, 1080)])
    used = [e for e in edl if e.get("kind") == "image"]
    assert used and all(e["layout"] == "blur" for e in used)


def test_portrait_image_is_cropped():
    edl = build([video("a.mp4"), image("tall.png", 1080, 1920)])
    used = [e for e in edl if e.get("kind") == "image"]
    assert used and all(e["layout"] == "crop" for e in used)


def test_image_selection_is_reproducible():
    clips = [video("a.mp4"), image("b.png"), image("c.png")]
    a = build(clips, seed=7)
    b = build(clips, seed=7)
    assert a == b


def test_video_only_catalog_is_unchanged():
    """Non-régression : sans image dans le catalogue, aucune clé nouvelle ne
    perturbe l'EDL et rien n'est marqué image."""
    edl = build([video("a.mp4"), video("b.mp4")])
    assert all(e["kind"] == "video" for e in edl)
    assert all("kenburns" not in e["effects"] for e in edl)
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_images.py -k "image or video_only" -v`
Expected: FAIL — `assert used, "aucune image montée"` (les images ne sont jamais choisies)

- [ ] **Step 3 : Séparer les pools avant la boucle**

Dans `beatsync.py`, `build_edl`, juste après la fonction interne `intervals_of` (~ligne 519) :

```python
    # Vidéos et images vivent dans le même catalogue mais ne se montent pas
    # pareil : une image n'a pas de plage exploitable, elle n'a qu'un plafond
    # de durée et un écart minimum entre deux apparitions.
    video_clips = [c for c in clips if c.get("kind", "video") != "image"]
    image_clips = [c for c in clips if c.get("kind") == "image"]
    IMAGE_MIN_GAP = 3          # segments entre deux images (anti-diaporama)
    last_image_seg = -IMAGE_MIN_GAP
```

Et transformer l'en-tête de boucle (celui posé en Task 1) en :

```python
    for seg_index, ((seg_start, beat_index), (seg_end, end_beat)) in enumerate(
            zip(boundaries, boundaries[1:])):
```

- [ ] **Step 4 : Élargir le pool aux images**

Remplacer le bloc `usable = [...]` / `if not usable: raise ...` (lignes 562-570) par :

```python
        usable = [
            c for c in video_clips
            if any(iv["end"] - iv["start"] >= source_needed for iv in intervals_of(c))
        ]
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

- [ ] **Step 5 : Brancher la branche image après le tirage**

Juste après `clip = rng.choice(pool)` (ligne 572), insérer :

```python
        if clip.get("kind") == "image":
            # Flash court : pas de plage à choisir, pas de ralenti sur un fixe.
            # Le Ken Burns (sens tirés à la seed) évite l'image figée ; le zoom
            # ordinaire serait redondant avec lui.
            last_image_seg = seg_index
            prev_path = clip["path"]
            edl.append(
                {
                    "timeline_start": seg_start,
                    "duration": duration,
                    "clip_path": clip["path"],
                    "kind": "image",
                    "clip_in": 0.0,
                    "beat_index": beat_index,
                    "section": section,
                    "speed": 1.0,
                    "effects": [e for e in effects if e != "zoom"] + ["kenburns"],
                    "kenburns": {"zoom_dir": rng.choice([1, -1]),
                                 "pan_dir": rng.choice([1, -1])},
                    # Le scan n'a pas tourné : layout déduit du seul ratio.
                    "focus_x": 0.5,
                    "layout": "blur" if clip["ratio"] >= 1.2 else "crop",
                    "clip_w": clip["width"],
                    "clip_h": clip["height"],
                }
            )
            continue
```

Enfin, dans le `edl.append({...})` existant (celui des vidéos, ligne 621), ajouter la clé `"kind": "video",` juste après `"clip_path": clip["path"],`.

- [ ] **Step 6 : Lancer les tests**

Run: `uv run pytest tests/test_images.py -v && uv run pytest -q`
Expected: PASS partout — y compris `test_same_seed_same_edl` et les tests de bornes de `test_build_edl_v2.py`, qui ne voient que des vidéos.

- [ ] **Step 7 : Commit**

```bash
git add beatsync.py tests/test_images.py
git commit -m "feat(montage): images montées en flash court avec Ken Burns

Une image n'est candidate que sur un segment <= IMAGE_MAX_DUR, jamais
deux fois à moins de 3 segments d'écart, toujours à vitesse 1.0 et avec
un Ken Burns dont les sens sont tirés à la seed. Layout déduit du ratio,
le scan n'ayant pas tourné dessus."
```

---

### Task 5 : Rendu d'une image (`-loop 1` + Ken Burns)

**Files:**
- Modify: `beatsync.py` — nouvelles fonctions avant `_segment_filters` (~ligne 954), `_segment_filters` (chaîne `post`), `render` (lignes 1063-1070)
- Test: `tests/test_images.py` (ajouter une section)

**Interfaces:**
- Consumes: `entry["kind"]`, `entry["kenburns"]`, `entry["effects"]` (Task 4).
- Produces:
  - `beatsync._segment_input_args(entry: dict) -> list[str]`
  - `beatsync.kenburns_filter(entry: dict, config: dict) -> str`

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_images.py` :

```python
# --- Rendu ------------------------------------------------------------------

from beatsync import _segment_filters, _segment_input_args, kenburns_filter  # noqa: E402


def image_entry(**overrides):
    return {"timeline_start": 0.0, "duration": 0.5, "clip_path": Path("/clips/b.png"),
            "kind": "image", "clip_in": 0.0, "speed": 1.0,
            "effects": ["kenburns"], "kenburns": {"zoom_dir": 1, "pan_dir": 1},
            "focus_x": 0.5, "layout": "crop", "clip_w": 1920, "clip_h": 1080,
            **overrides}


def video_entry(**overrides):
    return {"timeline_start": 0.0, "duration": 0.5, "clip_path": Path("/clips/a.mp4"),
            "kind": "video", "clip_in": 12.0, "speed": 1.0, "effects": [],
            "focus_x": 0.5, "layout": "crop", "clip_w": 1920, "clip_h": 1080,
            **overrides}


def test_image_input_is_looped_without_seek():
    args = _segment_input_args(image_entry())
    assert args[:2] == ["-loop", "1"]
    assert "-ss" not in args
    assert args[-2:] == ["-i", "/clips/b.png"]


def test_video_input_seeks_before_the_input():
    args = _segment_input_args(video_entry())
    assert args[0] == "-ss" and args[1].startswith("12.0")
    assert "-loop" not in args


def test_video_input_accounts_for_speed():
    """Un segment accéléré consomme plus de source : duration x speed."""
    args = _segment_input_args(video_entry(duration=2.0, speed=1.4))
    assert float(args[3]) == pytest.approx(2.0 * 1.4 + 0.5)


def test_kenburns_zoom_direction_changes_the_expression():
    zoom_in = kenburns_filter(image_entry(kenburns={"zoom_dir": 1, "pan_dir": 1}),
                              DEFAULT_CONFIG)
    zoom_out = kenburns_filter(image_entry(kenburns={"zoom_dir": -1, "pan_dir": 1}),
                               DEFAULT_CONFIG)
    assert zoom_in.startswith("zoompan=") and zoom_out.startswith("zoompan=")
    assert zoom_in != zoom_out


def test_kenburns_pan_direction_changes_the_expression():
    left = kenburns_filter(image_entry(kenburns={"zoom_dir": 1, "pan_dir": -1}),
                           DEFAULT_CONFIG)
    right = kenburns_filter(image_entry(kenburns={"zoom_dir": 1, "pan_dir": 1}),
                            DEFAULT_CONFIG)
    assert left != right


def test_segment_filters_apply_kenburns_to_an_image():
    joined = " ".join(_segment_filters(image_entry(), DEFAULT_CONFIG))
    assert "zoompan=" in joined


def test_segment_filters_leave_videos_untouched():
    joined = " ".join(_segment_filters(video_entry(), DEFAULT_CONFIG))
    assert "zoompan=" not in joined
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_images.py -k "input or kenburns" -v`
Expected: FAIL — `ImportError: cannot import name '_segment_input_args' from 'beatsync'`

- [ ] **Step 3 : Écrire les deux fonctions**

Dans `beatsync.py`, juste avant `_segment_filters` :

```python
def _segment_input_args(entry: dict) -> list[str]:
    """Arguments d'entrée FFmpeg d'un segment. Une image est bouclée (`-loop 1`)
    et n'a pas de point d'entrée ; une vidéo est seekée AVANT `-i` (seek rapide).
    Le rab de 0,5 s absorbe l'imprécision du seek : `-frames:v` coupe pile."""
    source_needed = entry["duration"] * entry.get("speed", 1.0)
    path = str(entry["clip_path"])
    if entry.get("kind") == "image":
        return ["-loop", "1", "-t", f"{source_needed + 0.5:.6f}", "-i", path]
    return ["-ss", f"{entry['clip_in']:.6f}", "-t", f"{source_needed + 0.5:.6f}",
            "-i", path]


def kenburns_filter(entry: dict, config: dict) -> str:
    """Zoom + pan lents sur une image fixe, pour qu'elle ne soit jamais figée.
    Les sens sont tirés à la seed dans build_edl : le filtre est déterministe."""
    width, height, fps = config["width"], config["height"], config["fps"]
    kb = entry.get("kenburns") or {}
    n = max(1, round(entry["duration"] * fps))
    z = f"1.02+0.10*on/{n}" if kb.get("zoom_dir", 1) > 0 else f"1.12-0.10*on/{n}"
    pan = 1 if kb.get("pan_dir", 1) > 0 else -1
    x = f"iw/2-(iw/zoom/2)+{pan}*(on/{n})*iw*0.04"
    return (f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={width}x{height}:fps={fps}")
```

- [ ] **Step 4 : Brancher dans `_segment_filters`**

Dans `_segment_filters`, juste après la construction de `post` (le bloc `minterpolate`/`fps` de la Task 2) et **avant** le bloc `if "zoom" in effects:` :

```python
    if "kenburns" in effects:
        post.append(kenburns_filter(entry, config))
```

- [ ] **Step 5 : Brancher dans `render`**

Dans `render`, remplacer les lignes de calcul et d'entrée :

```python
            n_frames = round(entry["duration"] * fps)
            source_needed = entry["duration"] * entry.get("speed", 1.0)
            _run_ffmpeg(
                [
                    "-ss", f"{entry['clip_in']:.6f}",  # avant -i : seek rapide
                    "-t", f"{source_needed + 0.5:.6f}",
                    "-i", str(entry["clip_path"]),
                    *_segment_filters(entry, config),
```

par :

```python
            n_frames = round(entry["duration"] * fps)
            _run_ffmpeg(
                [
                    *_segment_input_args(entry),
                    *_segment_filters(entry, config),
```

- [ ] **Step 6 : Lancer les tests**

Run: `uv run pytest tests/test_images.py -v && uv run pytest -q`
Expected: PASS partout

- [ ] **Step 7 : Vérifier sur un rendu réel**

Déposer une image dans `clips/` (n'importe quel PNG/JPG), puis :
`uv run python beatsync.py --duration 10 --output /tmp/image-test.mp4`
Expected: le rendu aboutit et l'image apparaît en flash court, avec un zoom perceptible. Si FFmpeg échoue sur le `zoompan`, lire le message : c'est presque toujours une expression `z`/`x` mal échappée.

- [ ] **Step 8 : Commit**

```bash
git add beatsync.py tests/test_images.py
git commit -m "feat(rendu): images bouclées en entrée + Ken Burns

_segment_input_args isole le choix des arguments d'entrée (-loop 1 sans
seek pour une image, -ss avant -i pour une vidéo) et devient testable
sans lancer FFmpeg. kenburns_filter construit le zoompan à sens fixés."
```

---

### Task 6 : Le catalogue accepte les images (API + UI)

**Files:**
- Modify: `webui.py` — `VIDEO_EXTS` (ligne 191) et ses usages (lignes 212, 301, 339, 363), `ASSET_MIMETYPES` (~ligne 49)
- Modify: `frontend/src/features/catalogue/Catalogue.tsx` (ligne 46, l'`accept` de la section Clips)
- Test: `tests/test_webui_platform.py` (ajouter)

**Interfaces:**
- Consumes: rien du moteur — le catalogue ne connaît que des extensions.
- Produces: `/api/clips` (POST/GET/DELETE) accepte `.jpg .jpeg .png .webp` en plus des vidéos ; `/api/state` liste les images dans `clips`.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_webui_platform.py` :

```python
def test_shared_clip_catalog_accepts_images(client, tmp_path):
    """Les images vivent dans clips/ comme les vidéos : même upload, même
    listing, même suppression."""
    upload = client.post("/api/clips", data={
        "file": (io.BytesIO(b"fake png"), "affiche.png")},
        content_type="multipart/form-data")
    assert upload.status_code == 200
    assert (tmp_path / "clips/affiche.png").is_file()

    names = [c["name"] for c in client.get("/api/state").get_json()["clips"]]
    assert "affiche.png" in names

    assert client.get("/api/clips/affiche.png").status_code == 200
    assert client.delete("/api/clips/affiche.png").status_code == 200
    assert not (tmp_path / "clips/affiche.png").is_file()


def test_shared_clip_catalog_still_rejects_unknown_formats(client):
    refused = client.post("/api/clips", data={
        "file": (io.BytesIO(b"nope"), "notes.txt")},
        content_type="multipart/form-data")
    assert refused.status_code == 400
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_webui_platform.py -k images -v`
Expected: FAIL — `assert 400 == 200` (format non supporté)

- [ ] **Step 3 : Implémenter côté serveur**

Dans `webui.py`, remplacer la ligne 191 par :

```python
    VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
    # Le catalogue « clips » contient les deux : une image se monte en flash court.
    CLIP_EXTS = VIDEO_EXTS | IMAGE_EXTS
```

Remplacer les quatre usages restants de `VIDEO_EXTS` par `CLIP_EXTS` :
- ligne ~212, le filtre du listing dans `/api/state` ;
- ligne ~301, `upload_clip` ;
- ligne ~339, `delete_clip_ep` ;
- ligne ~363, `serve_clip_ep`.

Ajouter à `ASSET_MIMETYPES` (~ligne 50) :

```python
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
```

- [ ] **Step 4 : Implémenter côté React**

Dans `frontend/src/features/catalogue/Catalogue.tsx`, section Clips (~ligne 46) :

```tsx
          accept=".mp4,.mov,.m4v,.mkv,.webm,.avi,.jpg,.jpeg,.png,.webp"
```

Et l'`emptyLabel` / le libellé de la section, si le texte dit « clips » seulement, devient « aucun clip ni image ». Ne pas toucher `AssetSection.tsx` : la table n'affiche que nom, taille et suppression — il n'y a pas d'aperçu à adapter.

- [ ] **Step 5 : Lancer les tests**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6 : Vérifier le build du frontend**

Run: `cd frontend && npm run build`
Expected: build sans erreur TypeScript

- [ ] **Step 7 : Commit**

```bash
git add webui.py frontend/src/features/catalogue/Catalogue.tsx tests/test_webui_platform.py
git commit -m "feat(catalogue): images acceptées dans clips/

Upload, listing, aperçu et suppression partagent désormais CLIP_EXTS
(vidéos + images). Aucun changement de sélection par niche : une image
est un asset de clips/ comme un autre."
```

---

# VOLET 3 — Texte fixe positionnable

### Task 7 : Mode « texte fixe » dans `apply_subtitles`

**Files:**
- Modify: `beatsync.py` — `DEFAULT_CONFIG["subtitles"]` (lignes 44-50), `_drawtext_escape` (lignes 679-684), `apply_subtitles` (lignes 865-879)
- Test: `tests/test_subtitles.py` (ajouter une section)

**Interfaces:**
- Consumes: rien des tâches précédentes.
- Produces: `config["subtitles"]` porte `mode` (`"llm" | "fixe"`), `text` (str), `x` (float), `y` (float), `size` (int). `apply_subtitles(edl, config, seed, cache_dir=None)` garde sa signature ; en mode `fixe` elle pose `entry["caption"]` sur **tous** les segments sans appeler le LLM. La Task 8 lit `x`, `y`, `size`.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_subtitles.py` :

```python
# --- Mode texte fixe --------------------------------------------------------


def fixed_config(**subs):
    return {**DEFAULT, "subtitles": {**DEFAULT["subtitles"],
                                     "enabled": True, "mode": "fixe", **subs}}


def test_fixed_mode_puts_the_same_caption_on_every_segment():
    edl = make_edl([0.0, 0.4, 0.8, 1.2, 1.6])
    out = apply_subtitles(edl, fixed_config(text="LIEN EN BIO"), seed=1)
    assert [e["caption"] for e in out] == ["LIEN EN BIO"] * 5


def test_fixed_mode_never_calls_the_llm(monkeypatch):
    """L'usine ne doit pas dépendre du LLM quand le texte est écrit à la main.
    On compte les appels plutôt que de lever : generate_punchlines rattrape
    Exception (dégradation en []), une AssertionError y serait avalée."""
    calls = []
    monkeypatch.setattr(beatsync, "_call_llm",
                        lambda pp, n, seed, model: (calls.append(1) or ["X"] * n))
    edl = make_edl([0.0, 0.4, 0.8])
    out = apply_subtitles(edl, fixed_config(text="DANCING DEAD"), seed=1)
    assert calls == []
    assert all(e["caption"] == "DANCING DEAD" for e in out)


def test_fixed_mode_disabled_leaves_the_edl_alone():
    edl = make_edl([0.0, 0.4])
    config = {**DEFAULT, "subtitles": {**DEFAULT["subtitles"],
                                       "enabled": False, "mode": "fixe", "text": "X"}}
    out = apply_subtitles(edl, config, seed=1)
    assert all("caption" not in e for e in out)


def test_unknown_mode_degrades_to_llm(monkeypatch):
    """Dégradation sûre : une valeur inattendue ne fait pas planter la génération."""
    monkeypatch.setattr(beatsync, "_call_llm", lambda *a, **k: ["A", "B", "C", "D"])
    edl = make_edl([0.0, 0.4, 1.8, 3.4])
    config = {**DEFAULT, "subtitles": {**DEFAULT["subtitles"],
                                       "enabled": True, "mode": "n'importe quoi",
                                       "preprompt": "test"}}
    out = apply_subtitles(edl, config, seed=1)
    assert all(e["caption"] for e in out)


# --- Échappement drawtext ---------------------------------------------------


def test_drawtext_escape_handles_newlines():
    """Un retour à la ligne réel casserait le parseur de filtergraph ; drawtext
    attend la séquence à deux caractères."""
    assert beatsync._drawtext_escape("HAUT\nBAS") == "HAUT\\nBAS"


def test_drawtext_escape_handles_quotes_and_specials():
    escaped = beatsync._drawtext_escape("c'est 100% : oui")
    for ch in ("'", "%", ":"):
        assert f"\\{ch}" in escaped
```

Vérifier en tête de `tests/test_subtitles.py` que `DEFAULT = beatsync.DEFAULT_CONFIG` existe déjà (c'est le cas) et que `apply_subtitles` est importé (c'est le cas).

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_subtitles.py -k "fixed or newlines" -v`
Expected: FAIL — les captions valent `""` (le mode est ignoré) et l'échappement laisse le retour à la ligne brut

- [ ] **Step 3 : Étendre la config**

Dans `beatsync.py`, `DEFAULT_CONFIG["subtitles"]`, ajouter après `"enabled": False,` :

```python
        "mode": "llm",                  # "llm" = punchlines générées | "fixe" = texte écrit à la main
        "text": "",                     # mode fixe : caption unique, du début à la fin
        "x": 0.5,                       # ancrage horizontal, fraction de largeur (texte centré dessus)
        "y": 0.74,                      # ancrage vertical, fraction de hauteur
        "size": 64,                     # taille de police, px
```

- [ ] **Step 4 : Corriger l'échappement des retours à la ligne**

Dans `_drawtext_escape`, après la boucle sur les caractères spéciaux et **avant** le `return` :

```python
    # Un retour à la ligne réel casse le parseur de filtergraph ; drawtext
    # interprète la séquence \n comme un saut de ligne. Fait en dernier : le
    # doublement des antislashs ci-dessus ne doit pas s'y appliquer.
    out = out.replace("\r\n", "\n").replace("\n", "\\n")
```

- [ ] **Step 5 : Ajouter la branche « fixe »**

Dans `apply_subtitles`, juste après le test `if not sub.get("enabled"): return edl` :

```python
    if sub.get("mode") == "fixe":
        # Caption unique écrite à la main : ni créneaux, ni LLM, ni cache.
        text = sub.get("text", "")
        for entry in edl:
            entry["caption"] = text
        return edl
```

- [ ] **Step 6 : Lancer les tests**

Run: `uv run pytest tests/test_subtitles.py -v && uv run pytest -q`
Expected: PASS partout

- [ ] **Step 7 : Commit**

```bash
git add beatsync.py tests/test_subtitles.py
git commit -m "feat(sous-titres): mode texte fixe, sans LLM

subtitles.mode = 'fixe' pose la même caption sur tous les segments et
n'appelle jamais le LLM. Toute autre valeur dégrade vers 'llm'.
_drawtext_escape convertit les retours à la ligne en séquence \\n :
un retour brut cassait le parseur de filtergraph."
```

---

### Task 8 : Position et taille du texte au rendu

**Files:**
- Modify: `beatsync.py` — le bloc `drawtext` de `_segment_filters` (lignes 994-1002)
- Test: `tests/test_subtitles.py` (ajouter)

**Interfaces:**
- Consumes: `config["subtitles"]["x" | "y" | "size"]` (Task 7).
- Produces: rien de nouveau — `_segment_filters` garde sa signature.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_subtitles.py` :

```python
def caption_entry():
    return {"timeline_start": 0.0, "duration": 1.0, "clip_path": "/clips/a.mp4",
            "clip_in": 0.0, "speed": 1.0, "effects": [], "layout": "crop",
            "focus_x": 0.5, "clip_w": 1920, "clip_h": 1080, "caption": "TEST"}


def test_caption_placement_comes_from_the_config():
    config = {**DEFAULT, "subtitles": {**DEFAULT["subtitles"],
                                       "x": 0.25, "y": 0.10, "size": 96}}
    joined = " ".join(_segment_filters(caption_entry(), config))
    assert "fontsize=96" in joined
    assert "x=w*0.2500-text_w/2" in joined
    assert "y=h*0.1000-text_h/2" in joined


def test_caption_defaults_keep_the_historical_placement():
    joined = " ".join(_segment_filters(caption_entry(), DEFAULT))
    assert "fontsize=64" in joined
    assert "x=w*0.5000-text_w/2" in joined


def test_generated_punchlines_use_the_same_placement_path():
    """Un seul chemin de code : le mode LLM hérite du réglage de placement."""
    config = {**DEFAULT, "subtitles": {**DEFAULT["subtitles"], "mode": "llm", "size": 40}}
    joined = " ".join(_segment_filters(caption_entry(), config))
    assert "fontsize=40" in joined


def test_no_caption_means_no_drawtext():
    entry = caption_entry()
    del entry["caption"]
    assert "drawtext" not in " ".join(_segment_filters(entry, DEFAULT))
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_subtitles.py -k caption -v`
Expected: FAIL — `assert 'fontsize=96' in ...` (la taille est codée en dur à 64)

- [ ] **Step 3 : Implémenter**

Dans `_segment_filters`, remplacer le bloc de la punchline :

```python
    # Punchline incrustée (après les accents pour rester nette). Position et
    # taille réglables : le texte est centré sur le point d'ancrage (x, y),
    # exprimé en fraction d'écran. Vaut pour les deux modes, généré et fixe.
    cap = entry.get("caption")
    subs = config.get("subtitles", {})
    font = resolve_caption_font(subs.get("font", "impact"))
    if cap and font:
        cap_x = max(0.0, min(1.0, float(subs.get("x", 0.5))))
        cap_y = max(0.0, min(1.0, float(subs.get("y", 0.74))))
        cap_size = max(8, int(subs.get("size", 64)))
        post.append(
            f"drawtext=fontfile={_drawtext_fontfile(font)}:text={_drawtext_escape(cap)}"
            f":fontsize={cap_size}:fontcolor=white:borderw=5:bordercolor=black@0.9"
            f":x=w*{cap_x:.4f}-text_w/2:y=h*{cap_y:.4f}-text_h/2"
        )
```

- [ ] **Step 4 : Lancer les tests**

Run: `uv run pytest tests/test_subtitles.py -v && uv run pytest -q`
Expected: PASS partout

- [ ] **Step 5 : Vérifier sur un rendu réel**

Run: `uv run python beatsync.py --duration 8 --subtitles "test" --output /tmp/caption-test.mp4`
Expected: le rendu aboutit et la punchline reste lisible, à peu près à la même hauteur qu'avant (l'ancrage passe du haut du texte à son centre : un léger décalage vers le haut est attendu et normal).

- [ ] **Step 6 : Commit**

```bash
git add beatsync.py tests/test_subtitles.py
git commit -m "feat(rendu): position et taille du texte réglables

drawtext lit x, y et size depuis subtitles au lieu de valeurs codées en
dur. Un seul chemin de code : les punchlines générées en héritent. Le
texte est centré sur son point d'ancrage, exprimé en fraction d'écran."
```

---

### Task 9 : API — validation du bloc `subtitles` de la niche

**Files:**
- Modify: `webui.py` — nouvelle fonction près de `coerce_overrides` (~ligne 55), endpoint `PATCH /api/niches/<id>` (lignes 509-518)
- Modify: `generate_niche.py` (ligne 96)
- Test: `tests/test_webui_platform.py` (ajouter)

**Interfaces:**
- Consumes: la forme de `subtitles` définie en Task 7.
- Produces: `webui.coerce_subtitles(subtitles: dict) -> dict` — force `x`, `y` en float bornés `[0, 1]`, `size` en int borné `[8, 200]`, valide `mode ∈ {"llm", "fixe"}`. Lève `ValueError`/`TypeError` si non convertible ; l'endpoint renvoie alors 400.

- [ ] **Step 1 : Écrire le test qui échoue**

Ajouter à `tests/test_webui_platform.py` :

```python
from webui import coerce_subtitles  # à ajouter à l'import existant de webui


def test_coerce_subtitles_forces_numeric_fields():
    out = coerce_subtitles({"mode": "fixe", "text": "SALUT", "x": "0.25",
                            "y": "0.1", "size": "80"})
    assert out["x"] == pytest.approx(0.25)
    assert out["y"] == pytest.approx(0.1)
    assert out["size"] == 80


def test_coerce_subtitles_clamps_out_of_range_values():
    out = coerce_subtitles({"x": 5.0, "y": -2.0, "size": 10_000})
    assert out["x"] == pytest.approx(1.0)
    assert out["y"] == pytest.approx(0.0)
    assert out["size"] == 200


def test_coerce_subtitles_rejects_unknown_mode():
    with pytest.raises(ValueError):
        coerce_subtitles({"mode": "magique"})


def test_coerce_subtitles_rejects_non_numeric():
    with pytest.raises(ValueError):
        coerce_subtitles({"size": "gros"})


def test_patch_niche_rejects_invalid_subtitles(client):
    nid = client.post("/api/niches", json={"name": "Test", "cadence": 1}).get_json()["id"]
    bad = client.patch(f"/api/niches/{nid}", json={"subtitles": {"size": "gros"}})
    assert bad.status_code == 400


def test_patch_niche_stores_a_fixed_caption(client):
    nid = client.post("/api/niches", json={"name": "Test", "cadence": 1}).get_json()["id"]
    ok = client.patch(f"/api/niches/{nid}", json={"subtitles": {
        "enabled": True, "mode": "fixe", "text": "LIEN EN BIO",
        "x": 0.5, "y": 0.2, "size": 72}})
    assert ok.status_code == 200
    subs = client.get("/api/state").get_json()["niches"][0]["subtitles"]
    assert subs["mode"] == "fixe"
    assert subs["text"] == "LIEN EN BIO"
    assert subs["size"] == 72
```

- [ ] **Step 2 : Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_webui_platform.py -k subtitles -v`
Expected: FAIL — `ImportError: cannot import name 'coerce_subtitles' from 'webui'`

- [ ] **Step 3 : Écrire la coercion**

Dans `webui.py`, après `coerce_overrides` :

```python
ALLOWED_SUBTITLE_MODES = {"llm", "fixe"}
SUBTITLE_RANGES = {"x": (0.0, 1.0), "y": (0.0, 1.0), "size": (8, 200)}


def coerce_subtitles(subtitles: dict) -> dict:
    """Force et borne les champs de placement du texte. Le bloc `subtitles` de la
    niche est un blob JSON écrit tel quel : une valeur non numérique ne casserait
    qu'au rendu FFmpeg, loin de la saisie. ValueError si non convertible."""
    coerced = dict(subtitles)
    for key, (lo, hi) in SUBTITLE_RANGES.items():
        if key in coerced:
            value = coerced[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                value = float(value)
            coerced[key] = max(lo, min(hi, value))
    if "size" in coerced:
        coerced["size"] = int(coerced["size"])
    if "mode" in coerced and coerced["mode"] not in ALLOWED_SUBTITLE_MODES:
        raise ValueError(f"mode de sous-titres inconnu : {coerced['mode']!r}")
    return coerced
```

- [ ] **Step 4 : Brancher dans l'endpoint**

Dans `webui.py`, `update_niche_ep` :

```python
    @app.patch("/api/niches/<int:niche_id>")
    def update_niche_ep(niche_id):
        fields = dict(request.json or {})
        conn = get_conn()
        try:
            if isinstance(fields.get("subtitles"), dict):
                fields["subtitles"] = coerce_subtitles(fields["subtitles"])
            dbmod.update_niche(conn, niche_id, **fields)
        except (TypeError, ValueError) as exc:
            return jsonify({"error": f"champ invalide : {exc}"}), 400
        finally:
            conn.close()
        return jsonify({"ok": True})
```

- [ ] **Step 5 : Enregistrer la caption fixe dans la bibliothèque**

Dans `generate_niche.py`, `generate_video` renvoie `info["captions"]` (vide en mode fixe puisque le LLM n'est pas appelé). Remplacer la ligne 96 :

```python
            subtitles={"lines": info["captions"]},
```

par :

```python
            # En mode fixe le LLM n'est pas appelé : on stocke le texte saisi pour
            # que la bibliothèque affiche la même caption que la vidéo.
            subtitles={"lines": info["captions"] or (
                [niche["subtitles"]["text"]]
                if niche["subtitles"].get("mode") == "fixe"
                and niche["subtitles"].get("text") else [])},
```

- [ ] **Step 6 : Lancer les tests**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 7 : Commit**

```bash
git add webui.py generate_niche.py tests/test_webui_platform.py
git commit -m "feat(api): validation du placement du texte fixe

coerce_subtitles force et borne x, y, size et refuse un mode inconnu :
le bloc subtitles de la niche est un blob JSON qui ne casserait qu'au
rendu, loin de la saisie. La bibliothèque affiche la caption fixe."
```

---

### Task 10 : UI — choix du mode et placement du texte

**Files:**
- Modify: `frontend/src/lib/api.ts` — type `Subtitles` (ligne 8)
- Modify: `frontend/src/features/niches/NicheDetail.tsx` — state (lignes 30-45), `save` (ligne 83), carte Punchlines (lignes 183-207)
- Test: build TypeScript + vérification manuelle (le frontend n'a pas de suite de tests dans ce dépôt)

**Interfaces:**
- Consumes: `PATCH /api/niches/<id>` avec `subtitles: {enabled, mode, preprompt, text, x, y, size}` (Task 9).
- Produces: rien pour les tâches suivantes (dernière tâche).

- [ ] **Step 1 : Étendre le type**

Dans `frontend/src/lib/api.ts`, ligne 8 :

```ts
export type Subtitles = {
  enabled?: boolean
  mode?: "llm" | "fixe"
  preprompt?: string
  text?: string
  x?: number
  y?: number
  size?: number
  lines?: string[]
}
```

- [ ] **Step 2 : Ajouter le state**

Dans `NicheDetail.tsx`, après la ligne `const [preprompt, setPreprompt] = useState(...)` :

```tsx
  const [subsMode, setSubsMode] = useState<"llm" | "fixe">(niche.subtitles?.mode ?? "llm")
  const [fixedText, setFixedText] = useState(niche.subtitles?.text ?? "")
  const [capX, setCapX] = useState(niche.subtitles?.x ?? 0.5)
  const [capY, setCapY] = useState(niche.subtitles?.y ?? 0.74)
  const [capSize, setCapSize] = useState(niche.subtitles?.size ?? 64)
```

Étendre le calcul de `dirty` (le repère « modifications non enregistrées ») en ajoutant :

```tsx
    subsMode !== (niche.subtitles?.mode ?? "llm") ||
    fixedText !== (niche.subtitles?.text ?? "") ||
    capX !== (niche.subtitles?.x ?? 0.5) ||
    capY !== (niche.subtitles?.y ?? 0.74) ||
    capSize !== (niche.subtitles?.size ?? 64) ||
```

- [ ] **Step 3 : Envoyer les champs à l'enregistrement**

Dans `save`, remplacer `subtitles: { enabled: subsEnabled, preprompt },` par :

```tsx
        subtitles: {
          enabled: subsEnabled,
          mode: subsMode,
          preprompt,
          text: fixedText,
          x: capX,
          y: capY,
          size: capSize,
        },
```

- [ ] **Step 4 : Écrire l'interface**

Dans la carte Punchlines, remplacer le libellé de la case et le bloc « Consigne de style » par :

```tsx
          <div className="space-y-3 border-t pt-4">
            <Label>Texte incrusté</Label>
            <label className="flex items-center gap-3 text-sm">
              <Checkbox
                checked={subsEnabled}
                onCheckedChange={(v) => setSubsEnabled(v === true)}
              />
              Incruster du texte dans la vidéo
            </label>

            {subsEnabled && (
              <>
                {/* Les deux modes s'excluent : soit le LLM écrit et le texte
                    change au fil de la vidéo, soit on fige une caption unique. */}
                <div className="flex gap-4 text-sm">
                  {(["llm", "fixe"] as const).map((m) => (
                    <label key={m} className="flex items-center gap-2">
                      <input
                        type="radio"
                        name="subs-mode"
                        checked={subsMode === m}
                        onChange={() => setSubsMode(m)}
                      />
                      {m === "llm" ? "Punchlines générées" : "Texte fixe"}
                    </label>
                  ))}
                </div>

                {subsMode === "llm" ? (
                  <div className="space-y-1">
                    <Label htmlFor="preprompt">Consigne de style</Label>
                    <Textarea
                      id="preprompt"
                      value={preprompt}
                      onChange={(e) => setPreprompt(e.target.value)}
                      placeholder="motivation gym, français, percutant, 4 mots max"
                    />
                    <p className="text-xs text-muted-foreground">
                      Guide le ton des punchlines. Ex. « motivation gym, français, percutant, 4 mots max ».
                    </p>
                  </div>
                ) : (
                  <div className="space-y-1">
                    <Label htmlFor="fixed-text">Texte</Label>
                    <Textarea
                      id="fixed-text"
                      value={fixedText}
                      onChange={(e) => setFixedText(e.target.value)}
                      placeholder="LIEN EN BIO"
                    />
                    <p className="text-xs text-muted-foreground">
                      Affiché à l'identique du début à la fin. Les retours à la ligne sont conservés.
                    </p>
                  </div>
                )}

                {/* Le placement vaut pour les deux modes. */}
                <div className="grid gap-3 sm:grid-cols-3">
                  <div className="space-y-1">
                    <Label htmlFor="cap-x">Position horizontale — {Math.round(capX * 100)} %</Label>
                    <input
                      id="cap-x" type="range" min={0} max={100} step={1}
                      value={Math.round(capX * 100)}
                      onChange={(e) => setCapX(Number(e.target.value) / 100)}
                      className="w-full"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="cap-y">Position verticale — {Math.round(capY * 100)} %</Label>
                    <input
                      id="cap-y" type="range" min={0} max={100} step={1}
                      value={Math.round(capY * 100)}
                      onChange={(e) => setCapY(Number(e.target.value) / 100)}
                      className="w-full"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="cap-size">Taille — {capSize} px</Label>
                    <input
                      id="cap-size" type="range" min={24} max={140} step={2}
                      value={capSize}
                      onChange={(e) => setCapSize(Number(e.target.value))}
                      className="w-full"
                    />
                  </div>
                </div>
                <p className="text-xs text-muted-foreground">
                  0 % = gauche / haut, 100 % = droite / bas. Le texte est centré sur ce point.
                </p>
              </>
            )}
          </div>
```

- [ ] **Step 5 : Vérifier le build**

Run: `cd frontend && npm run build`
Expected: build sans erreur TypeScript

- [ ] **Step 6 : Vérification manuelle bout-en-bout**

Run: `uv run python serve.py` (ou la commande de lancement habituelle), puis dans le navigateur :
1. ouvrir une niche, cocher « Incruster du texte », choisir « Texte fixe », saisir `LIEN EN BIO`, régler la position verticale à 20 % et la taille à 90 px, enregistrer ;
2. recharger la page → les réglages sont bien relus depuis la base ;
3. lancer une génération d'**une** variante et vérifier que la vidéo produite porte le texte à la position demandée.

Expected: les trois étapes passent. Si le texte n'apparaît pas, vérifier que `enabled` est bien à `true` — c'est l'interrupteur commun aux deux modes.

- [ ] **Step 7 : Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/features/niches/NicheDetail.tsx
git commit -m "feat(ui): choix punchlines générées / texte fixe + placement

La carte « Texte incrusté » de la niche propose les deux modes, exclusifs,
et trois curseurs de placement (X, Y en %, taille en px) valables pour
les deux."
```

---

### Task 11 : Documentation

**Files:**
- Modify: `CLAUDE.md` — sections `beatsync.py` (`build_edl`, `render`, punchlines) et `webui.py` (catalogue)

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: rien.

- [ ] **Step 1 : Mettre à jour la description de `build_edl`**

Dans `CLAUDE.md`, dans la puce `build_edl`, remplacer la mention du gasp par :

> `speed_ramp` : ralenti d'anticipation sur le segment qui finit sur un impact, accéléré de relance sur celui qui commence dessus (impacts = le drop, ou le premier beat de la fenêtre, et ses multiples d'`impact_beats`) ; segments plus courts que `min_dur` exemptés. Le « gasp » avant le drop en est un cas particulier. `ramp_speed`/`is_impact` sont **pures et testées**.

Ajouter dans la même puce :

> Images : un asset `kind: "image"` du catalogue n'est monté que sur un segment ≤ `IMAGE_MAX_DUR` (0,6 s), jamais deux fois à moins de 3 segments d'écart, à vitesse 1.0, avec un Ken Burns dont les sens sont tirés à la seed et un layout déduit du seul ratio (le scan ne tourne pas sur les images).

- [ ] **Step 2 : Mettre à jour `render`**

Ajouter à la puce `render` :

> Segments ralentis : `minterpolate` (flux optique) remplace le `fps=` simple quand `speed < 1` et `speed_ramp.interpolate`, après le cadrage — coûteux (5 à 15x l'encodage du segment), désactivable. Images : `_segment_input_args` boucle l'entrée (`-loop 1`, pas de seek).

- [ ] **Step 3 : Mettre à jour la puce punchlines et le catalogue**

Puce punchlines : préciser que `subtitles.mode` vaut `"llm"` (génération) ou `"fixe"` (caption unique écrite à la main, sans appel LLM), et que `x`, `y`, `size` règlent le placement pour **les deux** modes.

Puce `webui.py` : préciser que la section Clips du catalogue accepte aussi les images (`.jpg .jpeg .png .webp`), montées en flash court.

- [ ] **Step 4 : Vérifier**

Run: `uv run pytest -q`
Expected: PASS (aucun changement de code, contrôle final)

- [ ] **Step 5 : Commit**

```bash
git add CLAUDE.md
git commit -m "docs: ramps de vitesse, images fixes, texte fixe dans CLAUDE.md"
```

---

## Vérification finale

- [ ] `uv run pytest -q` — toute la suite passe
- [ ] `cd frontend && npm run build` — build sans erreur
- [ ] Un rendu réel avec les trois volets actifs : une niche qui sélectionne au moins une image, un preset avec `effects.speed` activé, et un texte fixe → la vidéo produite montre un ralenti fluide, un flash d'image et la caption au bon endroit
- [ ] Noter dans le message du dernier commit l'écart de temps de génération mesuré avec et sans `speed_ramp.interpolate` (relevé en Task 2, Step 5) — c'est le chiffre qui décidera du réglage par défaut sur la tour
