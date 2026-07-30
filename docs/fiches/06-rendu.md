# 06 — Rendu

> `_segment_input_args` · `_segment_filters` · `kenburns_filter` · `render` — lignes 1565-1807
> ← [05 punchlines](05-punchlines.md) · [vue d'ensemble](00-beatsync-vue-ensemble.md)

## Ce que fait ce bloc

Exécuter l'EDL. **Aucune décision ne se prend ici** — tout a été tranché par
`build_edl` ([04](04-build-edl.md)). Le rendu traduit chaque entrée en une
commande FFmpeg, puis recolle les morceaux.

---

## La stratégie : un fichier par segment

```python
with tempfile.TemporaryDirectory(prefix="beatsync-") as tmp:
    for i, entry in enumerate(edl):
        segment = tmpdir / f"seg{i:04d}.mp4"
        _run_ffmpeg([...])          # 1 encodage par segment
    _run_ffmpeg(["-f", "concat", ...])   # 1 concat en copie de flux
```

30 segments = 31 appels FFmpeg.

### Pourquoi pas un seul filtergraph géant

Chaque segment a sa propre chaîne de filtres : vitesse, layout, zoom, flash,
glitch, drawtext. Un filtergraph unique pour 30 segments hétérogènes serait
illisible, impossible à déboguer, et un plantage ne dirait pas **où**.

Ici, une erreur nomme le segment fautif, et `_run_ffmpeg` (l.1524) remonte la
commande complète :

```python
raise RuntimeError(f"ffmpeg a échoué :\n  ffmpeg {' '.join(args)}\n{result.stderr}")
```

Tu peux copier-coller la ligne et rejouer le cas.

### Le concat est en copie de flux (l.1682)

```python
"-c:v", "copy",
```

**Pas de ré-encodage.** Les segments sont déjà en H.264 aux bons paramètres ; on
les recolle tels quels. Sans ça, on encoderait deux fois — deux fois le temps,
et une génération de perte de qualité.

Prix à payer : tous les segments doivent partager le même timescale, d'où
`-video_track_timescale 15360` (l.1485).

---

## Le nombre de frames exact — le point critique

```python
n_frames = round(entry["duration"] * fps)
...
"-frames:v", str(n_frames),
```

Et dans la chaîne de filtres :

```python
post.append(f"tpad=stop_mode=clone:stop_duration={1 + freeze:g}")
```

### Le bug que ça corrige

Le commentaire l.1472-1475 :

> Les sources à fps non multiples (23,976…) peuvent rendre quelques ms de moins
> que demandé, et le concat accumulerait la dérive.

Un rush d'animé est souvent à **23,976 fps**. Sorti à 30 fps, un segment de
0,533 s peut rendre 15 frames au lieu de 16. Une frame perdue = 33 ms.

Sur un segment isolé, invisible. Sur 30 segments, **une seconde de décalage** :
à la fin de la vidéo, les coupes ne tombent plus sur la musique. Tout le projet
s'effondre.

### La parade en deux temps

| Filtre | Rôle |
|---|---|
| `tpad=stop_mode=clone` | **allonge** en clonant la dernière frame si la source est trop courte |
| `-frames:v N` | **coupe** pile au bon compte si elle est trop longue |

Encadré des deux côtés, chaque segment fait exactement `duration × fps` frames.
La dérive ne peut pas naître.

C'est le pendant de la quantification dans `build_edl` : là on empêche l'erreur
de décision de s'accumuler, ici l'erreur d'encodage.

---

## `_segment_input_args(entry, config=None)` — l.1565

Ce qu'on donne à FFmpeg **en entrée**.

```python
freeze = float(entry.get("freeze", 0.0))
source_needed = max(0.0, entry["duration"] - freeze) * entry.get("speed", 1.0)
margin = 0.0 if freeze > 0.0 else 0.5
if entry.get("kind") == "image":
    return ["-loop", "1", "-t", f"{source_needed + margin:.6f}", "-i", path]
return ["-ss", f"{entry['clip_in']:.6f}", "-t", f"{source_needed + margin:.6f}", "-i", path]
```

### `-ss` avant `-i` (seek rapide)

Placé **avant** l'entrée, FFmpeg saute directement au keyframe le plus proche.
Après `-i`, il décoderait tout depuis le début. Sur un épisode de 20 minutes, la
différence est énorme.

Le prix : le seek est imprécis (il tombe sur un keyframe). D'où le rab.

### Le rab de 0,5 s, et pourquoi il disparaît sur le figé

C'est le raisonnement le plus fin du bloc (l.1301-1307) :

> Un segment à figé (`freeze > 0`) veut au contraire **manquer** de source par
> construction — c'est ce que `tpad=stop_mode=clone` comble. Lui laisser le rab
> de seek reviendrait à **amputer le figé** (le rab, étiré par `1/speed`, peut
> annuler tout le budget de figé).

Un segment ordinaire prend 0,5 s de source en trop ; `-frames:v` coupe le
surplus. Aucun effet visible.

Un segment à figé, lui, demande **volontairement** moins de source que sa durée :
le manque est comblé par des frames clonées, c'est ça le figé. Si on lui donne
0,5 s de rab, et que ce rab est étiré par le ralenti (0,5 s à 0,5× = 1 s
d'écran), il remplit tout le budget de figé — **et le figé disparaît**.

D'où `margin = 0.0` quand `freeze > 0`. Un seek légèrement imprécis ne fait
alors qu'allonger le figé de quelques frames clonées : imperceptible.

### Le figé n'a aucun filtre dédié

```python
post.append(f"tpad=stop_mode=clone:stop_duration={1 + freeze:g}")
```

Le figé est un **effet de bord** de trois choses déjà là : moins de source
demandée, `tpad` qui clone, `-frames:v` qui garde le compte. Pas de `freeze`
filter, pas de branche spéciale. Une seule ligne change.

### Les écrans noirs : aucun fichier ouvert

```python
if entry.get("kind") == "black":
    cfg = config or DEFAULT_CONFIG
    return ["-f", "lavfi", "-i",
            f"color=c=black:s={cfg['width']}x{cfg['height']}:r={cfg['fps']}"]
```

Le strobe de build-up ([04](04-build-edl.md)) produit des entrées `kind: "black"`
sans clip. FFmpeg **génère** la matière : pas de fichier noir en asset, donc rien
à maintenir, et la source suit automatiquement le format de sortie — un preset en
carré donne un noir carré sans code supplémentaire. Mesuré à ~87 ms par segment,
un coût lié au lancement du process, pas à l'encodage.

`_segment_filters` sort tôt pour ces entrées, sur une chaîne minimale : `fps`,
la punchline si elle existe, `setsar=1,format=yuv420p`, `tpad`. Ni rognage de
bandes, ni `delogo`, ni layout — la source est déjà aux bonnes dimensions.

**La punchline est conservée sur le noir.** Si le texte disparaissait un
demi-temps sur deux, il clignoterait à 2 Hz et deviendrait illisible ; sur fond
noir il est au contraire parfaitement lisible. C'est la raison d'être de
`_caption_filter` (l.1607), extrait pour être partagé par les deux chemins plutôt
que dupliqué.

### Les images : `-loop 1`, pas de `-ss`

Une image n'a pas de timeline. On la boucle sur la durée voulue.

---

## `_segment_filters(entry, config)` — l.1637

La grosse fonction du bloc. Retourne soit `["-vf", ...]`, soit
`["-filter_complex", ..., "-map", "[v]"]`.

### L'ordre de la chaîne

```
pre  : crop (bandes noires) → delogo → setpts (vitesse)
       ↓
layout : crop | split | blur          ← passage en 1080x1920
       ↓
post : minterpolate|fps → kenburns → zoom → flash → glitch|rgb
       → grade → grain → drawtext → setsar/format → tpad
```

L'ordre **est** le raisonnement :

| Position | Pourquoi là |
|---|---|
| `crop` **en tout premier** | les bandes noires ne doivent entrer dans aucun calcul suivant — et il recale le delogo, voir ci-dessous |
| `delogo` **avant** le recadrage de layout | le crop intelligent ou le blur peuvent faire entrer le logo au champ |
| `setpts` avant tout le reste | la vitesse change la base temporelle |
| `minterpolate` **après** le layout | il travaille sur du 1080×1920 déjà cadré, pas sur la source — moins de pixels, et sert les 3 layouts sans duplication |
| `drawtext` **après** les accents | le RGB et le glitch abîmeraient le texte ; posé après, il reste net |
| `tpad` en dernier | complète le compte de frames |

### `pre` — bandes noires, delogo et vitesse (l.1648)

```python
crop = entry.get("crop")
if crop:
    pre += f"crop={crop['w']}:{crop['h']}:{crop['x']}:{crop['y']},"
if config.get("delogo") and "clip_w" in entry and entry.get("kind") != "image":
    pre += f"delogo=x=...:y=...:w=...:h=...,"
if speed != 1.0:
    pre += f"setpts=(PTS-STARTPTS)/{speed:.6f},"
```

`crop` porte le rectangle utile **en pixels**, calculé dans `build_edl` à partir
des fractions détectées par le scan ([02](02-scan-clips.md)). Absent sur les
clips sans bandes et sur les images — d'où le `.get`.

**L'ordre corrige un second défaut, gratuitement.** Le delogo est exprimé en
fractions de `clip_w`/`clip_h`. Or ces dimensions décrivent désormais le
**contenu**, pas le conteneur : en rognant d'abord, le gommage du logo se recale
tout seul sur le vrai coin de l'image. Avant, sur un clip letterboxé, il visait
à côté — décalé vers le bas de toute la hauteur de la bande.

Le delogo reste **réservé aux vidéos** : une image uploadée (affiche, visuel) n'a
pas de logo de chaîne, et le rectangle flouté l'abîmerait pour rien.

`setpts` divise les timestamps : `/0.5` étire (ralenti), `/1.4` compresse.

### Le flux optique — le filtre cher (l.1551)

```python
if entry.get("ramp_slow") and (config.get("speed_ramp") or {}).get("interpolate"):
    post = [f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"]
else:
    post = [f"fps={fps}"]
```

Un ralenti simple répète les images : le mouvement saccade. `minterpolate`
**invente** les images intermédiaires par estimation de mouvement. C'est ce qui
donne un vrai ralenti fluide.

**Le coût est réel** : 5 à 15× le temps d'encodage du segment. Mesuré sur un
rendu réel — 48,7 s contre 8,0 s pour une fenêtre de 8 s, soit ×6,1.

D'où la double condition. `ramp_slow` est le drapeau posé par
`_ramp_decision` ([04](04-build-edl.md)) : il vaut `True` **seulement** pour un
ralenti voulu par la règle de ramp, jamais pour un ralenti venant de
`clip_speed`.

Sans cette distinction, un preset à `clip_speed: 0.85` rendrait **tous** les
segments coûteux à interpoler — pour un ralenti global que personne n'a demandé
d'adoucir. Le réglage `speed_ramp.interpolate` permet de tout couper.

### Les trois layouts

**`split`** (l.1723) — duel, deux moitiés empilées :

```
[0:v] → split ──▶ crop moitié gauche ─▶ scale ─▶ [l1] ┐
              └─▶ crop moitié droite ─▶ scale ─▶ [r1] ┘─▶ vstack
```

**`blur`** (l.1736) — plan entier sur fond flouté :

```
[0:v] → split ──▶ scale+crop plein cadre ─▶ boxblur ─▶ eq(-6%) ─▶ [bg]
              └─▶ scale largeur ────────────────────────────────▶ [fg]
                                                    [bg][fg] ─▶ overlay centré
```

Le fond est assombri de 6 % pour que le plan net ressorte.

**`crop`** (l.1748) — le cas normal :

```python
x_expr = f"min(max(iw*{focus_x:.4f}-{width / 2:.0f},0),iw-{width})"
```

La fenêtre se cale sur `focus_x` ([03](03-cadrage.md)), bornée par
`min(max(...))` pour ne jamais sortir de l'image.

### Le shake (l.1631)

```python
pad_w, pad_h = (20, 38) if "shake" in effects else (0, 0)
x_expr = f"...+7*sin(n*7.3)..."
y_expr = f"...+7*cos(n*9.1)..."
```

`n` est le numéro de frame. Sinus et cosinus à des fréquences **différentes et
non harmoniques** (7,3 et 9,1) : la trajectoire ne se referme pas, le
tremblement ne paraît jamais périodique.

Le padding de 20×38 px donne la marge dans laquelle bouger sans révéler de bord
noir.

### `kenburns_filter(entry, config)` — l.1594

```python
z = f"1.02+0.10*on/{n}" if kb.get("zoom_dir", 1) > 0 else f"1.12-0.10*on/{n}"
pan = 1 if kb.get("pan_dir", 1) > 0 else -1
x = f"iw/2-(iw/zoom/2)+{pan}*(on/{n})*iw*0.04"
```

Zoom avant ou arrière, pan gauche ou droite, sur toute la durée du segment.

Les **sens sont tirés à la seed dans `build_edl`** (l.958), pas ici. Le filtre
n'est que déterministe. C'est ce qui garde tout le hasard concentré dans la
partie pure — le rendu ne contient aucun RNG.

### Le drawtext (l.1577)

```python
def _coerce(value, cast, default):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default

cap_x = max(0.0, min(1.0, _coerce(subs.get("x", 0.5), float, 0.5)))
cap_y = max(0.0, min(1.0, _coerce(subs.get("y", 0.74), float, 0.74)))
cap_size = max(8, _coerce(subs.get("size", 64), int, 64))
```

**Dernière ligne de défense avant le rendu.** Un formulaire vidé, un JSON
malformé, un `None` — tout retombe sur le défaut au lieu de faire planter le
rendu. Même principe que `generate_punchlines` qui dégrade en `[]`.

```python
f":x=w*{cap_x:.4f}-text_w/2:y=h*{cap_y:.4f}-text_h/2"
```

Le texte est **centré sur son point d'ancrage**, exprimé en fraction d'écran —
donc indépendant du format de sortie. `borderw=5` avec un contour noir à 90 %
assure la lisibilité sur n'importe quel fond.

### Étalonnage et grain (l.1262, l.1271)

```python
def color_grade_filter(grade: str) -> str:
    return {"chaud": "eq=gamma_r=1.06:gamma_b=0.94:saturation=1.05",
            "froid": "eq=gamma_b=1.06:gamma_r=0.94:saturation=0.98",
            "delave": "eq=saturation=0.72:contrast=0.94:brightness=0.03"}.get(grade, "")
```

Retourne `""` pour `neutre` ou un nom inconnu — le filtre n'est simplement pas
ajouté.

```python
frag = f"noise=alls={round(amount * 24)}:allf=t"
if amount >= 0.6:
    frag += ",rgbashift=rh=2:bh=-2"
```

Bruit temporel proportionnel ; au-delà de 0,6, une dérive chroma permanente
donne le look VHS. `amount` est clampé — l'UI n'impose pas de borne.

---

## `glitch_amount(accents)` — l.1284

```python
value = accents.get("glitch", False)
if isinstance(value, bool):
    return 0.6 if value else 0.0
try:
    return max(0.0, min(1.0, float(value)))
except (TypeError, ValueError):
    return 0.0
```

Le réglage était un booléen, il est devenu un nombre 0–1. Cette fonction lit les
deux : `True` → 0.6, `False` → 0.0, nombre → clampé.

Les presets enregistrés avant le changement continuent de marcher **sans
migration de base**.

---

## Le concat final (l.1493)

```python
"-f", "concat", "-safe", "0", "-i", str(concat_list),
"-ss", f"{config['start']:.6f}", "-t", f"{total:.6f}", "-i", str(audio_path),
"-map", "0:v:0", "-map", "1:a:0",
"-c:v", "copy",
"-c:a", "aac", "-b:a", config["audio_bitrate"],
"-bitexact", "-map_metadata", "-1",
"-shortest",
```

| Argument | Rôle |
|---|---|
| `-c:v copy` | pas de ré-encodage vidéo |
| `-ss` / `-t` sur l'audio | découpe la fenêtre dans le morceau original |
| `-map 0:v:0 -map 1:a:0` | vidéo de l'entrée 0, audio de l'entrée 1 |
| `-bitexact -map_metadata -1` | garde-fou de reproductibilité n°3 |
| `-shortest` | filet de sécurité si les durées divergent d'une frame |

L'audio n'est **jamais** découpé segment par segment : il est posé d'un bloc à
la fin. C'est la vidéo qui se cale sur lui, pas l'inverse.

---

## Ce qui est testé

`tests/test_beatsync_ambiance.py` · `test_end_scene.py` · `test_subtitles.py` ·
`test_images.py` · `test_speed_ramp.py`

`_segment_filters`, `_segment_input_args`, `color_grade_filter`, `grain_filter`,
`glitch_amount` et `kenburns_filter` retournent des **chaînes de caractères** —
donc testables sans lancer FFmpeg. Les tests vérifient que le fragment attendu
est présent (ou absent) selon l'entrée : `minterpolate` seulement si
`ramp_slow`, `delogo` absent sur une image, `tpad` allongé du figé.

`render` lui-même n'est pas testé unitairement : il ne fait qu'assembler des
morceaux déjà couverts.

---

## Les réglages

| Clé | Défaut | Effet |
|---|---|---|
| `crf` | `20` | qualité H.264 (plus bas = mieux) |
| `preset` | `"medium"` | vitesse/compression x264 |
| `audio_bitrate` | `"192k"` | AAC |
| `fps` | `30` | cadence de sortie |
| `delogo` | `True` | gomme le coin haut-gauche |
| `color_grade` | `"neutre"` | `chaud` \| `froid` \| `delave` |
| `grain` | `0.0` | 0–1, VHS au-delà de 0,6 |
| `clip_speed` | `1.0` | ralenti global, **sans** flux optique |
| `speed_ramp.interpolate` | `True` | flux optique sur les ralentis de ramp |
