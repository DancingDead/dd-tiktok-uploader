# Recadrage sur le locuteur actif — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Le cadrage 9:16 du clipper tient le visage de celui qui parle et change de personne par une coupe franche, au lieu de suivre le plus grand visage en panoramique.

**Architecture :** Un nouveau module `speaker.py` porte tout ce qui touche au cadrage — géométrie déménagée depuis `clipper.py`, plus la détection de visages, le suivi de pistes, la mesure d'agitation de bouche et le découpage en plans. La dépendance est à sens unique : `clipper` importe `speaker`, jamais l'inverse. Le cœur est en logique pure et testable (pistes, filtres, timeline, segments) ; seules la lecture vidéo et la détection OpenCV sont en I/O.

**Tech Stack :** Python 3.11+, uv, OpenCV (déjà installé, cascade Haar frontale), FFmpeg via `subprocess`.

**Spec :** `docs/superpowers/specs/2026-08-08-locuteur-actif-design.md`

## Global Constraints

- **uv, jamais pip.** `uv run pytest` pour tester, `uv run python` pour lancer. Le venv n'a pas de module pip.
- **Tout en français** : code, commentaires, interface, messages de commit. Noms de fonctions et de variables en anglais.
- **Les commentaires expliquent le POURQUOI.** Chaque constante non évidente porte la raison de sa valeur — c'est le standard de `clipper.py` et `beatsync.py`, reproduis-le.
- **`import cv2` est paresseux** : dans la fonction qui l'utilise, jamais en tête de module. Motif de `beatsync.analyze_audio` avec librosa. Un test vérifie que `import speaker` reste sous 0,3 s.
- **Dépendance à sens unique** : `speaker.py` n'importe **rien** de `clipper.py`. `clipper.py` importe `speaker`. Un import croisé rendrait les deux modules inchargeables séparément.
- **Pureté et reproductibilité** : les fonctions marquées pures ne font aucune I/O, n'utilisent ni horloge ni RNG, et ne mutent pas leurs arguments. Deux exécutions identiques doivent donner exactement le même découpage.
- **Invariants de cadrage à ne pas casser** : les x sont des entiers **pairs** (`_even` — un offset impair produit des artefacts de chroma en yuv420p), bornés dans `[0, src_w - crop_w]`, et une trajectoire immobile rend une **constante** (sans quoi on demande une réévaluation par frame pour rien, et sur ffmpeg 8 l'option `eval` n'existe même plus — voir `clipper.crop_supports_eval`).
- **Constantes du détecteur**, valeurs imposées par la spec § 3 : `FRAME_FPS = 10.0`, `DETECT_EVERY = 0.5`, `DETECT_SCALE = 0.5`, `IOU_MIN = 0.3`, `MIN_FACE_FRACTION = 0.06`, `STATIC_TOLERANCE = 2`, `ACTIVITY_WINDOW = 0.6`, `SWITCH_MARGIN = 1.5`, `CUT_THRESHOLD = 0.35`.
- **Commits fréquents**, un par tâche minimum, format `feat(speaker): …` / `refactor(speaker): …` / `docs(speaker): …`.

---

## Structures de données

Trois formes traversent tout le module. Elles sont volontairement des `dict` simples, comme le reste du projet (les entrées d'EDL de beatsync, les mots de transcript du clipper) :

```python
# Un rectangle de visage, en pixels de la SOURCE (jamais de l'image réduite)
Box = {"x": int, "y": int, "w": int, "h": int}

# Une piste : un visage suivi d'image en image.
# `boxes` et `activity` sont indexés par NUMÉRO D'IMAGE échantillonnée (0, 1, 2…),
# pas par seconde. Une piste peut avoir des trous (visage momentanément perdu).
Track = {"id": int, "boxes": dict[int, Box], "activity": dict[int, float]}

# Un segment de cadrage : entre deux frontières, avec interpolation à l'intérieur.
Segment = {"start": float, "end": float, "x_start": int, "x_end": int}
```

## Structure des fichiers

**Créés :**

| Fichier | Responsabilité |
|---|---|
| `speaker.py` | Tout le cadrage : géométrie de crop, détection et suivi de visages, agitation de bouche, détection de coupes, découpage en plans. |
| `tests/test_speaker_geometry.py` | La géométrie déménagée (ancien `test_clipper_track.py`) |
| `tests/test_speaker_tracks.py` | `link_tracks`, `usable_tracks` |
| `tests/test_speaker_timeline.py` | `speaker_timeline`, `crop_segments`, `crop_expr` |

**Modifiés :**

| Fichier | Changement |
|---|---|
| `clipper.py` | perd la géométrie de cadrage, importe `speaker`, `render_clip` consomme des segments |
| `beatsync.py` | deux clés dans `DEFAULT_CONFIG["clipper"]` |
| `webui.py` | `CLIPPER_RANGES`, `coerce_clipper` apprend le booléen |
| `frontend/src/lib/api.ts`, `frontend/src/features/settings/SettingsTab.tsx` | deux champs de plus |
| `CLAUDE.md`, `docs/fiches/10-clipper.md` | documentation |
| `tests/test_clipper_track.py` | **supprimé**, remplacé par `tests/test_speaker_geometry.py` |

---

### Task 1 : Créer `speaker.py` en y déménageant la géométrie de cadrage

Déménagement pur, **sans aucun changement de comportement**. Le but est d'avoir un module d'accueil vide de dette avant d'y construire la détection.

**Files:**
- Create: `speaker.py`
- Create: `tests/test_speaker_geometry.py` (contenu de `tests/test_clipper_track.py`, imports changés)
- Delete: `tests/test_clipper_track.py`
- Modify: `clipper.py` (retire les fonctions déménagées, importe depuis `speaker`)

**Interfaces:**
- Consumes: rien
- Produces, dans `speaker.py`, à l'identique de ce qui existe aujourd'hui dans `clipper.py` :
  - `OUT_W = 1080`, `OUT_H = 1920`
  - `DEAD_ZONE = 0.08`, `SMOOTH_WINDOW = 5`, `MAX_STEPS = 120`
  - `_even(value: float) -> int`
  - `crop_size(src_w: int, src_h: int) -> tuple[int, int]`
  - `_fill_holes(centers: list[float | None], default: float) -> list[float]`
  - `smooth_track(centers: list[float | None], default: float, dead_zone: float) -> list[float]`
  - `_ramp(t0: float, x0: int, t1: float, x1: int) -> str`
  - `crop_expr(track: list[float], sample_fps: float, crop_w: int, src_w: int) -> str` (signature inchangée à ce stade ; la Task 5 la remplacera)

- [ ] **Step 1 : Créer `speaker.py` avec le code déménagé**

Crée `speaker.py` avec cet en-tête, puis **copie telles quelles** depuis `clipper.py` les constantes `OUT_W`, `OUT_H`, `DEAD_ZONE`, `SMOOTH_WINDOW`, `MAX_STEPS` et les fonctions `_even`, `crop_size`, `_fill_holes`, `smooth_track`, `_ramp`, `crop_expr` — **avec leurs commentaires et leurs docstrings**, sans en modifier une ligne.

```python
"""speaker — cadrage du clipper : qui tient l'image, et comment on la découpe.

Deux sujets, une seule responsabilité : décider QUEL visage occupe le cadre à
chaque instant, et traduire cette décision en géométrie de crop pour FFmpeg.

La dépendance est à sens unique : `clipper` importe `speaker`, jamais l'inverse.
Un import croisé rendrait les deux modules inchargeables séparément, et c'est
`speaker` qui est le plus bas niveau des deux.

Comme dans `clipper`, le cœur est pur et testable — pistes, filtres, timeline,
segments — et seules la lecture vidéo et la détection OpenCV font de l'I/O.
"""

from pathlib import Path
```

`OUT_W`/`OUT_H` déménagent ici parce que ce sont des dimensions de **sortie**, donc de cadrage. `clipper` les réimportera : `build_ass` s'en sert pour `PlayResX`/`PlayResY`.

- [ ] **Step 2 : Retirer le code déménagé de `clipper.py` et l'importer**

Supprime de `clipper.py` les six fonctions et les cinq constantes ci-dessus, et remplace-les par un import en tête de module, à côté des autres imports standard :

```python
from speaker import (DEAD_ZONE, MAX_STEPS, OUT_H, OUT_W, crop_expr, crop_size,
                     smooth_track)
```

`_even`, `_fill_holes` et `_ramp` sont privées et ne sont pas utilisées hors de `speaker` — ne les réexporte pas.

Vérifie qu'il ne reste aucune référence :

```bash
grep -n "_even\|_fill_holes\|_ramp" clipper.py
```

Attendu : aucune ligne.

- [ ] **Step 3 : Déplacer le fichier de tests**

```bash
git mv tests/test_clipper_track.py tests/test_speaker_geometry.py
```

Puis, dans le fichier déplacé, remplace les imports depuis `clipper` par des imports depuis `speaker` :

```python
from speaker import MAX_STEPS, _fill_holes, crop_expr, crop_size, smooth_track
```

Ne touche à **aucun** test : le comportement ne change pas, donc les assertions non plus.

- [ ] **Step 4 : Lancer la suite complète**

```bash
uv run pytest -q
```

Attendu : 535 passés, exactement comme avant le déménagement. Si un test échoue, c'est que le déménagement a modifié quelque chose — reviens en arrière plutôt que d'ajuster le test.

- [ ] **Step 5 : Vérifier qu'aucun import lourd n'a fui, et qu'il n'y a pas de cycle**

```bash
uv run python -c "import time; t=time.time(); import speaker; print(f'speaker: {time.time()-t:.2f}s')"
uv run python -c "import time; t=time.time(); import clipper; print(f'clipper: {time.time()-t:.2f}s')"
uv run python -c "import speaker; import clipper; print('pas de cycle')"
```

Attendu : les deux imports sous 0,3 s, et « pas de cycle ».

- [ ] **Step 6 : Commit**

```bash
git add speaker.py clipper.py tests/test_speaker_geometry.py
git commit -m "refactor(speaker): sort la geometrie de cadrage de clipper.py"
```

---

### Task 2 : `link_tracks` — relier les détections en pistes

C'est le maillon sans lequel rien d'autre n'est possible : « qui parle » suppose une **identité** d'une image à l'autre, alors que la détection ne produit que des rectangles indépendants.

**Files:**
- Modify: `speaker.py`
- Test: `tests/test_speaker_tracks.py`

**Interfaces:**
- Consumes: rien
- Produces:
  - `speaker.IOU_MIN = 0.3`
  - `speaker.iou(a: dict, b: dict) -> float` — recouvrement de deux rectangles, 0.0 à 1.0
  - `speaker.link_tracks(detections: list[list[dict]], iou_min: float = IOU_MIN) -> list[dict]`

`detections[i]` est la liste des rectangles détectés sur l'image échantillonnée `i`. Le retour est une liste de pistes `{"id": int, "boxes": {i: box}, "activity": {}}`, triées par `id` croissant, `id` étant attribué dans l'ordre d'apparition.

- [ ] **Step 1 : Écrire les tests qui échouent**

Crée `tests/test_speaker_tracks.py` :

```python
import pytest

from speaker import iou, link_tracks


def b(x, y=100, w=100, h=100):
    return {"x": x, "y": y, "w": w, "h": h}


def test_iou_rectangles_identiques():
    assert iou(b(0), b(0)) == pytest.approx(1.0)


def test_iou_rectangles_disjoints():
    assert iou(b(0), b(500)) == pytest.approx(0.0)


def test_iou_recouvrement_partiel():
    # deux carrés de 100, décalés de 50 : intersection 50x100, union 150x100
    assert iou(b(0), b(50)) == pytest.approx(50 * 100 / (150 * 100))


def test_une_piste_suivie_sur_trois_images():
    tracks = link_tracks([[b(100)], [b(105)], [b(110)]])
    assert len(tracks) == 1
    assert tracks[0]["boxes"] == {0: b(100), 1: b(105), 2: b(110)}


def test_deux_visages_gardent_leur_identite():
    tracks = link_tracks([[b(100), b(800)], [b(110), b(810)]])
    assert len(tracks) == 2
    gauche = next(t for t in tracks if t["boxes"][0]["x"] == 100)
    droite = next(t for t in tracks if t["boxes"][0]["x"] == 800)
    assert gauche["boxes"][1]["x"] == 110
    assert droite["boxes"][1]["x"] == 810


def test_une_piste_qui_disparait_puis_revient_garde_son_id():
    """La cascade rate un visage sur une image (tête tournée) : ce n'est pas une
    nouvelle personne quand il réapparaît au même endroit."""
    tracks = link_tracks([[b(100)], [], [b(105)]])
    assert len(tracks) == 1
    assert set(tracks[0]["boxes"]) == {0, 2}


def test_deux_pistes_qui_se_croisent_n_echangent_pas_d_identite():
    """Les deux visages se rapprochent sans se recouvrir : chacun doit rester
    apparié au plus recouvrant, pas au premier venu."""
    tracks = link_tracks([[b(0), b(400)], [b(40), b(360)]])
    assert len(tracks) == 2
    gauche = next(t for t in tracks if t["boxes"][0]["x"] == 0)
    assert gauche["boxes"][1]["x"] == 40


def test_un_visage_qui_saute_trop_loin_ouvre_une_nouvelle_piste():
    """Recouvrement nul : c'est un autre visage, pas le même qui a bondi."""
    tracks = link_tracks([[b(0)], [b(900)]])
    assert len(tracks) == 2


def test_aucune_detection():
    assert link_tracks([[], [], []]) == []


def test_liste_vide():
    assert link_tracks([]) == []


def test_les_pistes_portent_un_dictionnaire_d_agitation_vide():
    """`activity` est rempli plus tard par la couche d'I/O ; la structure doit
    déjà exister pour que `usable_tracks` puisse la lire sans garde."""
    tracks = link_tracks([[b(100)]])
    assert tracks[0]["activity"] == {}


def test_ids_attribues_dans_l_ordre_d_apparition():
    tracks = link_tracks([[b(100)], [b(100), b(800)]])
    assert [t["id"] for t in tracks] == [0, 1]
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

```bash
uv run pytest tests/test_speaker_tracks.py -v
```

Attendu : `ImportError: cannot import name 'iou' from 'speaker'`.

- [ ] **Step 3 : Implémenter**

Ajoute dans `speaker.py` :

```python
# --- Suivi de visages -------------------------------------------------------

# Recouvrement minimal pour considérer que deux rectangles d'images successives
# sont le même visage. En dessous, deux visages voisins finiraient reliés dans
# la même piste — et le cadre passerait de l'un à l'autre sans qu'aucune coupe
# ne soit décidée.
IOU_MIN = 0.3


def iou(a: dict, b: dict) -> float:
    """Recouvrement de deux rectangles, rapporté à leur union. Pure."""
    x1 = max(a["x"], b["x"])
    y1 = max(a["y"], b["y"])
    x2 = min(a["x"] + a["w"], b["x"] + b["w"])
    y2 = min(a["y"] + a["h"], b["y"] + b["h"])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def link_tracks(detections: list[list[dict]], iou_min: float = IOU_MIN) -> list[dict]:
    """Relie les détections image par image en pistes persistantes.

    Une piste non appariée sur une image n'est pas close : la cascade rate
    régulièrement un visage (tête tournée, flou de mouvement), et rouvrir une
    piste ferait passer la même personne pour une nouvelle — donc une coupe
    injustifiée. Elle reste donc candidate à l'appariement sur sa DERNIÈRE
    position connue. Pure."""
    tracks: list[dict] = []
    for index, boxes in enumerate(detections):
        # Appariement glouton par recouvrement décroissant : le meilleur couple
        # est figé d'abord, ce qui évite qu'un visage vole l'appariement d'un
        # autre quand deux personnes se rapprochent.
        pairs = sorted(
            ((iou(track["boxes"][max(track["boxes"])], box), t, d)
             for t, track in enumerate(tracks)
             for d, box in enumerate(boxes)),
            key=lambda p: (-p[0], p[1], p[2]))
        used_tracks: set[int] = set()
        used_boxes: set[int] = set()
        for score, t, d in pairs:
            if score < iou_min or t in used_tracks or d in used_boxes:
                continue
            tracks[t]["boxes"][index] = boxes[d]
            used_tracks.add(t)
            used_boxes.add(d)
        for d, box in enumerate(boxes):
            if d not in used_boxes:
                tracks.append({"id": len(tracks), "boxes": {index: box},
                               "activity": {}})
    return tracks
```

- [ ] **Step 4 : Lancer les tests, vérifier qu'ils passent**

```bash
uv run pytest tests/test_speaker_tracks.py -v && uv run pytest -q
```

Attendu : 12 nouveaux tests PASS, suite complète verte.

- [ ] **Step 5 : Commit**

```bash
git add speaker.py tests/test_speaker_tracks.py
git commit -m "feat(speaker): relie les detections de visages en pistes"
```

---

### Task 3 : `usable_tracks` — écarter ce qui n'est pas un interlocuteur

Sur la source d'essai, trois « visages » de ~70 px sont figés au bord gauche de t=140 s à t=380 s : c'est de l'habillage. Le code actuel s'en sortait par chance en prenant le plus grand visage ; dès qu'on raisonne sur « qui parle », il faut les écarter explicitement.

**Files:**
- Modify: `speaker.py`
- Test: `tests/test_speaker_tracks.py` (compléter)

**Interfaces:**
- Consumes: la forme `Track` de la Task 2
- Produces:
  - `speaker.MIN_FACE_FRACTION = 0.06`
  - `speaker.STATIC_TOLERANCE = 2`
  - `speaker.usable_tracks(tracks: list[dict], frame_h: int) -> list[dict]`

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajoute à `tests/test_speaker_tracks.py` (et complète l'import en tête avec `usable_tracks`) :

```python
def piste(id_, boxes, activity=None):
    return {"id": id_, "boxes": boxes,
            "activity": activity if activity is not None else
            {i: 5.0 for i in boxes}}


def test_une_piste_normale_est_conservee():
    t = piste(0, {0: b(100, w=200, h=200), 1: b(110, w=200, h=200)})
    assert [x["id"] for x in usable_tracks([t], frame_h=1080)] == [0]


def test_un_visage_trop_petit_est_ecarte():
    """60 px sur 1080 = 5,5 %, sous le seuil : c'est une vignette."""
    t = piste(0, {0: b(100, w=60, h=60), 1: b(110, w=60, h=60)})
    assert usable_tracks([t], frame_h=1080) == []


def test_une_piste_jamais_agitee_est_ecartee():
    """Une affiche, un visage de dos, un faux positif : ça ne parle pas."""
    t = piste(0, {0: b(100, w=200, h=200), 1: b(110, w=200, h=200)},
              activity={0: 0.0, 1: 0.0})
    assert usable_tracks([t], frame_h=1080) == []


def test_un_habillage_parfaitement_immobile_est_ecarte():
    """Rectangle au pixel près identique sur toute la durée : de l'habillage.
    Il porte de l'agitation (compression, bruit) mais ne bouge pas d'un pixel."""
    fixe = b(88, y=92, w=200, h=200)
    t = piste(0, {i: dict(fixe) for i in range(40)})
    assert usable_tracks([t], frame_h=1080) == []


def test_une_personne_qui_bouge_a_peine_reste_conservee():
    """Trois pixels de déplacement suffisent à prouver qu'il y a quelqu'un."""
    t = piste(0, {i: b(100 + (i % 2) * 3, w=200, h=200) for i in range(40)})
    assert [x["id"] for x in usable_tracks([t], frame_h=1080)] == [0]


def test_une_piste_trop_courte_pour_juger_l_immobilite_est_conservee():
    """Sur deux images, l'immobilité ne prouve rien — on ne rejette pas."""
    fixe = b(88, y=92, w=200, h=200)
    t = piste(0, {0: dict(fixe), 1: dict(fixe)})
    assert [x["id"] for x in usable_tracks([t], frame_h=1080)] == [0]


def test_les_pistes_conservees_gardent_leur_ordre():
    a = piste(0, {i: b(100, w=200, h=200) for i in range(5)})
    petit = piste(1, {i: b(400, w=50, h=50) for i in range(5)})
    c = piste(2, {i: b(800, w=200, h=200) for i in range(5)})
    assert [x["id"] for x in usable_tracks([a, petit, c], frame_h=1080)] == [0, 2]


def test_aucune_piste():
    assert usable_tracks([], frame_h=1080) == []
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

```bash
uv run pytest tests/test_speaker_tracks.py -k usable -v
```

Attendu : `ImportError: cannot import name 'usable_tracks' from 'speaker'`.

- [ ] **Step 3 : Implémenter**

Ajoute dans `speaker.py`, après `link_tracks` :

```python
# Un visage sous cette fraction de la hauteur d'image n'est pas un interlocuteur
# cadré mais une vignette — sur la source d'essai, trois « visages » de 70 px
# sur 1080 étaient de l'habillage collé au bord.
MIN_FACE_FRACTION = 0.06
# Déplacement en dessous duquel une piste est jugée parfaitement immobile. Une
# personne vivante bouge toujours de plus de deux pixels ; ce qui n'en bouge pas
# est incrusté dans l'image.
STATIC_TOLERANCE = 2
# En deçà de ce nombre d'images, l'immobilité ne prouve rien : on ne rejette pas
# une piste sur un échantillon trop court.
_STATIC_MIN_FRAMES = 8


def usable_tracks(tracks: list[dict], frame_h: int) -> list[dict]:
    """Ne garde que les pistes qui peuvent être un interlocuteur qui parle.
    Conserve l'ordre d'entrée. Pure."""
    kept = []
    for track in tracks:
        boxes = list(track["boxes"].values())
        if not boxes:
            continue
        # Trop petit : une vignette, pas quelqu'un de cadré.
        if max(box["h"] for box in boxes) < MIN_FACE_FRACTION * frame_h:
            continue
        # Jamais agité : une affiche, un visage de dos, un faux positif.
        if not any(value > 0 for value in track["activity"].values()):
            continue
        # Parfaitement immobile sur une durée significative : de l'habillage.
        if len(boxes) >= _STATIC_MIN_FRAMES:
            xs = [box["x"] for box in boxes]
            ys = [box["y"] for box in boxes]
            if (max(xs) - min(xs) <= STATIC_TOLERANCE
                    and max(ys) - min(ys) <= STATIC_TOLERANCE):
                continue
        kept.append(track)
    return kept
```

- [ ] **Step 4 : Lancer les tests, vérifier qu'ils passent**

```bash
uv run pytest tests/test_speaker_tracks.py -v && uv run pytest -q
```

Attendu : 20 tests dans le fichier, suite complète verte.

- [ ] **Step 5 : Commit**

```bash
git add speaker.py tests/test_speaker_tracks.py
git commit -m "feat(speaker): ecarte l'habillage et les faux positifs"
```

---

### Task 4 : `speaker_timeline` — décider qui tient le cadre

**Files:**
- Modify: `speaker.py`
- Test: `tests/test_speaker_timeline.py`

**Interfaces:**
- Consumes: la forme `Track` des Tasks 2-3
- Produces:
  - `speaker.ACTIVITY_WINDOW = 0.6`, `speaker.SWITCH_MARGIN = 1.5`, `speaker.MIN_SHOT = 1.2`
  - `speaker.speaker_timeline(tracks, cuts, n_frames, fps, min_shot=MIN_SHOT) -> list[dict]`

`cuts` est l'ensemble des indices d'images où le montage d'origine change de plan. Le retour est une liste de `{"start": float, "end": float, "track_id": int | None}`, en secondes depuis le début du clip, contiguë et couvrant `[0, n_frames / fps]`. `track_id` vaut `None` quand aucune piste ne tient le cadre (cadrage centré).

- [ ] **Step 1 : Écrire les tests qui échouent**

Crée `tests/test_speaker_timeline.py` :

```python
import pytest

from speaker import speaker_timeline


def piste(id_, activity):
    """`activity` : dict index d'image -> agitation. Les rectangles ne servent
    pas ici, mais la structure doit être complète."""
    return {"id": id_, "boxes": {i: {"x": id_ * 500, "y": 0, "w": 200, "h": 200}
                                 for i in activity},
            "activity": dict(activity)}


FPS = 10.0


def test_une_seule_piste_tient_tout_le_clip():
    a = piste(0, {i: 5.0 for i in range(100)})
    tl = speaker_timeline([a], cuts=set(), n_frames=100, fps=FPS)
    assert tl == [{"start": 0.0, "end": 10.0, "track_id": 0}]


def test_aucune_piste_donne_un_segment_centre():
    tl = speaker_timeline([], cuts=set(), n_frames=100, fps=FPS)
    assert tl == [{"start": 0.0, "end": 10.0, "track_id": None}]


def test_bascule_quand_l_autre_domine():
    """Le premier parle 5 s, le second les 5 s suivantes."""
    a = piste(0, {i: (9.0 if i < 50 else 0.5) for i in range(100)})
    b = piste(1, {i: (0.5 if i < 50 else 9.0) for i in range(100)})
    tl = speaker_timeline([a, b], cuts=set(), n_frames=100, fps=FPS)
    assert [s["track_id"] for s in tl] == [0, 1]
    assert tl[0]["end"] == pytest.approx(tl[1]["start"])
    assert 4.0 < tl[1]["start"] < 6.5


def test_pas_de_bascule_sous_la_marge():
    """1,2 fois plus agité ne suffit pas : deux personnes qui se coupent la
    parole feraient osciller le cadre."""
    a = piste(0, {i: (9.0 if i < 50 else 5.0) for i in range(100)})
    b = piste(1, {i: (5.0 if i < 50 else 6.0) for i in range(100)})
    tl = speaker_timeline([a, b], cuts=set(), n_frames=100, fps=FPS)
    assert [s["track_id"] for s in tl] == [0]


def test_min_shot_empeche_le_clignotement():
    """La domination alterne toutes les 0,3 s ; avec min_shot à 1,2 s le cadre
    ne peut pas changer plus vite qu'une fois par 1,2 s."""
    a = piste(0, {i: (9.0 if (i // 3) % 2 == 0 else 0.5) for i in range(120)})
    b = piste(1, {i: (0.5 if (i // 3) % 2 == 0 else 9.0) for i in range(120)})
    tl = speaker_timeline([a, b], cuts=set(), n_frames=120, fps=FPS, min_shot=1.2)
    durees = [s["end"] - s["start"] for s in tl]
    assert all(d >= 1.2 - 1e-6 for d in durees[:-1])


def test_une_coupe_de_la_source_autorise_une_bascule_plus_tot():
    """Sans coupe, min_shot repousse le changement à 1,2 s. La coupe le rend
    invisible, donc on l'autorise avant. La fenêtre glissante décale forcément
    la détection de quelques images : le test compare les deux cas plutôt que
    d'exiger un instant précis."""
    a = piste(0, {i: (9.0 if i < 5 else 0.0) for i in range(40)})
    b = piste(1, {i: (0.0 if i < 5 else 9.0) for i in range(40)})
    sans = speaker_timeline([a, b], cuts=set(), n_frames=40, fps=FPS, min_shot=1.2)
    avec = speaker_timeline([a, b], cuts=set(range(5, 12)), n_frames=40, fps=FPS,
                            min_shot=1.2)
    assert [s["track_id"] for s in sans] == [0, 1]
    assert [s["track_id"] for s in avec] == [0, 1]
    assert avec[1]["start"] < sans[1]["start"]


def test_le_silence_conserve_le_cadrage_courant():
    """Personne ne parle sur la seconde moitié : on tient, on ne recentre pas."""
    a = piste(0, {i: (9.0 if i < 50 else 0.0) for i in range(100)})
    b = piste(1, {i: 0.0 for i in range(100)})
    tl = speaker_timeline([a, b], cuts=set(), n_frames=100, fps=FPS)
    assert [s["track_id"] for s in tl] == [0]


def test_la_timeline_est_contigue_et_couvre_tout_le_clip():
    a = piste(0, {i: (9.0 if i < 50 else 0.5) for i in range(100)})
    b = piste(1, {i: (0.5 if i < 50 else 9.0) for i in range(100)})
    tl = speaker_timeline([a, b], cuts=set(), n_frames=100, fps=FPS)
    assert tl[0]["start"] == 0.0
    assert tl[-1]["end"] == pytest.approx(10.0)
    for gauche, droite in zip(tl, tl[1:]):
        assert gauche["end"] == pytest.approx(droite["start"])


def test_deterministe():
    a = piste(0, {i: (9.0 if i < 50 else 0.5) for i in range(100)})
    b = piste(1, {i: (0.5 if i < 50 else 9.0) for i in range(100)})
    args = ([a, b], set(), 100, FPS)
    assert speaker_timeline(*args) == speaker_timeline(*args)


def test_clip_vide():
    assert speaker_timeline([], cuts=set(), n_frames=0, fps=FPS) == []
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

```bash
uv run pytest tests/test_speaker_timeline.py -v
```

Attendu : `ImportError: cannot import name 'speaker_timeline' from 'speaker'`.

- [ ] **Step 3 : Implémenter**

Ajoute dans `speaker.py` :

```python
# --- Découpage en plans -----------------------------------------------------

# Fenêtre glissante de mesure de l'agitation : assez longue pour lisser une
# syllabe, assez courte pour réagir à une prise de parole.
ACTIVITY_WINDOW = 0.6
# Le prétendant doit être une fois et demie plus agité que le tenant. En deçà,
# deux personnes qui se coupent la parole feraient osciller le cadre.
SWITCH_MARGIN = 1.5
# Durée minimale d'un plan. Dans une conversation vive la parole alterne en
# moins d'une seconde ; sans plancher, le cadre ferait des allers-retours qui se
# lisent comme un bug et non comme un montage.
MIN_SHOT = 1.2


def _windowed_activity(track: dict, index: int, half: int) -> float:
    """Agitation moyenne d'une piste autour d'une image. Une image sans mesure
    compte pour zéro : un visage non détecté ne parle pas, de notre point de vue."""
    values = [track["activity"].get(i, 0.0)
              for i in range(index - half, index + half + 1)]
    return sum(values) / len(values) if values else 0.0


def speaker_timeline(tracks: list[dict], cuts: set[int], n_frames: int,
                     fps: float, min_shot: float = MIN_SHOT) -> list[dict]:
    """Qui tient le cadre, à chaque instant. Segments contigus couvrant tout le
    clip ; `track_id` à None quand aucune piste ne s'impose (cadrage centré).
    Pure."""
    if n_frames <= 0:
        return []
    if not tracks:
        return [{"start": 0.0, "end": n_frames / fps, "track_id": None}]

    half = max(1, int(round(ACTIVITY_WINDOW * fps / 2)))
    min_frames = max(1, int(round(min_shot * fps)))
    current = None
    since = 0
    boundaries: list[tuple[int, int | None]] = []
    for index in range(n_frames):
        scores = {t["id"]: _windowed_activity(t, index, half) for t in tracks}
        best = max(scores, key=lambda k: (scores[k], -k))
        if current is None:
            current, since = best, index
            boundaries.append((index, current))
            continue
        # Une coupe de la source rend le saut invisible : min_shot ne s'y
        # applique pas.
        libre = index in cuts or (index - since) >= min_frames
        # Marge de bascule : le tenant garde la main tant qu'il n'est pas
        # nettement dépassé. Un tenant à zéro (silence) n'est pas dépassé non
        # plus — on tient plutôt que de recentrer.
        domine = scores[best] > SWITCH_MARGIN * scores[current] and scores[best] > 0
        if best != current and libre and domine:
            current, since = best, index
            boundaries.append((index, current))

    segments = []
    for (start_index, track_id), (next_index, _) in zip(
            boundaries, boundaries[1:] + [(n_frames, None)]):
        segments.append({"start": start_index / fps, "end": next_index / fps,
                         "track_id": track_id})
    return segments
```

- [ ] **Step 4 : Lancer les tests, vérifier qu'ils passent**

```bash
uv run pytest tests/test_speaker_timeline.py -v && uv run pytest -q
```

Attendu : 10 tests PASS, suite complète verte. Si `test_bascule_quand_l_autre_domine` échoue sur l'instant de bascule, vérifie le calcul de `half` : la fenêtre glissante décale naturellement la détection d'une demi-fenêtre, et le test tolère `4.0 < start < 6.5` pour cette raison.

- [ ] **Step 5 : Commit**

```bash
git add speaker.py tests/test_speaker_timeline.py
git commit -m "feat(speaker): decide qui tient le cadre, avec plancher de plan"
```

---

### Task 5 : `crop_segments` et la nouvelle `crop_expr`

**Files:**
- Modify: `speaker.py`
- Test: `tests/test_speaker_timeline.py` (compléter), `tests/test_speaker_geometry.py` (adapter les tests de `crop_expr`)

**Interfaces:**
- Consumes: la timeline de la Task 4, les pistes des Tasks 2-3, `crop_size` et `_even` de la Task 1
- Produces:
  - `speaker.crop_segments(timeline, tracks, crop_w, src_w) -> list[dict]` — liste de `{"start", "end", "x_start", "x_end"}`, x entiers pairs bornés
  - `speaker.track_to_segments(centers: list[float], sample_fps: float, crop_w: int, src_w: int) -> list[dict]` — convertit une trajectoire plate en segments d'une image chacun ; sert au **chemin de repli** (`speaker_cuts` désactivé), qui continue de raisonner en trajectoire
  - `speaker.crop_expr(segments: list[dict], crop_w: int, src_w: int) -> str` — **signature changée** : elle ne prend plus une trajectoire plate

L'ancienne `crop_expr(track, sample_fps, crop_w, src_w)` disparaît. Ses tests dans `tests/test_speaker_geometry.py` doivent être réécrits en termes de segments — c'est la spécification qui change, pas le test qu'on contourne.

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajoute à `tests/test_speaker_timeline.py` (complète l'import avec `crop_segments`, `crop_expr`) :

```python
def piste_pos(id_, positions):
    """`positions` : dict index d'image -> x du CENTRE du visage."""
    return {"id": id_,
            "boxes": {i: {"x": int(x) - 100, "y": 0, "w": 200, "h": 200}
                      for i, x in positions.items()},
            "activity": {i: 5.0 for i in positions}}


def test_segments_centres_quand_aucune_piste():
    tl = [{"start": 0.0, "end": 5.0, "track_id": None}]
    seg = crop_segments(tl, [], crop_w=600, src_w=1920)
    assert seg == [{"start": 0.0, "end": 5.0, "x_start": 660, "x_end": 660}]


def test_segment_cale_sur_le_visage_de_sa_piste():
    """Visage centré en x=900 → crop de 600 large ancré en 900-300 = 600."""
    a = piste_pos(0, {i: 900 for i in range(50)})
    tl = [{"start": 0.0, "end": 5.0, "track_id": 0}]
    seg = crop_segments(tl, [a], crop_w=600, src_w=1920)
    assert seg[0]["x_start"] == 600 and seg[0]["x_end"] == 600


def test_un_visage_qui_derive_donne_un_segment_qui_interpole():
    a = piste_pos(0, {i: 500 + i * 10 for i in range(50)})
    tl = [{"start": 0.0, "end": 5.0, "track_id": 0}]
    seg = crop_segments(tl, [a], crop_w=600, src_w=1920)
    assert seg[0]["x_start"] < seg[0]["x_end"]


def test_les_x_sont_pairs_et_bornes_dans_l_image():
    a = piste_pos(0, {i: 0 for i in range(10)})
    z = piste_pos(1, {i: 5000 for i in range(10)})
    tl = [{"start": 0.0, "end": 1.0, "track_id": 0},
          {"start": 1.0, "end": 2.0, "track_id": 1}]
    seg = crop_segments(tl, [a, z], crop_w=600, src_w=1920)
    assert seg[0]["x_start"] == 0
    assert seg[1]["x_start"] == 1320          # 1920 - 600
    assert all(s[k] % 2 == 0 for s in seg for k in ("x_start", "x_end"))


def test_une_piste_absente_de_la_timeline_ne_plante_pas():
    """Robustesse : un track_id inconnu retombe sur le cadrage centré."""
    tl = [{"start": 0.0, "end": 5.0, "track_id": 42}]
    seg = crop_segments(tl, [], crop_w=600, src_w=1920)
    assert seg[0]["x_start"] == 660


def test_expr_constante_quand_tout_est_immobile():
    seg = [{"start": 0.0, "end": 5.0, "x_start": 300, "x_end": 300}]
    assert crop_expr(seg, crop_w=600, src_w=1920) == "300"


def test_expr_saute_sec_entre_deux_segments():
    """C'est le cœur de la fonctionnalité : une coupe, pas un glissement."""
    seg = [{"start": 0.0, "end": 2.0, "x_start": 100, "x_end": 100},
           {"start": 2.0, "end": 4.0, "x_start": 900, "x_end": 900}]
    assert crop_expr(seg, crop_w=600, src_w=1920) == "if(lt(t,2),100,900)"


def test_expr_interpole_a_l_interieur_d_un_segment():
    seg = [{"start": 0.0, "end": 2.0, "x_start": 100, "x_end": 300}]
    expr = crop_expr(seg, crop_w=600, src_w=1920)
    assert "2*floor(" in expr and "t" in expr


def test_expr_sans_segment_rend_le_centre():
    assert crop_expr([], crop_w=600, src_w=1920) == "660"


def test_expr_borne_le_nombre_de_paliers():
    from speaker import MAX_STEPS
    seg = [{"start": i * 0.4, "end": (i + 1) * 0.4,
            "x_start": (i * 40) % 1200, "x_end": (i * 40) % 1200}
           for i in range(400)]
    assert crop_expr(seg, crop_w=600, src_w=1920).count("if(") <= MAX_STEPS
```

Puis, dans `tests/test_speaker_geometry.py`, **supprime** les tests de l'ancienne `crop_expr` (ceux dont le nom commence par `test_crop_expr_`) : leur spécification n'existe plus. Garde tous les autres (`_fill_holes`, `smooth_track`, `crop_size`), qui ne changent pas.

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

```bash
uv run pytest tests/test_speaker_timeline.py -k "segments or expr" -v
```

Attendu : `ImportError: cannot import name 'crop_segments' from 'speaker'`.

- [ ] **Step 3 : Implémenter**

Remplace l'ancienne `crop_expr` de `speaker.py` par ceci, et ajoute `crop_segments` juste avant :

```python
def _anchor(center: float, crop_w: int, src_w: int) -> int:
    """x du crop pour un visage centré en `center`, borné dans l'image et pair."""
    return _even(min(max(0.0, center - crop_w / 2), max(0, src_w - crop_w)))


def crop_segments(timeline: list[dict], tracks: list[dict], crop_w: int,
                  src_w: int) -> list[dict]:
    """Traduit la timeline en segments de cadrage. Un segment porte le x de son
    début et celui de sa fin : le cadre suit doucement l'orateur pendant un plan
    long, mais saute d'un segment à l'autre. Pure."""
    centre = _even(max(0, (src_w - crop_w) / 2))
    by_id = {t["id"]: t for t in tracks}
    segments = []
    for entry in timeline:
        track = by_id.get(entry["track_id"])
        if track is None or not track["boxes"]:
            segments.append({**{k: entry[k] for k in ("start", "end")},
                             "x_start": centre, "x_end": centre})
            continue
        indices = sorted(track["boxes"])
        first, last = track["boxes"][indices[0]], track["boxes"][indices[-1]]
        segments.append({
            "start": entry["start"], "end": entry["end"],
            "x_start": _anchor(first["x"] + first["w"] / 2, crop_w, src_w),
            "x_end": _anchor(last["x"] + last["w"] / 2, crop_w, src_w)})
    return segments


def track_to_segments(centers: list[float], sample_fps: float, crop_w: int,
                      src_w: int) -> list[dict]:
    """Convertit une trajectoire plate en segments d'une image chacun. Sert au
    chemin de repli (`speaker_cuts` désactivé), qui raisonne encore en
    trajectoire — `crop_expr` fusionnera les paliers identiques. Pure."""
    return [{"start": i / sample_fps, "end": (i + 1) / sample_fps,
             "x_start": _anchor(x, crop_w, src_w),
             "x_end": _anchor(x, crop_w, src_w)}
            for i, x in enumerate(centers)]


def crop_expr(segments: list[dict], crop_w: int, src_w: int) -> str:
    """Compile les segments en expression FFmpeg pour `crop=x=…`.

    Interpole À L'INTÉRIEUR d'un segment (l'orateur peut bouger pendant un plan
    long) et SAUTE SEC entre deux segments — c'est la coupe demandée, un
    glissement d'un visage à l'autre se lirait comme une dérive. Une suite
    entièrement immobile rend une constante, ce qui évite de demander une
    réévaluation par frame (voir `clipper.crop_supports_eval`). Pure."""
    centre = _even(max(0, (src_w - crop_w) / 2))
    if not segments:
        return str(centre)
    # Plafond de paliers : au-delà l'expression devient illisible et coûteuse.
    # Le dernier segment retenu est prolongé jusqu'à la fin plutôt que tronqué,
    # pour que le cadre ne reparte jamais au centre en cours de clip.
    if len(segments) > MAX_STEPS:
        segments = segments[:MAX_STEPS - 1] + [
            {**segments[MAX_STEPS - 1], "end": segments[-1]["end"]}]
    if all(s["x_start"] == s["x_end"] == segments[0]["x_start"]
           for s in segments):
        return str(segments[0]["x_start"])

    expr = _ramp(segments[-1]["start"], segments[-1]["x_start"],
                 segments[-1]["end"], segments[-1]["x_end"])
    for segment in reversed(segments[:-1]):
        inner = _ramp(segment["start"], segment["x_start"],
                      segment["end"], segment["x_end"])
        expr = f"if(lt(t,{segment['end']:g}),{inner},{expr})"
    return expr
```

- [ ] **Step 4 : Lancer les tests, vérifier qu'ils passent**

```bash
uv run pytest tests/test_speaker_timeline.py tests/test_speaker_geometry.py -v && uv run pytest -q
```

Attendu : tout vert. `clipper.py` ne compile plus si `render_clip` appelle encore l'ancienne signature — c'est attendu, la Task 7 le rebranche. Si la suite échoue **uniquement** sur `render_clip`, passe à l'étape suivante ; toute autre erreur doit être corrigée ici.

- [ ] **Step 5 : Commit**

```bash
git add speaker.py tests/test_speaker_timeline.py tests/test_speaker_geometry.py
git commit -m "feat(speaker): segments de cadrage, saut sec entre deux plans"
```

---

### Task 6 : La couche d'I/O — lire, détecter, mesurer

Aucune de ces fonctions n'a de test automatisé : elles n'existent que pour alimenter la logique pure, sur le même principe que les I/O du clipper. La vérification est manuelle, au Step 4.

**Écart assumé par rapport à la spec** : la spec § 2 listait `read_frames`, `detect_faces` et `frame_difference` comme fonctions distinctes. Elles deviennent ici des étapes internes de `analyze_framing`, parce qu'elles partagent toutes le même handle `cv2.VideoCapture` et le même état de décodage : les séparer obligerait à faire circuler ce handle entre quatre fonctions sans rien gagner, puisqu'aucune n'est testable isolément. Seule `_mouth_activity` reste extraite, parce qu'elle opère sur deux images déjà décodées et se relit mieux à part.

**Files:**
- Modify: `speaker.py`

**Interfaces:**
- Consumes: `link_tracks`, `usable_tracks`, `speaker_timeline`, `crop_segments`, `crop_size`
- Produces:
  - `speaker.FRAME_FPS = 10.0`, `DETECT_EVERY = 0.5`, `DETECT_SCALE = 0.5`, `CUT_THRESHOLD = 0.35`
  - `speaker.analyze_framing(video_path: Path, start: float, end: float, src_w: int, src_h: int, *, min_shot: float = MIN_SHOT) -> list[dict]` — rend des segments prêts pour `crop_expr`

- [ ] **Step 1 : Implémenter la couche d'I/O**

Ajoute à la fin de `speaker.py` :

```python
# --- I/O : lecture, détection, mesure ---------------------------------------

# La parole agite la bouche à 5-10 Hz : en dessous de 10 images/s, l'information
# n'existe tout simplement pas. C'est abordable parce qu'on décode le clip d'une
# traite — une lecture séquentielle coûte 1,4 ms l'image contre 45 ms pour un
# seek, mesuré sur la source d'essai.
FRAME_FPS = 10.0
# Une tête ne se déplace pas assez en un demi-quart de seconde pour justifier de
# repayer une détection Haar : entre deux détections, on tient les rectangles à
# leur dernière position et on n'y mesure que l'agitation de la bouche.
DETECT_EVERY = 0.5
# Haar en 960x540 coûte 8 ms contre 25 ms en pleine résolution, pour la même
# détection à ces tailles de visage.
DETECT_SCALE = 0.5
# Écart global entre deux images (fraction de la dynamique) au-dessus duquel le
# montage d'origine a changé de plan.
CUT_THRESHOLD = 0.35


def _mouth_activity(previous, current, box: dict, scale: float) -> float:
    """Agitation du tiers inférieur d'un rectangle entre deux images réduites."""
    x = int(box["x"] * scale)
    y = int((box["y"] + box["h"] * 2 / 3) * scale)
    w = max(1, int(box["w"] * scale))
    h = max(1, int(box["h"] / 3 * scale))
    a = previous[y:y + h, x:x + w]
    b = current[y:y + h, x:x + w]
    if a.size == 0 or a.shape != b.shape:
        return 0.0
    import numpy as np
    return float(np.mean(np.abs(a.astype("int16") - b.astype("int16"))))


def analyze_framing(video_path: Path, start: float, end: float, src_w: int,
                    src_h: int, *, min_shot: float = MIN_SHOT) -> list[dict]:
    """Segments de cadrage d'un clip : qui tient l'image et où couper.

    Décode le clip UNE fois, séquentiellement — c'est ce qui rend la mesure
    d'agitation abordable. I/O."""
    import cv2   # import paresseux : coûteux, inutile à la logique pure
    import numpy as np

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    capture = cv2.VideoCapture(str(video_path))
    crop_w, _ = crop_size(src_w, src_h)
    detections: list[list[dict]] = []
    activity_per_frame: list[dict[int, float]] = []
    cuts: set[int] = set()
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
        source_fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        keep_every = max(1, int(round(source_fps / FRAME_FPS)))
        detect_every = max(1, int(round(DETECT_EVERY * FRAME_FPS)))
        previous = None
        boxes: list[dict] = []
        index = 0
        raw = 0
        while capture.get(cv2.CAP_PROP_POS_MSEC) < end * 1000:
            ok, frame = capture.read()
            if not ok:
                break
            raw += 1
            if raw % keep_every:
                continue
            small = cv2.cvtColor(
                cv2.resize(frame, None, fx=DETECT_SCALE, fy=DETECT_SCALE),
                cv2.COLOR_BGR2GRAY)
            if index % detect_every == 0:
                found = cascade.detectMultiScale(small, 1.15, 5, minSize=(30, 30))
                boxes = [{"x": int(x / DETECT_SCALE), "y": int(y / DETECT_SCALE),
                          "w": int(w / DETECT_SCALE), "h": int(h / DETECT_SCALE)}
                         for x, y, w, h in found]
            detections.append([dict(b) for b in boxes])
            measures: dict[int, float] = {}
            if previous is not None:
                # Coupe du montage d'origine : l'image entière change d'un coup.
                if float(np.mean(np.abs(small.astype("int16")
                                        - previous.astype("int16")))) / 255 > CUT_THRESHOLD:
                    cuts.add(index)
                for d, box in enumerate(boxes):
                    measures[d] = _mouth_activity(previous, small, box, DETECT_SCALE)
            activity_per_frame.append(measures)
            previous = small
            index += 1
    finally:
        capture.release()

    tracks = link_tracks(detections)
    # L'agitation est mesurée par rectangle détecté ; on la reporte sur la piste
    # à laquelle ce rectangle a été rattaché.
    for frame_index, measures in enumerate(activity_per_frame):
        for track in tracks:
            box = track["boxes"].get(frame_index)
            if box is None:
                continue
            for d, value in measures.items():
                if d < len(detections[frame_index]) and \
                        detections[frame_index][d] == box:
                    track["activity"][frame_index] = value
    usable = usable_tracks(tracks, src_h)
    timeline = speaker_timeline(usable, cuts, len(detections), FRAME_FPS,
                                min_shot=min_shot)
    return crop_segments(timeline, usable, crop_w, src_w)
```

- [ ] **Step 2 : Vérifier que la suite ne casse pas**

```bash
uv run pytest -q
```

Attendu : aucune régression sur les tests de logique pure.

- [ ] **Step 3 : Vérifier l'absence d'import lourd en tête**

```bash
uv run python -c "import time; t=time.time(); import speaker; print(f'{time.time()-t:.2f}s')"
```

Attendu : sous 0,3 s. Au-delà, un `import cv2` ou `numpy` a fui en tête de module.

- [ ] **Step 4 : Vérification manuelle sur la vraie source**

```bash
uv run python -c "
from pathlib import Path
import speaker, clipper
src = Path('data/clipper/football-magouilles-compagnie-ep12/source.mp4')
w, h = clipper.probe_size(src)
seg = speaker.analyze_framing(src, 60.0, 90.0, w, h)
print(f'{len(seg)} segment(s) sur 30 s :')
for s in seg:
    print(f\"  {s['start']:5.1f} -> {s['end']:5.1f}  x {s['x_start']} -> {s['x_end']}\")
print()
print('expression :', speaker.crop_expr(seg, speaker.crop_size(w, h)[0], w)[:300])
"
```

Attendu : plusieurs segments, dont les durées respectent le plancher de 1,2 s, avec des `x` qui changent nettement d'un segment à l'autre. Note le résultat dans ton rapport : c'est la première fois que la chaîne complète tourne.

- [ ] **Step 5 : Commit**

```bash
git add speaker.py
git commit -m "feat(speaker): detection, mesure d'agitation et decoupage reels"
```

---

### Task 7 : Brancher dans `render_clip` et exposer les deux réglages

**Files:**
- Modify: `clipper.py` (`render_clip`, `DEFAULTS`), `beatsync.py` (`DEFAULT_CONFIG["clipper"]`), `webui.py` (`CLIPPER_RANGES`, `coerce_clipper`), `frontend/src/lib/api.ts`, `frontend/src/features/settings/SettingsTab.tsx`
- Test: `tests/test_clipper_api.py` (compléter)

**Interfaces:**
- Consumes: `speaker.analyze_framing`, `speaker.crop_expr`, `speaker.crop_size`
- Produces: les réglages `speaker_cuts` (booléen, défaut `True`) et `min_shot` (défaut `1.2`, bornes `[0.4, 5.0]`)

- [ ] **Step 1 : Écrire les tests qui échouent**

Ajoute à `tests/test_clipper_api.py` :

```python
def test_coerce_accepte_le_booleen_speaker_cuts():
    assert coerce_clipper({"speaker_cuts": True})["speaker_cuts"] is True
    assert coerce_clipper({"speaker_cuts": False})["speaker_cuts"] is False
    assert coerce_clipper({"speaker_cuts": "true"})["speaker_cuts"] is True
    assert coerce_clipper({"speaker_cuts": "false"})["speaker_cuts"] is False


def test_coerce_refuse_un_speaker_cuts_non_booleen():
    with pytest.raises(ValueError):
        coerce_clipper({"speaker_cuts": "peut-etre"})
    with pytest.raises(ValueError):
        coerce_clipper({"speaker_cuts": 3})


def test_coerce_borne_min_shot():
    assert coerce_clipper({"min_shot": "2"})["min_shot"] == 2.0
    assert coerce_clipper({"min_shot": 99})["min_shot"] == 5.0
    assert coerce_clipper({"min_shot": 0})["min_shot"] == 0.4
    with pytest.raises(ValueError):
        coerce_clipper({"min_shot": "<script>"})
```

- [ ] **Step 2 : Lancer les tests, vérifier qu'ils échouent**

```bash
uv run pytest tests/test_clipper_api.py -k "speaker_cuts or min_shot" -v
```

Attendu : `KeyError` ou assertion sur une clé absente.

- [ ] **Step 3 : Ajouter les réglages**

Dans `clipper.py`, `DEFAULTS`, après `digest_chars` :

```python
    # Recadrage sur celui qui parle, avec coupes franches. Désactivable : si la
    # détection se comporte mal sur un contenu donné, on doit pouvoir revenir au
    # suivi simple sans attendre un correctif.
    "speaker_cuts": True,
    # Durée minimale d'un plan, en secondes. En dessous le cadre clignote, au
    # delà il reste sur quelqu'un qui ne parle plus.
    "min_shot": 1.2,
```

Réplique **exactement les mêmes clés, valeurs et commentaires** dans `beatsync.py`, `DEFAULT_CONFIG["clipper"]` — le test `test_clipper_defaults_match_beatsync` vérifie l'égalité des deux, ne le casse pas.

Dans `webui.py`, ajoute `"min_shot": (0.4, 5.0)` à `CLIPPER_RANGES`, puis apprends le booléen à `coerce_clipper`, après la boucle numérique :

```python
    if "speaker_cuts" in settings:
        value = settings["speaker_cuts"]
        if isinstance(value, bool):
            coerced["speaker_cuts"] = value
        elif isinstance(value, str) and value.lower() in ("true", "false"):
            coerced["speaker_cuts"] = value.lower() == "true"
        else:
            # Ni booléen ni "true"/"false" : on refuse plutôt que d'interpréter.
            # `bool("peut-etre")` vaut True, ce qui activerait la fonctionnalité
            # sur une faute de frappe.
            raise ValueError(f"speaker_cuts doit être un booléen : {value!r}")
```

- [ ] **Step 4 : Brancher `render_clip`**

Dans `clipper.py`, remplace le calcul de `expr` de `render_clip` :

```python
    src_w, src_h = probe_size(video_path)
    crop_w, crop_h = crop_size(src_w, src_h)
    if config.get("speaker_cuts", True):
        segments = speaker.analyze_framing(
            video_path, start, end, src_w, src_h,
            min_shot=float(config.get("min_shot", speaker.MIN_SHOT)))
        expr = speaker.crop_expr(segments, crop_w, src_w)
    else:
        # Repli : suivi lissé du plus grand visage, le comportement d'avant le
        # recadrage sur le locuteur. dead_zone en pixels SOURCE, donc rapportée
        # à crop_w — le crop est ensuite étiré vers OUT_W.
        track = smooth_track(track_faces(video_path, start, end),
                             default=src_w / 2, dead_zone=DEAD_ZONE * crop_w)
        expr = speaker.crop_expr(
            speaker.track_to_segments(track, SAMPLE_FPS, crop_w, src_w),
            crop_w, src_w)
```

et remplace l'import de `speaker` en tête de `clipper.py` par `import speaker` en plus des noms déjà importés (on a besoin du module pour `analyze_framing` et `MIN_SHOT`).

Garde `track_faces` : c'est le chemin de repli.

- [ ] **Step 5 : Ajouter les deux champs à l'interface**

Dans `frontend/src/lib/api.ts`, étends le type `clipper` de `Overrides` avec `speaker_cuts?: boolean` et `min_shot?: number`.

Dans `frontend/src/features/settings/SettingsTab.tsx`, ajoute à la carte « Clipper », **sur le moule exact des champs déjà présents** :
- une case à cocher « Recadrer sur celui qui parle », avec l'aide « Le cadre suit l'intervenant qui parle et change de personne par une coupe franche. Décoche pour revenir au suivi simple du plus grand visage. » ;
- un champ numérique « Durée minimale d'un plan (s) », avec l'aide « En dessous, le cadre clignote ; au-delà, il reste sur quelqu'un qui ne parle plus. »

Utilise le composant de case à cocher déjà présent dans `frontend/src/components/ui/checkbox.tsx`.

- [ ] **Step 6 : Vérifier**

```bash
uv run pytest -q && cd frontend && npm run build
```

Attendu : les deux verts.

- [ ] **Step 7 : Commit**

```bash
git add clipper.py beatsync.py webui.py frontend/src tests/test_clipper_api.py
git commit -m "feat(clipper): branche le recadrage sur le locuteur et ses reglages"
```

---

### Task 8 : Vérification à l'œil et documentation

C'est la tâche qui manquait au premier lot du clipper : trois pannes ont été livrées faute d'avoir regardé le résultat. On ne refait pas l'erreur.

**Files:**
- Modify: `CLAUDE.md`, `docs/fiches/10-clipper.md`
- Create: aucun

**Interfaces:**
- Consumes: tout ce qui précède
- Produces: rien de code

- [ ] **Step 1 : Rendre un clip réel et le regarder**

```bash
uv run python -c "
import json, pathlib, clipper
src = pathlib.Path('data/clipper/football-magouilles-compagnie-ep12/source.mp4')
words = json.loads((src.parent / 'transcript.json').read_text())
clipper.render_clip(src, 60.0, 100.0, pathlib.Path('/tmp/locuteur.mp4'),
                    words=words, config={'speaker_cuts': True})
print('rendu OK')
"
ffprobe -v error -show_entries stream=codec_type,width,height -of csv=p=0 /tmp/locuteur.mp4
```

Puis **regarde la vidéo** et réponds franchement à trois questions dans ton rapport :

1. le cadre tient-il le visage de celui qui parle ?
2. les coupes tombent-elles aux bons moments, sans clignoter ?
3. l'habillage figé du bord gauche est-il bien ignoré ?

Si la réponse à l'une des trois est non, **dis-le** plutôt que de conclure au succès. Compare avec un rendu de repli pour te faire une opinion :

```bash
uv run python -c "
import json, pathlib, clipper
src = pathlib.Path('data/clipper/football-magouilles-compagnie-ep12/source.mp4')
words = json.loads((src.parent / 'transcript.json').read_text())
clipper.render_clip(src, 60.0, 100.0, pathlib.Path('/tmp/repli.mp4'),
                    words=words, config={'speaker_cuts': False})
print('repli OK')
"
```

- [ ] **Step 2 : Documenter dans `CLAUDE.md`**

Ajoute une entrée `speaker.py` à la section « Architecture », au format des autres (dense, chaque décision non évidente portant sa justification). Elle doit couvrir : les fonctions pures (`iou`, `link_tracks`, `usable_tracks`, `speaker_timeline`, `crop_segments`, `crop_expr`, `crop_size`, `smooth_track`), la couche d'I/O (`analyze_framing`), et les pièges :

- **la dépendance est à sens unique** — `clipper` importe `speaker`, jamais l'inverse ;
- **la lecture est séquentielle et non par `seek`** : 1,4 ms contre 45 ms l'image, et c'est ce renversement qui rend les 10 images/s abordables — la parole agite la bouche à 5-10 Hz, à 2 images/s l'information n'existe pas ;
- **`crop_expr` interpole dans un segment et saute entre deux** : c'est la coupe demandée, un glissement d'un visage à l'autre se lirait comme une dérive ;
- **une piste non appariée n'est pas close** : la cascade rate régulièrement un visage, et rouvrir une piste ferait passer la même personne pour une nouvelle, donc une coupe injustifiée ;
- **les trois filtres de `usable_tracks`** et le contre-exemple qui les a motivés (l'habillage figé de la source d'essai) ;
- **`min_shot` et `SWITCH_MARGIN`** existent contre le clignotement ;
- **la cascade est frontale** : un intervenant de profil n'est pas détecté, sa piste est tenue à sa dernière position ;
- **`speaker_cuts` est le repli** vers le suivi simple.

Mets aussi à jour la ligne `clipper.py` de `CLAUDE.md` : la géométrie de cadrage n'y est plus.

- [ ] **Step 3 : Mettre à jour la fiche du clipper**

Dans `docs/fiches/10-clipper.md`, la section sur le recadrage décrit le suivi du plus grand visage — elle est devenue fausse. Réécris-la pour le nouveau fonctionnement, et **revérifie tous les numéros de ligne de la fiche** : le déménagement de la Task 1 les a tous décalés. Un numéro de ligne faux est pire que pas de numéro, parce qu'on s'y fie sans vérifier.

- [ ] **Step 4 : Vérifier**

```bash
uv run pytest -q && cd frontend && npm run build
```

- [ ] **Step 5 : Commit**

```bash
git add CLAUDE.md docs/fiches/10-clipper.md
git commit -m "docs(speaker): documente le recadrage sur le locuteur actif"
```

---

## Ce qui n'est pas dans ce plan

Rappel de la spec, pour qu'aucune tâche ne dérive :

- diarisation audio et modèles de détection de locuteur actif (écartés au profit de l'agitation de bouche) ;
- suivi des visages de profil (la cascade est frontale) ;
- split-screen à deux visages ;
- recadrage vertical — le crop reste ancré en haut (`y=0`).
