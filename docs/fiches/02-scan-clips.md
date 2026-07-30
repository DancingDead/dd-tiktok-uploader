# 02 — Scan des clips

> `load_clips` · `classify_frames` · `content_rect` · `usable_intervals` · `_char_presence` · `scan_clips` · `find_final_scene` — lignes 261-629
> ← [01 analyse audio](01-analyse-audio.md) · → [03 cadrage](03-cadrage.md)

## Ce que fait ce bloc

Regarder chaque clip et répondre à une seule question :

> **Quelles portions de ce clip sont montables ?**

Un épisode d'animé de 20 minutes contient des génériques, des cartes
Crunchyroll, des fondus au noir, des plans de décor sans personne, des
dialogues figés. Rien de tout ça ne fait un edit. Le scan les élimine **une
fois**, et `build_edl` n'a plus qu'à piocher dans ce qui reste.

---

## `load_clips(folder)` — l.261

Métadonnées seulement : `ffprobe` donne largeur, hauteur, durée. Pas de
décodage.

```python
{"path": Path, "kind": "video"|"image", "duration": float|None,
 "width": int, "height": int, "ratio": float}
```

### Le tri (l.264) — garde-fou de reproductibilité n°1

```python
for path in sorted(Path(folder).iterdir()):
```

`iterdir()` suit l'ordre du **système de fichiers**, qui n'est pas garanti et
peut changer après une copie, un déplacement, un `rsync`. Comme `rng.choice()`
pioche dans une liste, un ordre différent donne un montage différent **à seed
égale**. Le `sorted` est ce qui rend la promesse tenable.

### `duration: None` pour les images (l.289)

Une image n'a pas de durée — `ffprobe` n'en renvoie pas. C'est un `None`
**assumé**, pas un oubli : le montage boucle l'image sur la durée du segment
(`-loop 1`, voir [06](06-rendu.md)). Les fonctions qui itèrent sur les durées
testent donc `if not clip.get("duration")`.

---

## `_scan_one(clip)` — l.546 : le décodage

```python
"-vf", f"fps={SCAN_FPS},scale={SCAN_W}:{SCAN_H}",   # 2 fps, 640x360
"-f", "rawvideo", "-pix_fmt", "rgb24", "-",         # brut sur stdout
```

FFmpeg sort des pixels bruts sur stdout, numpy les reshape sans copie. Pas de
fichier intermédiaire.

### Deux résolutions pour deux usages (l.509-512)

```python
small = frames.reshape(n, SMALL_H, SCAN_H // SMALL_H, SMALL_W, SCAN_W // SMALL_W, 3) \
              .mean(axis=(2, 4)).astype(np.uint8)
```

| Résolution | Sert à | Pourquoi |
|---|---|---|
| **640×360** | visages, contours | il faut du détail pour détecter un visage |
| **32×18** | couleur, mouvement | une dominante orange ou une diff inter-frames n'a besoin d'aucun détail |

Le `reshape` + `mean` fait une **moyenne par blocs de 20×20** en une seule
opération vectorisée. Travailler la couleur et le mouvement sur 576 pixels au
lieu de 230 400, c'est 400× moins de calcul pour un résultat identique.

---

## `classify_frames(frames, sample_dt)` — l.303 · **pure**

Trois verdicts par frame :

```python
orange = orange_pixels.mean(axis=(1, 2)) > 0.35   # carte Crunchyroll
black  = f.mean(axis=(1, 2, 3)) < 18.0            # générique, fondu
motion = np.abs(np.diff(f, axis=0)).mean(...) / 255.0   # 0..1
```

### Pourquoi `int16` (l.310)

```python
f = frames.astype(np.int16)
```

Les frames arrivent en `uint8`. Un `np.diff` sur des `uint8` **wrappe** :
`10 - 20` donne 246, pas −10. Tout le calcul de mouvement serait faux. Le cast
en entier signé est obligatoire, pas cosmétique.

### `motion[0] = motion[1]` (l.320)

La première frame n'a pas de précédente, donc pas de différence. Plutôt que de
la laisser à 0 — ce qui la ferait passer pour statique et couperait la plage
dès le départ — on lui recopie la valeur de la seconde.

---

## `_char_presence(frames)` — l.496

Le bloc le plus subtil. Il répond à : **y a-t-il quelqu'un à l'écran, où, et
sont-ils deux ?**

Retourne trois tableaux par frame :

| Sortie | Type | Usage |
|---|---|---|
| `presence` | 1.0 / 0.6 / 0.0 | filtre les plans vides |
| `interest_x` | 0..1 | où caler le crop 9:16 ([03](03-cadrage.md)) |
| `dual` | bool | duel → layout split ([03](03-cadrage.md)) + scène de fin |

### La détection à deux niveaux (l.419-440)

```
1. Cascade « visage d'animé »  → presence = 1.0   (assets/lbpcascade_animeface.xml)
2. Sinon, contours d'encre     → presence = 0.6   (Sobel dans la bande centrale)
3. Sinon                        → presence = 0.0   (plan vide)
```

Pourquoi deux niveaux : la cascade rate les plans de dos, les silhouettes, les
plans larges. Les contours d'encre les rattrapent — un personnage dessiné, même
de dos, produit des traits nets que le décor n'a pas. Le poids 0.6 dit
« probablement quelqu'un, avec moins de certitude ».

### Le masque du logo (l.418)

```python
gray[: int(height * 0.14), : int(width * 0.25)] = 0  # masque logo coin haut-gauche
```

Le logo Crunchyroll dans le coin faisait détecter un « visage » à répétition.
Plutôt que de filtrer après coup, on **noircit la zone avant la détection**. Le
problème est éliminé par construction.

### Bande centrale pour les contours, plein champ pour les visages

```python
x0, x1 = int(width * 0.30), int(width * 0.70)   # l.412
```

Asymétrie volontaire, expliquée l.402-405 :

- **Visages** : détectés n'importe où. Le crop intelligent (`focus_x`) ramènera
  le personnage dans le champ, donc un visage sur le bord reste utile.
- **Contours** : cherchés dans les 40 % centraux seulement. Un signal de texture
  au bord ne survivrait pas au crop 9:16 — le compter serait mentir.

### Le duel : double détection (l.426-434)

```python
if len(faces) >= 2:
    # Duel : détection STRICTE uniquement — le réglage permissif
    # voit des « visages » dans les textures de décor.
    strict = cascade.detectMultiScale(..., minNeighbors=5, minSize=(30, 30))
    if len(strict) >= 2:
        dual[i] = strict_x.min() < 0.4 * width and strict_x.max() > 0.6 * width
```

Le réglage permissif (`minNeighbors=2`) sert la **présence** : mieux vaut un
faux positif qu'un plan valide écarté. Mais pour le duel il produisait des
« deux visages » dans du feuillage ou de la brique.

D'où une seconde passe stricte, **uniquement quand le permissif a vu ≥2 visages**
— l'ordre évite de payer la double détection sur toutes les frames. Et la
condition de position (`< 0.4` et `> 0.6`) exige qu'ils soient de part et
d'autre : deux visages côte à côte ne sont pas un affrontement.

---

## `usable_intervals(...)` — l.371 · **pure**

Transforme les verdicts par frame en **plages temporelles**.

```python
ok = ~orange & ~black & (motion >= motion_min) & (presence > 0.0)
```

Puis parcours des runs consécutifs de `ok`, avec :

| Paramètre | Défaut | Rôle |
|---|---|---|
| `margin` | 0.5 s | rogne chaque bord — les transitions de plan sont sales |
| `min_len` | 1.0 s | une plage trop courte n'est pas montable |
| `motion_min` | 0.008 | seuil **par échantillon** |
| `interval_motion_min` | 0.05 | seuil **sur la moyenne de la plage** |

### Les deux seuils de mouvement — le point à comprendre

C'est expliqué l.284-287. Un seul seuil ne peut pas faire les deux jobs :

- **Seuil bas par échantillon (0,008)** : une scène de combat a des micro-pauses
  (l'instant avant l'impact). Un seuil élevé les traiterait comme des ruptures
  et **fragmenterait** la scène en dix bouts inutilisables.
- **Seuil élevé sur la moyenne (0,05)** : un pan d'établissement ou un dialogue
  figé bouge un peu, en continu. Il passerait le seuil par échantillon sans
  problème. On l'écarte **en bloc**, sur sa moyenne.

Un seuil unique bas garderait les pans ; un seuil unique haut casserait les
combats. Il en faut deux.

### La sentinelle (l.297)

```python
for i, good in enumerate([*ok, False]):  # sentinelle pour fermer la dernière run
```

Un `False` ajouté à la fin ferme la dernière plage. Sans ça, un clip qui se
termine en pleine scène utilisable perdrait cette scène — l'un des cas limites
les plus faciles à oublier.

---

## `scan_clips(clips, cache_dir)` — l.599 : le cache

C'est ce qui rend l'usine viable. Sans cache, un lot de 10 vidéos re-décoderait
30 clips 10 fois.

```
clé      = md5(chemin absolu)      → data/cache/scan/<md5>.json
validité = mtime du fichier
```

### Trois décisions

**1. Clé = chemin, pas contenu.** Hasher le contenu d'un mp4 de 500 Mo coûterait
plus cher que le scan. Le `mtime` suffit à détecter un remplacement.

**2. Corruption = miss, et version du schéma (l.612-627)**

```python
try:
    cached = json.loads(cache_path.read_text())
    if cached.get("version") == SCAN_CACHE_VERSION \
            and cached.get("mtime") == clip["path"].stat().st_mtime:
        _apply_scan_payload(clip, cached)
        continue
except (json.JSONDecodeError, OSError, KeyError):
    pass
```

Un process tué en pleine écriture laisse un JSON tronqué. On l'attrape, on
ignore, on re-scanne, on réécrit. Le cache **ne peut pas casser une génération** —
au pire il la ralentit.

`SCAN_CACHE_VERSION` (l.489) est comparé **en plus** du `mtime`, pas à sa place.
Il sert quand le *schéma* du payload change : les entrées écrites avant l'arrivée
de `crop` n'ont pas cette clé, et les lire telles quelles laisserait les clips
déjà scannés sans rognage — le défaut survivrait au correctif, en silence. Le
numéro vit **dans** le payload, donc une entrée périmée ne peut jamais être
appliquée à moitié.

Conséquence assumée : la génération qui suit un changement de version re-scanne
tout le catalogue, une fois.

**3. Le cache est mutualisé.** Indexé par chemin de clip, pas par niche. Deux
niches qui partagent un clip partagent son scan.

### Images ignorées (l.605)

```python
if clip.get("kind") == "image":
    continue  # rien à décoder ; sans clé `intervals` l'image est utilisable en entier
```

---

## `content_rect(frames)` — l.341 · **pure** : les bandes noires

Un extrait de film récupéré sur YouTube arrive letterboxé — deux bandes noires
en haut et en bas. Sans rognage elles survivent au cadrage 9:16 et se retrouvent
dans la vidéo finale ; pire, le `ratio` lu par `load_clips` décrit alors le
**conteneur** et non l'image, ce qui fausse le choix du layout (fiche 03).

La détection réutilise les frames que `_scan_one` décode déjà — aucun décodage
supplémentaire.

```python
luma = np.asarray(frames, dtype=np.float32).mean(axis=3)   # (N, H, W)
rows = np.percentile(luma.mean(axis=2), 95, axis=0)        # profil par ligne
cols = np.percentile(luma.mean(axis=1), 95, axis=0)        # profil par colonne
```

### Le 95ᵉ percentile, pas le maximum

Un sous-titre incrusté **dans** la bande, ou un flash isolé, n'apparaît que sur
une poignée de frames. Un maximum se ferait avoir et conclurait qu'il n'y a pas
de bande ; le percentile les ignore. C'est le genre de détail qui ne se voit
qu'en production.

### Segments continus depuis les bords seulement (`_edge_runs`, l.332)

Une ligne sombre isolée au milieu de l'image n'est pas une bande et ne doit rien
rogner. Seuls comptent le segment de tête et celui de queue de chaque axe. Les
bandes **latérales** (pillarbox) passent par le même code, sans effort
supplémentaire.

### Trois seuils, trois raisons

| Constante | Valeur | Rôle |
|---|---|---|
| `BAR_LUMA_MAX` | 16.0 | une bande reste sous cette luminance (0-255) |
| `BAR_MIN_FRACTION` | 0.015 | en dessous, c'est du bruit de bord, pas une bande |
| `BAR_MAX_TOTAL` | 0.30 | au-delà, c'est une scène de nuit → `None` |

Le dernier est le plus important : il empêche de **mutiler un plan sombre**. Les
letterbox réels tournent tous autour de 25 % (un 2.35:1 dans du 16:9 en donne
24,3 %, un pillarbox 4:3 exactement 25 %), donc 30 % les couvre avec de la marge.

**Limite assumée** : un format ultra-large type 2.76:1 donnerait 35,6 % de bandes
et serait refusé. Ces formats sont absents du catalogue, et abaisser la garde
ferait courir un risque bien pire.

### Ce que `_scan_one` en fait (l.571-574)

Le rectangle est stocké **en fractions** dans `clip["crop"]` — donc indépendant
de la résolution, ce qui compte parce que le scan force `scale=640:360` sans
préserver le ratio. Et `clip["ratio"]` est **corrigé pour décrire le contenu** :

```python
clip["ratio"] = (crop["w"] * clip["width"]) / (crop["h"] * clip["height"])
```

Cette correction existe **sur les deux chemins** — le scan réel et la relecture
de cache (`_apply_scan_payload`, l.594). Ne la faire qu'au scan réel la ferait
disparaître dès la deuxième génération, quand le cache prend le relais.

---

## L'invariant `intervals` — à retenir

Trois états **distincts**, et la différence compte :

| État | Signification | Effet dans `build_edl` |
|---|---|---|
| clé **absente** | pas scanné | clip entier utilisable |
| `[]` | scanné, rien d'exploitable | clip **exclu** |
| `[{...}]` | scanné, plages trouvées | on pioche dedans |

Le code correspondant (`intervals_of`, l.948) :

```python
def intervals_of(clip: dict) -> list[dict]:
    if "intervals" not in clip:  # pas scanné : clip entier utilisable
        return [{"start": 0.0, "end": clip["duration"], "motion": 1.0}]
    return clip["intervals"]  # scanné ([] = rien d'exploitable, clip exclu)
```

Confondre « absent » et `[]` casserait le CLI sans cache d'un côté, ou
ressusciterait les clips inutilisables de l'autre.

---

## `find_final_scene(clips, min_source)` — l.338

Choisit **la plage la plus badass** pour la scène de fin ([04](04-build-edl.md)).

### Chercher dans la queue seulement

```python
FINAL_SCENE_TAIL = 1 / 3
tail_start = clip["duration"] * (1.0 - FINAL_SCENE_TAIL)
```

En mode chrono, la fin de la timeline correspond à la fin de l'histoire. Le
climax d'un épisode est dans son dernier tiers, pas dans son intro.

### Le score

```python
FINAL_SCENE_WEIGHTS = {"dual": 1.0, "presence": 0.6, "motion": 0.6}
```

Le duel prime : deux personnages face à face, c'est l'affrontement. Présence et
mouvement départagent.

Le mouvement est **normalisé sur les candidats** (l.377), pas en absolu — sinon
un clip très agité écraserait le critère de présence à lui seul.

### Restreindre plutôt qu'écarter (l.366-369)

```python
start = max(interval["start"], tail_start)
```

Une plage qui commence avant le dernier tiers mais déborde dedans n'est pas
jetée : on garde sa portion utile. Une plage **entièrement** avant `tail_start`,
elle, est écartée.

### `min_source` — le paramètre qui évite un bug

L'appelant calcule combien de secondes de source la scène va consommer
(`(durée − freeze) × vitesse`) et le passe ici. Une plage plus courte n'est pas
candidate.

Sans ce plancher, le rendu lirait **au-delà** de la plage exploitable — donc
dans des images que le scan avait justement rejetées. Le ralenti d'une scène de
fin amplifie l'écart.

### Pure et sans RNG (l.356-357)

À catalogue égal, la scène est toujours la même. La reproductibilité ne dépend
pas d'elle. Le départage (l.387) est explicitement déterministe : meilleur
score, puis **nom de clip**, puis plage la plus tardive.

---

## Ce qui est testé

`tests/test_scan.py` · `tests/test_scan_cache.py` · `tests/test_end_scene.py`

`classify_frames`, `usable_intervals`, `interval_dual_ratio` et
`find_final_scene` sont **pures** : on leur passe des tableaux numpy fabriqués à
la main. Aucun FFmpeg, aucun fichier vidéo dans les tests.

`_scan_one` et `_char_presence` ne le sont pas (FFmpeg + OpenCV) — ils sont
testés indirectement.

---

## Les constantes

| Constante | Valeur | Note |
|---|---|---|
| `SCAN_FPS` | 2.0 | 2 échantillons/s |
| `SCAN_W, SCAN_H` | 640, 360 | détection |
| `SMALL_W, SMALL_H` | 32, 18 | couleur/mouvement |
| `EDGE_PRESENCE_THRESHOLD` | 0.008 | fraction de « trait d'encre » |
| `FINAL_SCENE_TAIL` | 1/3 | queue du clip |

## Piège d'environnement

```toml
"opencv-python-headless>=4.10,<5"
```

**OpenCV 5.x a retiré `CascadeClassifier`.** Le pin `<5` n'est pas de la
prudence, c'est une nécessité : la détection de visages d'animé repose
entièrement dessus.
