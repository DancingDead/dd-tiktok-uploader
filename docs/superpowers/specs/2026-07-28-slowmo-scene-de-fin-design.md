# Spec — Ralentis plus longs et « scène de fin »

**Date** : 2026-07-28
**Statut** : validé, prêt pour plan d'implémentation

Suite directe du spec du 2026-07-28 sur les ramps de vitesse
(`2026-07-28-ramps-images-texte-fixe-design.md`), écrit après avoir regardé les
premiers rendus. Deux volets : les ralentis existants durent trop peu pour se
voir, et il manque une conclusion au montage quand le mode chronologique raconte
une histoire.

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
- `coerce_overrides` : bornage des trois nouvelles valeurs.
