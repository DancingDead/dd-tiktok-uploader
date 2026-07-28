# Spec — Ralentis plus longs, « scène de fin », format carré

**Date** : 2026-07-28
**Statut** : validé, prêt pour plan d'implémentation

Suite directe du spec du 2026-07-28 sur les ramps de vitesse
(`2026-07-28-ramps-images-texte-fixe-design.md`), écrit après avoir regardé les
premiers rendus. Trois volets : les ralentis existants durent trop peu pour se
voir ; il manque une conclusion au montage quand le mode chronologique raconte
une histoire ; et le 9:16 recadre trop violemment les rushes d'animé, qui sont
en 16:9.

---

## Volet A — Les ralentis prennent leur temps

### Problème

`ramp_speed` attribue bien 0,5× au segment qui se termine sur un impact, mais ce
segment **subit** la grille de coupe : après le drop, le strobo coupe à chaque
beat, donc le ralenti dure environ un demi-beat (0,47 s à 128 BPM) et passe
presque inaperçu. Le rendu est « cool mais perfectible » précisément là.

### Décision de cadrage (issue du brainstorming)

Le levier retenu est la **durée**, pas l'intensité. La borne moteur de vitesse
reste `[0.5, 1.5]` et `slow` reste à 0,5 : écarté, descendre sous 0,5× (qui
aurait demandé de desserrer la borne et rendrait le flux optique obligatoire).

### Design

#### A.1 Fusion des coupes avant un impact

Nouvelle fonction **pure** :

```python
def merge_boundaries_before_impacts(cut_beats: list[tuple[float, int]],
                                    anchor: int, config: dict) -> list[tuple[float, int]]
```

Elle retire de `cut_beats` toute coupe dont l'indice de beat tombe dans
l'intervalle **`[impact - slow_beats, impact)`** — les segments concernés
fusionnent donc en un seul, plus long, qui se termine sur l'impact et reçoit le
ralenti.

Le beat d'impact lui-même n'est jamais retiré : la coupe sur l'impact est ce qui
donne son sens au motif (et, pour le drop, la coupe garantie pile sur le drop est
un invariant existant de `build_edl`).

Placement dans `build_edl` : juste après la construction de `cut_beats` et
**avant** le calcul des frontières quantifiées, donc avant `_ramp_decision`.
L'exemption `min_dur` juge ainsi la durée **fusionnée**, ce qui est le but — un
segment qui n'aurait pas été ramped parce qu'il durait 0,47 s le devient une fois
porté à 0,94 s.

#### A.2 Config

```python
"speed_ramp": {
    "slow": 0.5,
    "fast": 1.4,
    "impact_beats": 8,
    "slow_beats": 2,      # NOUVEAU : beats fusionnés avant un impact ; 0 = comportement actuel
    "min_dur": 0.25,
    "interpolate": True,
},
```

`slow_beats: 0` ou `1` reproduit exactement le comportement actuel (aucune coupe
à retirer) : le champ est son propre interrupteur, pas besoin d'un booléen de
plus.

#### A.3 Conséquences assumées

- **Coût d'encodage** : le flux optique s'applique désormais à des segments deux
  fois plus longs. Le surcoût par vidéo augmente à peu près d'autant. Il reste
  borné aux seuls ralentis de ramp (invariant acquis au spec précédent).
- **Seeds** : le nombre de coupes change, donc le tirage des clips change. Les
  vidéos déjà en bibliothèque ne se reproduiront plus à seed égale. L'invariant
  (même seed **et même config** → même vidéo) tient ; ce spec est une nouvelle
  frontière de génération.

---

## Volet B — Scène de fin

### Problème

Quand le mode `chrono` est actif, le montage suit la chronologie de l'histoire du
clip : la fin de la timeline correspond à la fin du récit. Mais le montage s'y
arrête sans conclusion — la dernière coupe est une coupe comme les autres. On
veut une **fin** : un ralenti long sur le climax, figé sur la dernière image.

### Décisions de cadrage (issues du brainstorming)

- Forme : **ralenti long qui fige sur la dernière image**. Écartés : le ralenti
  sans figé (trop sobre) et l'arrêt sur image seul (proche de ce que fait déjà
  une image du catalogue).
- Choix du plan : **la plage la plus intense du dernier tiers du clip**. Écartés :
  le duel le plus net où qu'il soit (casse la logique chronologique) et le pic de
  mouvement seul (une explosion sans personnage à l'écran).
- Déclenchement : **option indépendante**, cochée dans le preset. Elle fonctionne
  sans `chrono`, mais l'UI indique qu'elle prend son sens avec. Écartés :
  l'activation automatique avec `chrono` (change d'un coup le rendu de tous les
  presets existants) et le réglage sans effet quand `chrono` est éteint (un
  réglage qui ne fait rien en silence est une source de confusion).
- Durée : **8 beats, dont 1 s de figé** (≈ 3,75 s à 128 BPM). Écartés : 4 beats
  (l'effet de suspension ne s'installe pas) et 16 beats (un quart de la vidéo ;
  TikTok punit les fins qui traînent).

### Design

#### B.1 Trouver le moment

Nouvelle fonction **pure** :

```python
def find_final_scene(clips: list[dict], config: dict) -> dict | None
```

Retourne `{"clip": <dict clip>, "interval": <dict plage>}` ou `None`.

Pour chaque clip **vidéo scanné** (les images n'ont pas de plages, et une image
n'a pas de climax), elle ne retient que les plages dont le `start` tombe dans le
**dernier tiers** du clip (`start >= 2/3 × duration`) — la fin du récit — puis
les note :

| Signal | Poids | Ce que ça capte |
|---|---|---|
| `dual` | 1.0 | Deux personnages face à face — le duel |
| `presence` | 0.6 | Des personnages à l'écran, pas un décor |
| `motion` | 0.6 | De l'action, pas un plan contemplatif |

`presence` et `motion` sont déjà moyennés par plage par `usable_intervals`.
`dual` est un tableau **par échantillon** (`clip["dual"]`, pas d'agrégat par
plage) : la fraction de duel d'une plage se calcule comme le fait déjà le
cadrage dans `build_edl`, en découpant le tableau sur `scan_dt` —
`dual[int(start/dt) : ceil(end/dt)].mean()`.

`motion` n'est pas borné a priori ; il est normalisé par le maximum observé sur
les plages candidates avant pondération, pour qu'un clip très agité n'écrase pas
le critère de présence.

**Départage déterministe** : à score égal, la plage du clip dont le nom vient en
premier (les clips sont déjà triés par nom par `load_clips`), puis la plage la
plus tardive. Aucun tirage aléatoire — à catalogue égal, la scène de fin est la
même, ce qui préserve l'invariant de reproductibilité sans consommer le `rng`.

**Dégradation** : si aucun clip n'a de plage exploitable dans son dernier tiers
(catalogue non scanné, clips trop courts), la fonction retourne `None` et le
montage se termine normalement. L'usine ne casse pas sur un cas dégradé — même
principe que `generate_punchlines` qui dégrade en `[]`.

#### B.2 Monter la scène

Dans `build_edl`, quand `end_scene["enabled"]` et que `find_final_scene` a
retourné une scène :

1. Une frontière est **posée** sur le beat le plus proche de
   `fin_de_fenêtre - beats × durée_de_beat`, et toutes les frontières situées
   **après** elle sont retirées. Poser la frontière est nécessaire : sans ça, le
   segment final commencerait à la dernière coupe existante avant la fenêtre, qui
   peut être bien plus tôt et donnerait une scène de durée arbitraire.
2. Ce segment porte la scène trouvée, à `end_scene["speed"]`, avec
   `ramp_slow: True` (c'est un ralenti voulu, pas un effet de `clip_speed`), et
   le strobo n'a plus cours dessus.
3. Le point d'entrée est calé sur la **fin** de la plage retenue — le climax est
   la conclusion du plan, pas son début :
   `clip_in = interval["end"] - source_needed`, borné à `interval["start"]`.
4. L'entrée porte `freeze: end_scene["freeze"]`.

Le cadrage (`focus_x`, `layout`) est calculé comme pour n'importe quel segment,
par la logique existante.

#### B.3 Le figé, sans filtre nouveau

Le rendu force déjà un nombre de frames exact via `tpad=stop_mode=clone` +
`-frames:v` : `tpad` clone la dernière image quand la source est trop courte.
Il suffit donc de **consommer moins de source** :

```
source_needed = (duration - freeze) * speed
```

FFmpeg clone la dernière frame pendant la durée restante. Aucune modification de
la chaîne de filtres, et l'invariant du nombre de frames exact tient tel quel.

Un seul ajustement : `tpad` est aujourd'hui appelé avec `stop_duration=1`, ce qui
plafonne le clonage à une seconde. Il devient `stop_duration = 1 + freeze` pour
laisser la marge nécessaire.

`_segment_input_args` applique le même retrait au `-t` d'entrée.

#### B.4 Config

```python
"end_scene": {
    "enabled": False,   # opt-in : aucun preset existant ne change de rendu
    "beats": 8,         # durée totale de la scène, en beats
    "freeze": 1.0,      # s de figé à la toute fin
    "speed": 0.5,
},
```

#### B.5 UI

Case à cocher dans l'éditeur de preset, libellée « Scène de fin », avec la
mention « ralenti long sur le climax, figé sur la dernière image — prend son sens
avec le mode chronologique ». Les trois valeurs numériques suivent le mécanisme
d'overrides existant, avec coercition et bornage côté serveur
(`coerce_overrides`) : `beats` dans `[2, 32]`, `freeze` dans `[0.0, 3.0]`,
`speed` dans `[0.5, 1.5]`.

---

## Volet C — Format vertical ou carré

### Problème

Le montage sort toujours en 9:16 (1080×1920). Les clips sources sont des rushes
d'animé en 16:9 : le recadrage vertical en jette **68 % de la largeur**. En 1:1
il n'en jette que 44 %, et le résultat tient souvent mieux — notamment pour
l'animé, où la composition est large.

`width` et `height` sont déjà des champs de config dont tout le cadrage dérive,
mais ils ne sont exposés nulle part : ni dans les réglages, ni dans les overrides
de preset. Seuls des commentaires et la description du CLI supposent le 9:16.

### Décisions de cadrage (issues du brainstorming)

- Le format se choisit **dans le preset**. Écartés : les réglages globaux (il
  faudrait basculer toute l'usine pour tester un carré sur une niche) et la niche
  (on ne pourrait plus comparer les deux formats sur le même contenu sans
  dupliquer la niche). Au niveau preset, une niche qui alterne deux presets
  produit les deux formats dans le même lot, sur les mêmes morceaux.
- Les deux cadrages de secours **s'adaptent au format**. Écartés : garder la
  logique telle quelle (duels empilés en bandes 2:1, fonds floutés inutiles) et
  le crop seul en carré (on perdrait le plan d'ensemble sur les sources très
  larges).

### Design

#### C.1 Le champ

```python
"format": "vertical",   # "vertical" = 1080x1920 | "carre" = 1080x1080
```

Fonction **pure** :

```python
FORMATS = {"vertical": (1080, 1920), "carre": (1080, 1080)}

def apply_format(config: dict) -> dict
```

Elle pose `width` et `height` d'après `format`, sans muter l'entrée. Un format
inconnu retombe sur `"vertical"` — dégradation sûre, même principe que `section`
et `subtitles.mode`.

Appelée en tête de `generate_video`, le **point de passage unique** par lequel
entrent le CLI et l'usine par niche. `width`/`height` restent les champs que lit
le rendu ; `format` est seulement ce qui les pose. Le reste de la chaîne de
filtres dérive déjà de ces deux nombres et n'est pas touché.

#### C.2 Les deux règles de cadrage, exprimées en ratio de sortie

Dans `build_edl`, le choix du layout devient fonction du ratio de sortie
`out_ratio = width / height` (0,5625 en vertical, 1,0 en carré) :

**Split** (duel, moitiés gauche/droite empilées) — condition ajoutée :
`out_ratio <= 0.75`. Vrai en vertical, faux en carré. En 1:1 un crop centré tient
déjà les deux personnages dans la plupart des plans, là où l'empilement donnerait
deux bandes 2:1.

**Blur** (plan entier sur fond flouté) — condition ajoutée au test de dispersion
existant : `clip_ratio >= 2.0 * out_ratio`. En vertical le seuil vaut 1,125, donc
tout 16:9 y a droit et le **comportement actuel est préservé à l'identique**. En
carré il vaut 2,0 : un 16:9 (1,78) passe en crop, seul un scope 2.35:1 déclenche
le fond flouté.

Les deux conditions sont pures et dérivées de la config, donc testables sans
rendu.

#### C.3 Ce qui suit sans modification

- `delogo` est exprimé en fractions des dimensions du **clip source**.
- La punchline (`x`, `y`, `size`) est en fractions de la **sortie**.
- `kenburns_filter` prend déjà `s={width}x{height}`.
- Les deux dimensions restent paires — H.264 l'exige.

#### C.4 Validation et surfaces

`coerce_overrides` refuse un format inconnu en 400, comme il le fait déjà pour
`color_grade` et `section` (`ALLOWED_FORMATS = ("vertical", "carre")`).

UI : un choix à deux entrées dans l'éditeur de preset. CLI : `--format`, et la
description argparse (« Montage vidéo vertical 9:16… ») cesse de mentionner un
format unique.

### Hors périmètre de ce volet

Desserrer `min_presence` et les seuils de scan pour le carré. Le carré pardonne
beaucoup plus au recadrage, donc les plages écartées par le scan pourraient être
moins nombreuses — mais ça se décide en regardant des rendus, pas a priori.

---

## Interactions vérifiées

- **Images du catalogue** : exclues de `find_final_scene` (pas de plages), et la
  branche image de `build_edl` reste inchangée. Une image ne peut pas être la
  scène de fin.
- **Flux optique** : la scène de fin est un ralenti voulu ; elle est marquée
  `ramp_slow` et bénéficie donc de `minterpolate` quand `interpolate` est actif.
  C'est cohérent — c'est le plan le plus regardé de la vidéo.
- **`snap_end_to_phrase`** : inchangé. La scène de fin vit **dans** la fenêtre
  déjà calée sur une frontière de phrase ; elle n'étend pas la vidéo et ne
  désynchronise pas l'audio.
- **Mode `calm`** (sans drop) : l'ancre des impacts est déjà le premier beat de
  la fenêtre, la fusion du volet A fonctionne donc aussi. La scène de fin ne
  dépend pas du drop.
- **Volet A + volet B** : la fusion des coupes du volet A s'applique à la grille
  entière ; la réservation des derniers beats du volet B s'applique ensuite. Une
  coupe supprimée deux fois ne pose pas de problème (opérations idempotentes sur
  une liste de frontières).
- **Volet C + volet B** : la scène de fin passe par la logique de layout
  ordinaire, donc elle hérite des règles sensibles au format sans traitement
  particulier. En carré, un duel final sera recadré plutôt qu'empilé — ce qui est
  l'effet recherché.
- **Volet C + images du catalogue** : la branche image de `build_edl` choisit
  aujourd'hui son layout sur un seuil codé en dur (`ratio >= 1.2`), hérité du
  9:16. Il devient la **même règle** que pour les vidéos
  (`clip_ratio >= 2.0 * out_ratio`), pour qu'une image ne se comporte pas
  autrement qu'un clip dans le même format. Effet de bord assumé : en vertical le
  seuil passe de 1,2 à 1,125, donc les images dont le ratio tombe entre les deux
  gagnent un fond flouté qu'elles n'avaient pas.
- **Volet C + punchlines** : `x` et `y` étant des fractions de la sortie, une
  position réglée en vertical reste proportionnellement la même en carré. Le
  texte ne sort pas du cadre.

## Hors périmètre

- Reconnaissance sémantique du « coup ultime ». On ne dispose que de signal
  visuel bas niveau ; prétendre reconnaître un impact narratif serait mentir sur
  ce que fait le code. Le trio duel + présence + mouvement dans le dernier tiers
  est une approximation assumée, à juger à l'œil sur quelques rendus.
- Desserrement de la borne de vitesse `[0.5, 1.5]`.
- Transition spécifique vers la scène de fin (fondu, flash) : la coupe suffit.

## Tests

Tous purs, sans FFmpeg ni réseau :

- `merge_boundaries_before_impacts` : coupes retirées dans la fenêtre d'un
  impact, coupes hors fenêtre intactes, beat d'impact jamais retiré,
  `slow_beats: 0` sans effet.
- `build_edl` : le segment qui précède un impact dure bien `slow_beats` beats ;
  l'exemption `min_dur` juge la durée fusionnée.
- `find_final_scene` : le duel gagne à mouvement égal ; une plage du premier
  tiers n'est jamais retenue ; `None` sur un catalogue non scanné ; départage
  déterministe à score égal ; les images sont ignorées.
- `build_edl` avec `end_scene` : un seul segment sur les N derniers beats, dont
  la durée vaut bien N beats (et non une durée arbitraire héritée de la coupe
  précédente), à la bonne vitesse, avec `freeze` et `ramp_slow` posés, et
  `clip_in` calé sur la fin de la plage retenue.
- `end_scene` désactivé, ou `find_final_scene` retournant `None` : l'EDL est
  identique à celle produite sans la fonctionnalité.
- `_segment_input_args` : la source consommée est réduite du figé.
- `_segment_filters` : `tpad` porte `stop_duration = 1 + freeze`.
- `coerce_overrides` : bornage des trois nouvelles valeurs de `end_scene`, et
  refus d'un `format` inconnu.
- `apply_format` : les deux formats donnent les bonnes dimensions ; un format
  inconnu retombe sur vertical ; la config d'entrée n'est pas mutée.
- Layout sensible au format : en carré aucun segment ne reçoit `split` même sur
  un duel franc ; un 16:9 y passe en `crop` alors qu'il passe en `blur` en
  vertical ; une source 2.35:1 déclenche `blur` dans les deux formats.
- Non-régression du vertical pour les **vidéos** : à format `"vertical"`, le
  layout choisi pour un clip vidéo est celui d'avant ce spec (le seuil 1,125 y
  est plus permissif que le test de dispersion, qui reste le facteur limitant).
  Les **images** font exception, par le changement de seuil documenté plus haut —
  un test le verrouille explicitement plutôt que de le laisser passer pour une
  régression.
