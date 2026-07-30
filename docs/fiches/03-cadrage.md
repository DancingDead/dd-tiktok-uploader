# 03 — Cadrage

> `apply_format` · `snap_end_to_phrase` · `resolve_window` · `frame_extract` — lignes 107-113, 704-785
> ← [02 scan des clips](02-scan-clips.md) · → [04 build_edl](04-build-edl.md)

## Ce que fait ce bloc

Deux cadrages qui n'ont rien à voir mais portent le même nom :

| | Question | Fonction |
|---|---|---|
| **Cadrage temporel** | quelles 30 s du morceau ? | `resolve_window` |
| **Cadrage spatial** | quelle zone de l'image ? | `frame_extract` |

Le premier tourne une fois par vidéo, le second une fois par segment.

---

## `apply_format(config)` — l.107

```python
FORMATS = {"vertical": (1080, 1920), "carre": (1080, 1080),
           "horizontal": (1920, 1080)}
```

Pose `width` / `height` à partir de `format`. Trois lignes, trois décisions.

### Le point de passage unique

Appelée en tête de `generate_video` (l.1501), donc **le CLI et l'usine par niche
passent tous les deux par là**. Aucun autre endroit ne fixe les dimensions.

### Ne mute pas l'entrée (l.108)

```python
out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in config.items()}
```

Copie **profonde d'un niveau**. Dans `generate_niche.py`, la même config de base
sert à 10 variantes successives ; si `apply_format` mutait, la variante 2
hériterait des modifications de la variante 1.

### Format inconnu → vertical (l.109)

```python
FORMATS.get(out.get("format", "vertical"), FORMATS["vertical"])
```

Une valeur corrompue en base ne fait pas planter un lot de 10 vidéos. Même
philosophie que `section` inconnu, ou qu'une police absente.

### Pourquoi le carré existe

Le commentaire l.100-101 le dit :

> Le carré recadre beaucoup moins un rush 16:9 (44 % de la largeur jetée contre
> 68 % en vertical), ce qui sert notamment l'animé.

Un plan large d'animé perd les deux tiers de sa composition en 9:16. En 1:1 il
en garde davantage. C'est un arbitrage cadrage / occupation d'écran, pas une
préférence esthétique.

---

## `snap_end_to_phrase(...)` — l.657 · **pure**

**Problème** : une fenêtre de 30 s coupe la musique là où le chrono tombe.
Souvent en plein milieu d'une phrase musicale. À l'oreille : la vidéo s'arrête
« mal ».

**Solution** : étendre la fin au prochain multiple de 16 beats **après le drop**.

```python
phrase = phrase_beats * float(np.median(np.diff(beats)))
n = math.ceil((end - drop_time) / phrase - 1e-9)
if drop_time + n * phrase > track_duration:
    n = math.floor((track_duration - drop_time) / phrase + 1e-9)
return drop_time + n * phrase if n >= 1 else end
```

### `np.median`, pas `np.mean` (l.666)

La durée d'un beat est déduite des écarts entre beats détectés. `beat_track`
rate ou double occasionnellement un beat — ces erreurs produisent des écarts
aberrants qui **tirent la moyenne** mais laissent la médiane intacte.

### Repli sur la frontière précédente (l.668-669)

Si étendre dépasserait le morceau, on prend le multiple **inférieur** — plus
court que demandé, mais musical, plutôt que long et coupé net.

### Sans drop, inchangé (l.664)

Les phrases se comptent **à partir du drop**. Sans drop, pas d'origine, donc pas
de calage. On rend `end` tel quel.

---

## `resolve_window(analysis, config, start, duration)` — l.673

Le seul de ce bloc qui **mute** `config` — délibérément, c'est le point où la
fenêtre est fixée pour toute la suite. Il pose `drop_time`, `start`, `end`.

```
config["section"] == "calm" ?
│
├─ OUI ─▶ drop = None
│         auto_start = find_calm(...)          ← pas de drop en mode calme
│
└─ NON ─▶ drop = find_drop(...)
          auto_start = max(0, drop - buildup)  ← 10 s de montée par défaut
                       ou 0.0 si pas de drop

start explicite (--start) ? ─▶ il gagne toujours

duration == "full" ? ─▶ end = durée du morceau
                 sinon ─▶ end = min(start + duration, durée)
                          puis snap_end_to_phrase(...)
```

### Pourquoi le buildup (l.689)

```python
auto_start = max(0.0, drop - config["buildup"])
```

Commencer **au** drop, ce serait tomber dedans sans contexte. Les 10 s de montée
donnent la tension qui rend le drop satisfaisant. C'est aussi ce qui crée la
section `buildup` dans l'EDL — coupes lentes avant l'explosion.

Le `max(0.0, ...)` gère un drop à moins de 10 s du début.

### Pas de drop en mode calme (l.684)

`drop = None` est **posé explicitement**. Conséquence en cascade dans
`build_edl` : pas de strobo, pas de section drop, et l'ancre des impacts
retombe sur le premier beat de la fenêtre (l.735) — le motif de vitesse reste
actif, mais calé autrement.

### `duration == "full"` saute find_calm (l.685-686)

Chercher la fenêtre la plus calme n'a pas de sens si on prend tout le morceau.

---

## `frame_extract(clip, clip_in, source_needed, config)` — l.751 · **pure**

Pour un extrait précis, répond à deux questions :

```python
return focus_x, layout   # (0..1, "crop" | "split" | "blur")
```

Appelée **deux fois** dans `build_edl` : chemin ordinaire (l.1204) et scène de
fin (l.1015). Le même code cadre les deux — pas de duplication, donc pas de
divergence possible.

### Le minimum de 3 échantillons (l.762)

```python
i1 = max(i0 + 3, math.ceil((clip_in + source_needed) / dt))
```

Le scan échantillonne à 2 fps, soit un point toutes les 0,5 s. Un segment d'un
beat à 150 BPM dure 0,4 s → **un seul échantillon**. Sur un point isolé,
`np.std` vaut 0 et une moyenne de duel vaut 0 ou 1 — les deux critères
deviennent du bruit.

On force donc une fenêtre d'au moins 3 échantillons (1,5 s), quitte à déborder
un peu de l'extrait. Juger sur le voisinage vaut mieux que juger sur rien.

### Les trois layouts

```
crop   ─ la fenêtre 9:16 se cale sur focus_x        ← le cas normal
split  ─ moitié gauche / moitié droite empilées     ← duel
blur   ─ plan entier sur fond flouté                ← source très large
```

### `split` : duel **et** sortie verticale (l.778)

```python
if len(dual) and float(dual.mean()) >= 0.5 and out_ratio <= 0.75:
    return focus_x, "split"
```

Deux conditions :

1. **≥ 50 % de la fenêtre en duel** — un duel fugace ne justifie pas de casser
   la composition.
2. **Sortie verticale seulement.** En 1:1, chaque moitié deviendrait une bande
   2:1 écrasée ; et le crop carré tient déjà les deux personnages. Le split
   résout un problème qui n'existe pas en carré.

### `blur` : deux conditions aussi (l.782)

```python
if float(window_x.std()) >= 0.18 and clip.get("ratio", 1.0) >= 2.0 * out_ratio:
    return focus_x, "blur"
```

1. **σ ≥ 0,18** : le centre d'intérêt se déplace beaucoup pendant l'extrait. Un
   crop fixe raterait l'action ; un crop mobile serait du jitter.
2. **Source ≥ 2 × le ratio de sortie** — et le facteur 2 change tout selon le
   format :

| Format | Seuil effectif | Ce qui passe |
|---|---|---|
| vertical (0,5625) | **1,125** | tout le 16:9 (1,78) |
| carré (1,0) | **2,0** | seulement du scope (2,35) |

En vertical, le blur est fréquent. En carré, il est réservé au cinémascope —
parce qu'un 16:9 dans un carré ne perd presque rien au crop.

### Sans données de scan → centré (l.758)

```python
if "interest_x" not in clip:
    return 0.5, "crop"
```

Un clip non scanné se recadre au centre. Dégradation silencieuse, cohérente
avec `intervals_of` ([02](02-scan-clips.md)).

### Le recalage sur le contenu (l.766-772)

```python
crop = clip.get("crop")
if crop and crop["w"] > 0:
    focus_x = float(np.clip((focus_x - crop["x"]) / crop["w"], 0.0, 1.0))
```

`interest_x` est mesuré sur le cadre **entier**, bandes noires comprises
([02](02-scan-clips.md)). Pour un letterbox horizontal, cela ne change rien :
`crop["x"] = 0` et `crop["w"] = 1`, la formule est l'identité.

Mais pour un **pillarbox**, le centre d'intérêt pointerait à côté une fois le
rognage appliqué — on aurait corrigé un défaut en en créant un autre. D'où le
remappage du cadre entier vers le contenu.

Un point tombant dans une bande est ramené dans les bornes par le `clip`.

> Un chemin de repli échappe au remappage : un clip scanné dont la fenêtre de
> scan est vide rend `0.5` sans passer par ici, soit le centre du cadre et non
> celui du contenu. Cas étroit, sans conséquence pour un letterbox horizontal.

---

## Le cas des images

Les images ne passent **pas** par `frame_extract` — le scan n'a pas tourné sur
elles, il n'y a ni `interest_x` ni `dual`. `build_edl` applique une règle
réduite (l.1230-1232) :

```python
"layout": ("blur"
           if clip["ratio"] >= 2.0 * (config["width"] / config["height"])
           else "crop"),
```

**Seul le seuil blur s'applique**, avec la même formule. Jamais `split` : le
duel n'est noté que sur les clips scannés.

---

## Ce qui est testé

`tests/test_framing.py` · `tests/test_format.py` · `tests/test_phrase_end.py`

Les quatre fonctions sont **pures et sans RNG**. `frame_extract` se teste en
fabriquant un `clip` avec un `interest_x` choisi — un tableau qui oscille pour
déclencher le blur, une dispersion faible pour rester en crop.

---

## Les réglages

| Clé | Défaut | Effet |
|---|---|---|
| `format` | `"vertical"` | 1080×1920 ou 1080×1080 |
| `buildup` | `10.0` | s de montée avant le drop |
| `phrase_beats` | `16` | longueur d'une phrase musicale |
| `section` | `"drop"` | `"calm"` bascule sur `find_calm` |

Les seuils de `frame_extract` (0,5 de duel, 0,18 de σ, facteur 2,0, minimum 3
échantillons) sont **en dur**. Ce sont des constantes de jugement visuel, pas
des préférences utilisateur.
