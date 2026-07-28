# Spec — Ramps de vitesse, images fixes, texte fixe positionnable

**Date** : 2026-07-28
**Statut** : validé, prêt pour plan d'implémentation

Trois besoins indépendants, issus d'une veille, regroupés dans un seul spec parce
qu'ils traversent les mêmes couches (`DEFAULT_CONFIG` → preset/niche → `build_edl` →
`_segment_filters` → UI). L'implémentation se fait en **trois lots séquentiels**
(volet 1, puis 2, puis 3), chacun livrable seul.

---

## Volet 1 — Ramps de vitesse synchronisés au beat, avec flux optique

### Problème

Le montage n'a qu'un seul effet de vitesse : le « gasp », un ralenti 0,5x codé en dur
sur le dernier segment avant le drop (`beatsync.py:536-539`). Le reste de la vidéo
tourne à `clip_speed` (1,0 par défaut). On veut du **dynamisme rythmique** : des
ralentis et des accélérations calés sur la musique, et des ralentis **fluides** —
donc avec interpolation de frames (flux optique), qui invente par calcul les images
manquantes entre les images réelles.

### Décisions de cadrage (issues du brainstorming)

- Motif retenu : **ralenti d'anticipation → accéléré de relance autour des impacts**.
  Écartés : l'alternance systématique par phrase (mécanique) et la vitesse pilotée
  par l'énergie (trop peu de contraste sur un track dense).
- Interpolation : **`minterpolate` FFmpeg, uniquement sur les segments ralentis**.
  Écarté : RIFE (dépendance PyTorch + poids, et sans GPU sur la tour ce serait plus
  lent que `minterpolate`) et l'interpolation systématique sur tous les segments
  (temps de génération inacceptable sur un lot de N variantes).

### Design

#### 1.1 Impacts

Un **impact** est un beat qui porte le motif. Les impacts sont :

- le beat du drop (`drop_idx`), quand il existe ;
- tous les beats d'indice `i` tels que `(i - drop_idx) % impact_beats == 0`,
  **avant comme après** le drop.

Sans drop connu (`drop_time is None`, ex. mode `section: "calm"`), l'ancre est le
premier beat de la fenêtre au lieu de `drop_idx` — le motif reste donc actif.

Seuls les impacts qui **coïncident avec une frontière de segment** produisent un
effet : les frontières sont déjà quantifiées frame et portent leur `beat_index`
(`beatsync.py:505-510`), la comparaison se fait donc sur l'indice de beat, pas sur
un timestamp flottant.

#### 1.2 Règle de vitesse

Fonction **pure** extraite et testée isolément :

```python
def ramp_speed(start_beat: int, end_beat: int, duration: float,
               impacts: set[int], config: dict) -> float
```

- le segment **finit** sur un impact (`end_beat in impacts`) → `slow` (0,5x) ;
- le segment **commence** sur un impact (`start_beat in impacts`) → `fast` (1,4x) ;
- les deux à la fois → **`slow` gagne** (l'anticipation prime sur la relance) ;
- sinon → `clip_speed` global.

Exemption : si `duration < min_dur` (0,25 s), on retourne `clip_speed` sans ramp.
C'est le cas du strobo à 1 beat après le drop — un 0,5x sur trois frames ne se voit
pas et coûterait cher en interpolation.

La valeur retournée reste clampée à `[0.5, 1.5]` comme aujourd'hui (défense contre
une config UI hors bornes) — les bornes de `slow` et `fast` ci-dessous sont donc
choisies à l'intérieur de cet intervalle.

Aucun tirage aléatoire n'intervient : la fonction est déterministe, **l'invariant de
reproductibilité est intact**.

#### 1.3 Config

`effects["speed"]` reste l'**interrupteur maître** (déjà exposé dans les presets et
l'UI) ; le nouveau bloc ne porte que les paramètres :

```python
"speed_ramp": {
    "slow": 0.5,          # vitesse du segment d'anticipation, 0.3–1.0
    "fast": 1.4,          # vitesse du segment de relance, 1.0–1.5
    "impact_beats": 8,    # périodicité des impacts, en beats
    "min_dur": 0.25,      # s : en dessous, pas de ramp
    "interpolate": True,  # flux optique sur les segments ralentis
},
```

S'empile normalement via `db.effective_config` (`DEFAULT_CONFIG ← settings.json ←
preset`). `merge_settings` ne conserve que les clés présentes dans la base : les cinq
clés doivent donc figurer dans `DEFAULT_CONFIG`.

#### 1.4 Rendu — flux optique

Dans `_segment_filters` (`beatsync.py:954`), la chaîne `post` commence aujourd'hui par
`fps={fps}`. Quand `speed < 1.0` **et** `speed_ramp["interpolate"]`, cette entrée est
remplacée par :

```
minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1
```

Position choisie délibérément : `post` s'exécute **après** le `setpts` (qui est dans
`pre`, donc les timestamps sont déjà étirés) et **après** le scale/crop, donc
l'interpolation travaille sur du 1080x1920 déjà cadré plutôt que sur la source, et le
même code sert aux trois layouts (`crop`, `split`, `blur`).

`minterpolate` est déterministe : rien à faire pour la reproductibilité.

#### 1.5 Coût, assumé

`minterpolate` en `mi_mode=mci` est le poste le plus lourd du projet : compter **5 à
15x** le temps d'encodage des segments concernés. Avec `impact_beats: 8` sur une
fenêtre de 30 s, ça représente ~4 à 8 segments ralentis par vidéo, pas la totalité —
mais sur un lot de 20 variantes l'écart est net. `interpolate: False` retombe sur le
`setpts` seul : ralenti saccadé, coût nul.

#### 1.6 Impact sur l'existant

Le gasp actuel devient un cas particulier de la règle (le drop est un impact). Les
presets déjà enregistrés produiront donc des vidéos différentes à seed égale. C'est un
changement de config, pas une violation de l'invariant (même seed **et même config** →
même vidéo), mais il faut le savoir avant de comparer un rendu à un ancien.

---

## Volet 2 — Images fixes dans le catalogue de clips

### Problème

Le catalogue partagé `clips/` n'accepte que des vidéos. On veut pouvoir y déposer des
**images** (visuels, artworks, screenshots) et qu'elles soient montées comme les clips
si la niche les sélectionne. Une image tenue longtemps casse le rythme : elle doit
apparaître **brièvement**.

### Décisions de cadrage (issues du brainstorming)

- Une image est un **flash court avec Ken Burns** : segment court plafonné, et toujours
  un léger zoom/pan pour qu'elle ne soit jamais figée.
- Pas de quota d'images réglable exposé à l'utilisateur (écarté : un réglage de plus
  pour un gain marginal). La fréquence naturelle du tirage suffit, avec un garde-fou
  interne fixe.
- Les images vivent dans `clips/`, le catalogue partagé existant — pas de troisième
  section dans l'onglet Catalogue, pas de nouvelle colonne en base : la sélection par
  niche (`niche["clips"]`) les couvre déjà.

### Design

#### 2.1 Catalogue et UI

`IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}` s'ajoute à `VIDEO_EXTS` dans
`webui.py` pour :

- l'upload (`POST /api/clips`) ;
- la suppression (`DELETE /api/clips/<name>`, garde anti-traversal inchangée) ;
- le service (`GET /api/clips/<name>`), avec les mimetypes correspondants ajoutés à
  `ASSET_MIMETYPES`.

Côté React, la table du catalogue (`AssetSection.tsx`) n'affiche que nom, taille et
bouton supprimer — **pas d'aperçu** : la seule modification est l'attribut `accept` de
la section Clips dans `Catalogue.tsx`. Le reste (sélection dans la niche, retrait,
catalogue partagé) ne bouge pas.

#### 2.2 Chargement et scan

`load_clips` marque chaque entrée `"kind": "video" | "image"`. Une image a
`duration = None` ; largeur, hauteur et ratio viennent d'`ffprobe` comme pour une
vidéo. Le message d'erreur « aucun clip vidéo … » devient « aucun clip ni image … ».

`scan_clips` **saute** les images : rien à décoder, rien à classifier. Elles n'ont donc
pas de clé `intervals`, ce qui les rend entièrement utilisables — exactement la
sémantique existante d'un clip non scanné (`intervals` absent ≠ `intervals: []`).

#### 2.3 Sélection dans `build_edl`

Une image n'est candidate que si :

- la durée du segment est ≤ `IMAGE_MAX_DUR` — c'est le flash court. **Constante de
  module** dans `beatsync.py` (0,6 s), pas un champ de config : la décision de
  cadrage est de ne pas exposer de réglage d'images à l'utilisateur ;
- le filtre `prev_path` existant s'applique (jamais deux segments consécutifs sur le
  même asset) ;
- **au moins 3 segments** séparent deux images, quelles qu'elles soient. Sans ce
  garde-fou, un passage de strobo (segments à 1 beat) devient un diaporama. Compteur
  déterministe, pas de tirage.

L'entrée d'EDL produite pour une image porte :

- `clip_in = 0.0` ;
- `speed = 1.0` (aucun ralenti sur un fixe — l'exemption est explicite, elle prime sur
  la règle du volet 1) ;
- `kenburns` dans `effects` ;
- `layout = "blur"` si `ratio >= 1.2` (un visuel 16:9 crope trop violemment en 9:16),
  `"crop"` sinon. Règle déterministe : le scan, qui décide normalement du layout, n'a
  pas tourné sur les images.
- `kind = "image"`.

Le sens du zoom (avant/arrière) et la direction du pan sont tirés sur le `rng` seedé
local, donc reproductibles.

#### 2.4 Rendu

`render` branche sur `entry["kind"]` :

- vidéo : inchangé (`-ss` avant `-i`, `-t source_needed + 0.5`) ;
- image : `-loop 1 -t <source_needed + 0.5> -i <path>`, sans `-ss`.

`kenburns` ajoute dans `post` un `zoompan` à dérive lente (`d=1`, appliqué image par
image sur le flux bouclé, comme le fait déjà l'effet `zoom`).

Le `-frames:v` exact reste en place : pas de dérive audio/vidéo au concat.

---

## Volet 3 — Texte fixe positionnable

### Problème

Les seules incrustations possibles sont les **punchlines générées par LLM**, qui
changent au fil de la vidéo. Certains formats TikTok demandent au contraire **une
caption unique, identique du début à la fin**, écrite à la main, dont on veut choisir
la **position et la taille**.

### Décisions de cadrage (issues du brainstorming)

- Le texte se règle **sur la niche**, à côté du préprompt de punchlines (écarté : le
  preset, qui décrit un style de montage et non un contenu ; et la saisie au lancement
  du lot, qui ne mémorise rien).
- **Exclusif** avec les punchlines générées : soit LLM, soit texte fixe.
- Placement par **curseurs X/Y en pourcentage + taille en px** (écarté : les positions
  prédéfinies, trop grossières ; le drag & drop sur maquette, beaucoup plus de JS pour
  un gain de confort).
- Pas de texte qui change en cours de vidéo dans ce périmètre — c'est ce que fait déjà
  le mode LLM.

### Design

#### 3.1 Config

Le bloc `subtitles` existant gagne le mode et le placement :

```python
"subtitles": {
    "enabled": False,
    "mode": "llm",        # "llm" (punchlines générées) | "fixe"
    "text": "",           # mode fixe ; les retours à la ligne sont respectés
    "x": 0.5,             # ancrage horizontal, fraction de largeur
    "y": 0.74,            # ancrage vertical, fraction de hauteur
    "size": 64,           # taille de police, px
    "preprompt": "", "min_dur": 1.4, "model": "claude-opus-4-8", "font": "impact",
},
```

Valeurs par défaut choisies pour **reproduire le placement actuel** (`fontsize=64`,
bas-centré). Toute valeur de `mode` autre que `"fixe"` est traitée comme `"llm"`
(dégradation sûre, comme `section` dans le spec du 2026-07-20).

Stocké dans la colonne JSON `subtitles` de la niche, déjà existante (`db.py:36`) :
**aucune migration DB**.

#### 3.2 Logique

`apply_subtitles` (`beatsync.py:865`) gagne une branche en tête, après le test
`enabled` :

```python
if sub.get("mode") == "fixe":
    for entry in edl:
        entry["caption"] = sub.get("text", "")
    return edl
```

Le LLM n'est **jamais** appelé en mode fixe : pas de découpage en créneaux, pas de
cache, pas de dépendance réseau. Fonction pure, testable.

`generate_niche` stocke le texte dans `subtitles.lines` de la vidéo produite (une
seule entrée) pour que la bibliothèque affiche la caption comme pour le mode LLM.

#### 3.3 Rendu

Le `drawtext` de `_segment_filters` (`beatsync.py:997-1002`) lit `size`, `x` et `y`
depuis la config au lieu des valeurs codées en dur. Ancrage **centré sur le point** :

```
x='w*{x}-text_w/2'
y='h*{y}-text_h/2'
fontsize={size}
```

Un seul chemin de code : les punchlines générées héritent gratuitement du réglage de
position et de taille.

**À vérifier avant de brancher l'UI** : `_drawtext_escape` doit tenir les retours à la
ligne et les apostrophes. Un test dédié le couvre ; si l'échappement actuel ne les
gère pas, le corriger fait partie de ce lot.

#### 3.4 UI

La carte « Punchlines » du détail niche gagne :

- un choix **LLM / Texte fixe** (le préprompt et le textarea s'affichent alternativement) ;
- un `textarea` pour le texte ;
- trois curseurs : X (%), Y (%), taille (px), avec valeur numérique affichée.

Le rendu du texte ne demande aucune précaution XSS particulière : le frontend est du
React (`NicheDetail.tsx`), qui échappe les valeurs interpolées par construction —
l'`esc()` mentionné dans CLAUDE.md concerne l'ancienne UI vanilla.

`x`, `y` et `size` sont en revanche coercés et bornés **côté serveur** — 400 si non
convertible — comme les overrides numériques existants : le champ `subtitles` de la
niche est un blob JSON écrit tel quel en base, et une valeur non numérique casserait
le rendu FFmpeg au moment de la génération, loin de la saisie.

---

## Tests

Tout en pur, sans FFmpeg ni réseau :

- `ramp_speed` : segments avec et sans drop, coïncidence début/fin d'impact, conflit
  début+fin (slow gagne), exemption `min_dur`, `effects["speed"]` désactivé.
- Construction du filtre d'un segment ralenti : présence de `minterpolate` à la place
  de `fps=`, et absence quand `interpolate: False` ou `speed >= 1.0`.
- Éligibilité des images dans `build_edl` sur un faux catalogue mixte : plafond de
  durée, écart minimum de 3 segments, `speed == 1.0`, layout selon le ratio.
- Arguments de rendu d'une image : `-loop 1` présent, `-ss` absent.
- `apply_subtitles` en mode `fixe` : caption identique sur tous les segments, LLM non
  appelé (mock qui lève si appelé).
- `_drawtext_escape` sur retours à la ligne, apostrophes, deux-points, `%`.

## Hors périmètre

- Quota d'images réglable par l'utilisateur.
- RIFE ou tout autre interpolateur neuronal.
- Texte fixe qui change en cours de vidéo (c'est le mode LLM).
- Aperçu drag & drop du placement du texte.
