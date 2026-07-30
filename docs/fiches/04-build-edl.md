# 04 — build_edl ★ le cœur

> `build_edl` · `is_impact` · `ramp_speed` · `merge_boundaries_before_impacts` · `free_windows` — lignes 121-233, 787-1250
> ← [03 cadrage](03-cadrage.md) · → [05 punchlines](05-punchlines.md)

## Ce que fait ce bloc

Construire l'**Edit Decision List** : la liste des segments de la vidéo. Pour
chacun — quel clip, à partir de quelle seconde, pendant combien de temps, à
quelle vitesse, avec quels effets, cadré comment.

C'est la fonction la plus complexe du projet (~300 lignes) et la plus
importante à comprendre : **tout le montage se décide ici**.

## La propriété qui rend ça tenable

```python
def build_edl(analysis: dict, clips: list[dict], config: dict, seed: int) -> list[dict]:
    """Construit l'Edit Decision List. Logique pure : aucun I/O, déterministe à seed égal."""
```

**Aucun I/O.** Pas de fichier lu, pas de FFmpeg lancé, pas de pixel touché. Des
dicts en entrée, une liste de dicts en sortie.

Ce que ça permet concrètement : tester le rythme des coupes, la position du
strobo, l'exemption des ralentis courts — en millisecondes, sans un seul
fichier vidéo. Les 5 fichiers de tests qui couvrent cette fonction tournent
sans FFmpeg.

---

## Le plan de la fonction

```
1. Percentiles d'énergie par beat            l.784-795
2. Localiser le drop                         l.797-804
3. Marcher beat par beat → cut_beats         l.818-830
4. Fusionner avant les impacts               l.838
5. Quantifier → boundaries                   l.840-851
6. Réserver la scène de fin                  l.857-891
7. Pour chaque segment : clip, plage, effets l.910-1153
```

---

## Étape 1 — Les percentiles d'énergie (l.784)

```python
beat_energy = np.interp(beats, energy_times, energy)
ranks = beat_energy.argsort().argsort()
percentiles = (ranks + 0.5) / max(1, len(beats))
```

Chaque beat reçoit son **rang percentile** d'énergie. Trois tiers en découlent :
calme / moyen / intense.

### Sur le morceau ENTIER, pas la fenêtre

Le commentaire l.784-786 est explicite :

> Rang percentile d'énergie de chaque beat, calculé sur le morceau **ENTIER**
> (pas la fenêtre) : 30 s de pur drop => coupes rapides partout.

C'est le point le moins intuitif du fichier. Si on normalisait sur la fenêtre,
une fenêtre entièrement dans le drop aurait quand même un « tiers calme » — ses
30 % les moins énergiques. On monterait lentement des passages qui sont
objectivement intenses.

En normalisant sur le morceau, « intense » veut dire *intense par rapport à ce
morceau*, pas *par rapport à cette fenêtre*. Une fenêtre de drop est alors
intense de bout en bout, et coupe vite partout.

### `argsort().argsort()`

Le double `argsort` transforme des valeurs en rangs. Le premier donne l'ordre,
le second donne la position de chaque élément dans cet ordre. Idiome numpy
classique.

---

## Étape 2 — Localiser le drop (l.797)

```python
if drop_time is not None and start <= drop_time < end:
    drop_idx = int(np.argmin(np.abs(beats - drop_time)))
    if not (start <= beats[drop_idx] < end):
        drop_idx = None
```

Double vérification : le drop doit être dans la fenêtre, **et** le beat sur
lequel il se cale aussi. Un drop juste avant `end` pourrait se caler sur un beat
situé après — le second test l'attrape.

`drop_idx is None` déclenche partout ailleurs le mode « sans drop » : pas de
strobo, `section = "main"`, ancre d'impact sur le premier beat.

---

## Étape 3 — Marcher beat par beat (l.818)

```python
i, last = int(in_window[0]), int(in_window[-1])
while i <= last:
    cut_beats.append((float(beats[i]), i))
    nxt = i + step_at(i)
    if drop_idx is not None and i < drop_idx < nxt:
        nxt = drop_idx  # garantit une coupe pile sur le drop
    i = nxt
```

### `step_at(i)` — le rythme (l.806)

```python
def step_at(i: int) -> int:
    if drop_idx is not None and drop_idx <= i < drop_idx + strobe_beats:
        return 1  # strobo au drop, quelle que soit l'énergie
    if config["cut_mode"] == "fixed":
        return max(1, int(config["cut_every"]))
    p = percentiles[i]
    return intense_step if p >= high_thr else mid_step if p >= low_thr else calm_step
```

L'ordre des tests **est** la hiérarchie des règles :

1. **Strobo** — 16 beats après le drop, on coupe à chaque beat. Priorité
   absolue, l'énergie ne peut pas l'annuler.
2. **Mode fixe** — `--cut-every N` court-circuite l'énergie.
3. **Énergie** — 4 beats (calme) / 2 (moyen) / 1 (intense).

### La coupe garantie sur le drop (l.826)

C'est **le** détail qui fait tout tenir. Sans ce test, un pas de 4 beats peut
enjamber le drop : la coupe tomberait 2 beats avant ou après, et le montage
raterait le moment que toute la fenêtre construit.

On détecte l'enjambement (`i < drop_idx < nxt`) et on force le prochain arrêt
sur `drop_idx`. Le pas suivant repart normalement.

---

## Étape 4 — Fusionner avant les impacts (l.838)

### Le concept d'impact (l.119)

```python
def is_impact(beat_index: int, anchor: int, impact_beats: int) -> bool:
    if beat_index < 0 or impact_beats <= 0:
        return False
    return (beat_index - anchor) % impact_beats == 0
```

Un **impact** = le drop, et tous les beats espacés d'un multiple de
`impact_beats` (8 par défaut), **avant comme après**. Une grille de temps forts
alignée sur le drop.

`beat_index < 0` = borne de fenêtre, jamais un impact.

### Le motif de vitesse (l.149)

```python
def ramp_speed(start_beat, end_beat, duration, anchor, config) -> float:
```

| Le segment… | Vitesse | Effet |
|---|---|---|
| **finit** sur un impact | `slow` (0,5) | ralenti d'anticipation |
| **commence** sur un impact | `fast` (1,4) | accéléré de relance |
| ni l'un ni l'autre | `clip_speed` (1,0) | normal |
| plus court que `min_dur` | `clip_speed` | exempté |

Quand les deux s'appliquent, **le ralenti gagne** — l'anticipation prime.

Le « gasp » avant le drop, effet signature du montage, n'est pas codé à part :
c'est ce motif appliqué au drop, qui est un impact comme un autre.

### Le problème que résout la fusion (l.158)

Voilà le point le plus subtil du fichier. Pendant le strobo, on coupe **tous les
beats**. Un segment ralenti à 0,5× dure donc un demi-beat. À 150 BPM : 0,2
seconde. **Invisible.**

```python
def distance_to_next_impact(beat_index: int) -> int:
    return (anchor - beat_index) % impact_beats  # 0 si le beat EST un impact

kept = [(t, b) for t, b in cut_beats
        if not (0 < distance_to_next_impact(b) < slow_beats)]
```

On **retire les coupes** situées dans les `slow_beats` beats avant un impact.
Les segments concernés fusionnent en un seul, plus long, qui se termine sur
l'impact et recevra le ralenti.

Le `0 <` exclut le beat d'impact lui-même : c'est lui qui porte le motif, et
pour le drop c'est une coupe garantie.

### Pourquoi `slow_beats` retire N−1 coupes (l.179-181)

> `slow_beats` est la LONGUEUR voulue du segment ralenti : pour qu'il couvre N
> beats, il faut retirer les N−1 coupes intermédiaires, donc les distances
> 1..N−1. À 1, rien n'est retiré — c'est la grille actuelle.

À `slow_beats: 1`, la fonction est un no-op. C'est ce qui rend le réglage
rétrocompatible.

### Le garde-fou (l.186)

```python
return kept or cut_beats
```

Une fenêtre sans aucun impact verrait toutes ses coupes disparaître — un
montage d'un seul plan de 30 s. On préfère la grille d'origine.

### L'ordre est critique (l.835-837)

> À faire **AVANT** la quantification, pour que l'exemption `min_dur` juge la
> durée fusionnée et non celle d'origine.

Si on fusionnait après, `_ramp_decision` verrait la durée d'un demi-beat,
appliquerait l'exemption `min_dur`, et refuserait le ralenti — sur un segment
qui, une fois fusionné, était largement assez long. La fusion et l'exemption
s'annuleraient mutuellement.

### `_ramp_decision` vs `ramp_speed` (l.130)

`_ramp_decision` retourne `(vitesse, ramp_slow)`. Le booléen dit si le ralenti
vient de la **règle de ramp** — par opposition à `clip_speed`, un réglage global
de preset qui produit lui aussi des ralentis.

Cette distinction sert **uniquement** au flux optique au rendu
([06](06-rendu.md)) : `minterpolate` coûte 5 à 15× le temps d'encodage. On ne le
déclenche que sur les ralentis voulus par la ramp. Sinon un preset à
`clip_speed: 0.85` rendrait **tous** les segments coûteux à interpoler.

---

## Étape 5 — Quantifier (l.840)

```python
out_end = round((end - start) * fps) / fps
boundaries = [(0.0, -1)]
for t, beat_index in cut_beats:
    cut = round((t - start) * fps) / fps
    if cut - boundaries[-1][0] >= frame - 1e-9 and cut <= out_end - frame + 1e-9:
        boundaries.append((cut, beat_index))
boundaries.append((out_end, -1))
```

Chaque coupe est arrondie à la frame la plus proche. Deux filtres : au moins
une frame d'écart avec la précédente, et pas trop près de la fin.

**Ici, pas dans `render`.** L'erreur est bornée à ½ frame par coupe et ne
s'accumule jamais. Voir la [vue d'ensemble](00-beatsync-vue-ensemble.md).

Le `-1` marque une borne de fenêtre — pas un beat, donc jamais un impact.

---

## Étape 6 — Réserver la scène de fin (l.857)

Opt-in (`end_scene.enabled`). Les N derniers beats deviennent **un segment
unique** portant le climax : ralenti long, puis figé sur la dernière image.

### Trois vérifications avant d'accepter

```python
fits_window = candidate >= frame and candidate <= out_end - frame
keeps_drop = not (drop_out is not None and candidate <= drop_out + frame)
```

| Test | Pourquoi |
|---|---|
| `fits_window` | une scène qui avale toute la fenêtre n'est plus une conclusion |
| `keeps_drop` | une scène qui commence avant ou sur le drop lui **volerait sa coupe** |
| `scene is not None` | il faut une plage exploitable dans la queue d'un clip |

Le second est un bug réel corrigé : la scène de fin écrasait la frontière du
drop et le montage perdait son moment fort.

### Le calcul de `min_source` (l.916-931)

```python
es_speed = _clamp_speed(es_cfg.get("speed", 0.5))
scene_duration = out_end - candidate
freeze = max(0.0, min(scene_duration, float(es_cfg.get("freeze", 1.0))))
es_source = (scene_duration - freeze) * es_speed
scene = find_final_scene(clips, min_source=es_source)
```

Combien de **source** consomme la scène ? La partie figée n'en consomme aucune
(elle clone une image), et le ralenti étire : `(durée − freeze) × vitesse`.

Ce plancher est passé à `find_final_scene` ([02](02-scan-clips.md)) pour
qu'elle n'écarte pas les plages trop courtes — sinon le rendu lirait au-delà,
dans du non-scanné.

### Poser la frontière (l.780-782)

```python
boundaries = [b for b in boundaries if b[0] < es_start - 1e-9]
boundaries.append((es_start, -1))
boundaries.append((out_end, -1))
```

On tronque et on repose. Sans ça, le segment final commencerait à la dernière
coupe existante, d'une durée arbitraire.

### `find_final_scene` retourne `None` → montage normal

Aucune exception. L'usine ne casse pas sur un catalogue non scanné.

---

## Étape 7 — La boucle d'attribution (l.797)

Pour chaque paire de frontières consécutives :

```
duration = seg_end - seg_start
tier     = calm | mid | intense           ← percentile du beat
section  = buildup | drop | main          ← position vs drop
speed, ramp_slow = _ramp_decision(...)
```

### 7a — Scène de fin (l.815)

Court-circuit. **Pas de tirage** — la scène a été choisie hors du rng, par
`find_final_scene`, de façon déterministe.

```python
clip_in = max(interval["start"], interval["end"] - es_source)
```

Le climax est la **conclusion** du plan : on cale l'entrée sur sa fin, pas sur
son début.

L'entrée porte `end_scene: True`, `ramp_slow: True` (ce ralenti mérite le flux
optique), `freeze`, et `effects: []` — la scène se suffit, ni shake ni glitch.

### 7b — Les effets (l.848)

```python
if effects_cfg.get("zoom") and (tier == "intense" or section == "drop"):
    effects.append("zoom")
if section == "drop":
    if effects_cfg.get("flash") and drop_seg_count % 8 == 0:
        effects.append("flash")
        if accents.get("rgb"):
            effects.append("rgb")
    if effects_cfg.get("shake") and (
        drop_seg_count == 0 or (tier == "intense" and rng.random() < 0.3)
    ):
        effects.append("shake")
    if drop_seg_count > 0 and tier == "intense" \
            and rng.random() < glitch_amount(accents):
        effects.append("glitch")
    drop_seg_count += 1
elif effects_cfg.get("shake") and tier == "intense" and rng.random() < 0.3:
    effects.append("shake")
```

| Effet | Règle | Intention |
|---|---|---|
| `zoom` | tier intense ou section drop | punch |
| `flash` + `rgb` | tous les 8 segments de drop | marque les temps forts |
| `shake` | 1er segment du drop, puis 30 % des intenses | impact d'entrée |
| `glitch` | ~25 % des intenses du drop, **jamais le premier** | texture |

`drop_seg_count` compte les segments **de la section drop**, pas les segments
tout court. C'est ce qui aligne le flash sur la structure musicale plutôt que
sur le début de la vidéo.

Les `rng.random()` viennent tous du `random.Random(seed)` local → même seed,
mêmes effets.

### 7c — Choisir le clip (l.868)

```python
source_needed = duration * speed
usable = [c for c in video_clips
          if any(iv["end"] - iv["start"] >= source_needed for iv in intervals_of(c))]
```

**`duration × speed`** : un segment de 1 s à 0,5× consomme 0,5 s de source ; à
1,4× il en consomme 1,4. Un clip trop court pour le ralenti peut suffire pour
l'accéléré.

Les images ne rejoignent le pool que sous deux conditions (l.873) :

```python
if image_clips and duration <= IMAGE_MAX_DUR + 1e-9 \
        and seg_index - last_image_seg >= IMAGE_MIN_GAP:
```

`IMAGE_MAX_DUR = 0.6 s` (au-delà, un fixe casse le rythme) et `IMAGE_MIN_GAP = 3`
segments (anti-diaporama).

**La seule exception à « ne jamais bloquer »** (l.876) : si `usable` est vide, on
lève. Il n'y a rien à dégrader.

```python
pool = [c for c in usable if c["path"] != prev_path] or usable
clip = rng.choice(pool)
```

On évite de reprendre le clip précédent — sauf s'il est le seul possible.

### 7d — Les images (l.886)

Pas de plage à choisir, pas de ralenti sur un fixe (`speed: 1.0`). Le zoom
ordinaire est **retiré** et remplacé par un Ken Burns dont les sens sont tirés à
la seed :

```python
"effects": [e for e in effects if e != "zoom"] + ["kenburns"],
"kenburns": {"zoom_dir": rng.choice([1, -1]), "pan_dir": rng.choice([1, -1])},
```

Les deux seraient redondants — et le Ken Burns est ce qui empêche l'image de
paraître figée.

### 7e — Choisir la plage (l.918)

```python
candidates = [iv for iv in intervals_of(clip) if iv["end"] - iv["start"] >= source_needed]
min_presence = config.get("min_presence", 0.0)
candidates = [iv for iv in candidates if iv.get("presence", 1.0) >= min_presence] or candidates
```

Le `or candidates` est un **fallback** : si toutes les plages sont sous le seuil
de présence, on garde la liste complète plutôt que de planter. Motif récurrent
dans le fichier.

### 7f — Le mode chrono (l.1074) — la plus belle idée du fichier

```python
# Cible calculée sur les plages COMPLÈTES (stable), jamais sur les fenêtres libres.
full = [iv for iv in intervals_of(clip)
        if iv["end"] - iv["start"] >= source_needed] or intervals_of(clip)
progress = seg_start / out_end if out_end > 0 else 0.0
full_slacks = [iv["end"] - iv["start"] - source_needed for iv in full]
target = progress * sum(full_slacks)
desired_iv, desired_offset = full[-1], full_slacks[-1]
for iv, slack in zip(full, full_slacks):
    if target <= slack:
        desired_iv, desired_offset = iv, target
        break
    target -= slack
desired = desired_iv["start"] + desired_offset

floor = max((end for _, end in consumed.get(clip["path"], [])), default=0.0)
ordered = [w for w in candidates
           if w["end"] - source_needed >= floor - 1e-9] or candidates
window = min(ordered, key=lambda w: abs(
    min(max(desired, w["start"]), w["end"] - source_needed) - desired))
clip_in = min(max(desired, window["start"]), window["end"] - source_needed) \
    + rng.uniform(0.0, 1.0)
clip_in = min(max(clip_in, window["start"]), window["end"] - source_needed)
```

**Le principe** : position dans la timeline ≈ position dans l'histoire du clip.

À 50 % de la vidéo, on pioche vers 50 % de l'histoire. La conséquence est
recherchée : **le drop tombe sur le climax**, parce que le drop est vers la fin
de la fenêtre et que la fin de la fenêtre pointe vers la fin de l'histoire.

Le mécanisme se fait en deux temps depuis l'arrivée de la mémoire de
consommation (§7g) : on calcule d'abord une position **voulue** (`desired`) sur
les plages complètes, puis on choisit la **fenêtre libre la plus proche** de
cette position.

Ce découpage n'est pas cosmétique. Calculer la cible sur les fenêtres libres
paraîtrait plus direct, mais celles-ci **rétrécissent à chaque consommation** :
faire porter la même fraction de progression sur un total qui rapetisse fait
s'emballer la position vers la fin du clip bien avant la fin de la timeline. Le
dénominateur doit rester stable.

Trois protections :

| Ligne | Rôle |
|---|---|
| `+ rng.uniform(0.0, 1.0)` | jitter seedé — deux variantes ne piochent pas exactement pareil |
| filtre sur `floor` | **monotonie par clip** : une fenêtre peut être libre tout en étant *avant* ce qui a déjà servi ; y revenir ne serait pas un rejeu, mais romprait la chronologie |
| `min(max(...))` | reste dans la fenêtre, et laisse la place à `source_needed` |

Le `floor` est la **fin** de la dernière portion consommée, pas son début : avec
le début, un nouvel extrait pourrait chevaucher le précédent tout en satisfaisant
la règle.

La monotonie est ce qui donne l'impression d'une histoire qui avance plutôt que
d'un shuffle.

> Le `or candidates` de la ligne `ordered` n'est pas mort : il est vivant sur les
> deux chemins de repli (§7g), qui reconstruisent `free` sans réappliquer le
> plancher. C'est là, et seulement là, que le mode chrono peut reculer.

### 7g — La mémoire de consommation (l.855, 994-1032, 1152) — l'anti-répétition

Avant, des coupes tombaient entre deux extraits quasi identiques du même plan :
un effet de retour en arrière qui cassait la fluidité. Trois causes se
combinaient, toutes mortes aujourd'hui.

| Cause | Ce qui se passait | Où c'est mort |
|---|---|---|
| Garantie de progression à `+ 0.1` s | un clip revu trois plans plus loin rejouait quasiment les mêmes images | remplacée par `free_windows` |
| Clamp appliqué **après** la garantie | quand la cible dépassait la plage, le point d'entrée redescendait **sous** la position précédente — un vrai retour en arrière | la garantie est désormais dans le choix de la fenêtre, pas dans une correction après coup |
| Mode libre sans mémoire | `rng.uniform` tirait n'importe où, sans savoir ce qui avait déjà servi | `candidates` vient de `free_windows` |

`build_edl` tient `consumed: dict[Path, list[tuple[float, float]]]` — les
portions déjà montrées, par clip. `free_windows` (fiche 07) en déduit ce qui
reste, en élargissant chaque portion consommée d'une **marge de 0,5 s** : sans
elle, un nouvel extrait pourrait démarrer exactement là où le précédent
s'arrêtait et rester visuellement identique.

Cette mémoire alimente **trois** points de décision : le filtre des clips
utilisables, le mapping chrono ci-dessus, et le tirage libre.

**La scène de fin réserve sa portion avant la boucle** : son point d'entrée est
calculable dès la réservation des derniers beats, donc on l'inscrit dans
`consumed` à ce moment-là. Aucun segment ordinaire ne montre le climax par
avance.

**Deux replis distincts**, à ne pas confondre :

| Repli | Condition | Effet |
|---|---|---|
| **global** | aucun clip n'a de fenêtre libre assez longue | on rouvre les plages entières pour **tous** les clips |
| **de pool** | seul le clip précédent est utilisable | on rouvre les plages des **autres** clips |

Le second est un arbitrage produit : plutôt re-montrer un passage déjà vu d'un
autre clip qu'enchaîner deux plans du même. La coupure visuelle est ce que l'œil
remarque en premier. Le repeat immédiat ne reste permis qu'avec un seul clip
vidéo au catalogue.

**Ordre de grandeur** : une vidéo de 30 s consomme ~71 s de catalogue, dont ~42 s
de pure marge. Le genou d'épuisement est vers **150 s de matière scannée
exploitable** — trois rushes de 2 min dont le scan ne retient que 30 % (81 s)
épuisent ; cinq clips de 3 min passent largement.

### 7h — Le strobe de build-up (l.188, 951-1000) — l'effet blackout

`effects.blackout` (opt-in) transforme **tout le build-up** en une alternance
éclair d'image / écran noir, au pas de `blackout_beats` (0,5 beat par défaut).

`blackout_boundaries` (**pure et testée**) réécrit la grille d'avant le drop en
**comptant à rebours depuis le drop**. Ce sens de comptage n'est pas un détail :
il garantit que le segment qui se termine sur le drop est un **éclair d'image** et
jamais un noir — l'impact tombe donc sur une image. Compter depuis le début de la
fenêtre laisserait la parité au hasard de la durée du build-up.

Le **segment de tête** est forcé en image quelle que soit sa parité : une vidéo
qui s'ouvre sur du noir ressemble à un bug. On accepte donc deux éclairs
d'affilée au tout début.

Les frontières intermédiaires portent `-1` ; **celle du drop garde son indice de
beat**, qui sert la relance accélérée (`fast`) qui suit l'impact.

**Le « gasp » ne survit pas au strobe, et c'est voulu.**
`merge_boundaries_before_impacts` travaille sur `cut_beats`, donc *avant* la
grille de frontières ; `blackout_boundaries` réécrit ensuite tout le build-up et
jette cette fusion. L'éclair qui précède le drop passe alors sous `min_dur` et se
voit exempté de ralenti. Le clignotement court jusqu'à l'impact sans
interruption.

Une entrée noire porte `kind: "black"` et **rien d'autre** — ni clip, ni cadrage.
Elle sort de la boucle avant la sélection : aucun tirage seedé consommé, aucune
matière retirée au catalogue.

**Les images du catalogue sont exclues du strobe.** Un éclair dure 0,23 s, donc
sous `IMAGE_MAX_DUR` : sans cette exclusion, un tiers de la montée deviendrait un
diaporama d'images fixes, alors que l'intention est un plan différent à chaque
éclair.

**Limite connue** : sous ~70 s de matière scannée exploitable, la mémoire
anti-répétition s'épuise et le strobe dégénère en images quasi identiques qui
clignotent — exactement ce qu'il cherche à éviter.

### Le mode libre (l.1107)

```python
if (section == "drop" or tier == "intense") and len(candidates) > 1:
    median_motion = float(np.median([iv["motion"] for iv in candidates]))
    candidates = [iv for iv in candidates if iv["motion"] >= median_motion] or candidates
interval = rng.choice(candidates)
clip_in = rng.uniform(interval["start"], interval["end"] - source_needed)
```

`chrono: False` restaure le tirage libre, avec préférence pour les plages
nerveuses sur les moments intenses. Encore un `or candidates`.

---

## L'entrée d'EDL produite

```python
{
    "timeline_start": float,   # position dans la vidéo (0 = début fenêtre)
    "duration":       float,   # durée à l'écran
    "clip_path":      Path,
    "kind":           "video" | "image",
    "clip_in":        float,   # point d'entrée dans le clip source
    "beat_index":     int,     # -1 = borne de fenêtre
    "section":        "buildup" | "drop" | "main",
    "speed":          float,
    "ramp_slow":      bool,    # ← décide du flux optique au rendu
    "effects":        [str],
    "focus_x":        float,   # 0..1
    "layout":         "crop" | "split" | "blur",
    "clip_w", "clip_h": int,
    # scène de fin uniquement :
    "end_scene":      True,
    "freeze":         float,
    # images uniquement :
    "kenburns":       {"zoom_dir": ±1, "pan_dir": ±1},
}
```

`render` ne fait qu'exécuter ça. Aucune décision ne lui reste.

---

## Ce qui est testé

`test_build_edl.py` · `test_build_edl_v2.py` · `test_chrono.py` ·
`test_speed_ramp.py` · `test_end_scene.py` · `test_images.py`

Six fichiers pour une fonction — proportionné à ce qu'elle porte. Aucun ne
lance FFmpeg.

Les trois helpers extraits (`is_impact`, `ramp_speed`,
`merge_boundaries_before_impacts`) le sont **pour être testés isolément** : ils
concentrent la logique la plus délicate, et les vérifier séparément vaut mieux
que de les observer à travers 30 segments.

---

## Les réglages

| Clé | Défaut | Effet |
|---|---|---|
| `cut_mode` | `"energy"` | `"fixed"` = intervalle constant |
| `cut_every` | `2` | beats par coupe en mode fixe |
| `energy_thresholds` | `(0.40, 0.75)` | frontières calme/moyen/intense |
| `energy_intervals` | `(4, 2, 1)` | beats par coupe selon le tier |
| `strobe_beats` | `16` | durée du strobo après le drop |
| `chrono` | `True` | ordre chronologique dans le clip |
| `min_presence` | `0.3` | score minimal de personnages |
| `effects` | tous `True` | interrupteurs zoom/flash/shake/speed |
| `accents` | `{rgb, glitch}` | aberration chromatique, micro-glitch |
| `speed_ramp.slow` / `.fast` | `0.5` / `1.4` | vitesses des ramps |
| `speed_ramp.impact_beats` | `8` | périodicité des impacts ; 0 = pas de ramps |
| `speed_ramp.slow_beats` | `2` | beats fusionnés avant un impact |
| `speed_ramp.min_dur` | `0.25` | seuil d'exemption |
| `end_scene.*` | désactivé | `enabled`, `beats`, `freeze`, `speed` |
