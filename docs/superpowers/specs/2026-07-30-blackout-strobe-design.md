# Spec — Strobe de build-up entrelacé de noirs (`blackout`)

**Date** : 2026-07-30
**Statut** : validé, prêt pour plan d'implémentation

## Problème / objectif

Le montage dispose de deux effets d'impact, tous deux situés **au drop ou après** :
`strobe_beats` force des coupes à chaque beat pendant 16 beats après le drop, et
`effects.flash` pose un fondu blanc sur les temps forts de la section drop.

Rien ne travaille le **build-up**. On veut y installer une montée de tension :
un strobe fait d'éclairs d'image entrecoupés de noir, calé sur le rythme, qui
s'arrête pile sur le drop pour en accentuer l'impact.

## Décisions de cadrage (issues du brainstorming)

- **Un nouvel extrait à chaque éclair**, pas un plan unique qui clignote. Le
  rendu recherché est la mitraillette, pas le strobe sur une image fixe.
- **Toute la durée du build-up**, pas une borne de N derniers beats. Écarté : le
  cadrage sur les derniers beats (jugé trop timide) et la cadence accélérée
  (grammaire classique, mais un réglage de plus à équilibrer sans l'avoir vu).
- Le nom `blackout`, pas `flash` : ce dernier désigne déjà le fondu blanc des
  impacts, et deux effets homonymes rendraient la config illisible.

## Design

### 1. Configuration

```python
"effects": {"zoom": True, "flash": True, "shake": True, "speed": True,
            "blackout": False},        # opt-in : aucun preset existant ne change
"blackout_beats": 0.5,                 # durée d'un éclair ET d'un noir, en beats
```

`blackout_beats` vit au niveau racine, à côté de `strobe_beats` dont il est le
pendant côté build-up. Un seul nombre à régler : `0.5` donne un demi-temps
d'image puis un demi-temps de noir ; `0.25` double la fréquence ; `1.0` la
divise.

L'effet ne s'applique que si **`effects.blackout`** est vrai **et** qu'un drop
existe dans la fenêtre. Sans drop il n'y a pas de build-up, donc rien à faire —
dégradation silencieuse, comme partout ailleurs.

### 2. La grille, comptée à rebours depuis le drop

Fonction **pure** :

```python
def blackout_boundaries(boundaries: list[tuple[float, int]], drop_out: float,
                        beat_dur: float, config: dict,
                        fps: float) -> tuple[list[tuple[float, int]], set[int]]
```

Elle remplace les frontières situées avant le drop par une alternance régulière,
et retourne aussi l'ensemble des **frames de début** des segments noirs.

Le comptage part **du drop et remonte**, pas du début de la fenêtre. C'est ce qui
garantit la propriété qui compte : le segment qui se termine sur le drop est un
**éclair d'image**, jamais un noir. L'impact tombe donc sur une image. Compter
depuis le début laisserait la parité au hasard de la durée du build-up.

```
   début fenêtre                                        drop
        │                                                 │
        ├── tête ──┼─ noir ─┼─ img ─┼─ noir ─┼─ img ──────┤
                      k=4      k=3     k=2      k=1   k=0 (impact)
```

Un segment d'indice `k` (compté à rebours depuis le drop, `k = 0` pour celui qui
s'y termine) est **noir si `k` est impair**.

**Le segment de tête** — du début de la fenêtre à la première frontière de strobe
— est plus court que les autres et **toujours une image**, quelle que soit sa
parité. Une vidéo qui s'ouvre sur du noir ressemble à un bug. Quand sa parité
aurait voulu du noir, on obtient donc deux éclairs d'affilée au tout début : une
irrégularité d'un demi-temps, invisible à l'écran, et préférable à une ouverture
sur écran noir.

**Les indices de beat.** Les frontières intermédiaires du strobe ne tombent pas
sur des beats : elles portent `-1`, la valeur qui désigne déjà « pas un beat »
dans `boundaries`. En revanche **la frontière du drop conserve son indice de beat
d'origine** — c'est lui qui fait du drop un impact pour `ramp_speed`, et le
perdre casserait le ralenti d'anticipation et l'accéléré de relance qui
l'encadrent.

La frontière de début de fenêtre `(0.0, -1)` est conservée telle quelle.

Les positions sont quantifiées sur la grille de frames, comme toutes les
frontières de `build_edl`, et l'écart minimal d'une frame entre deux frontières
est préservé.

### 3. Le noir comme entrée d'EDL

Une entrée noire porte `kind: "black"`, aux côtés de `video` et `image`. Elle n'a
**ni `clip_path`, ni `clip_in`, ni cadrage** :

```python
{"timeline_start": …, "duration": …, "kind": "black",
 "beat_index": …, "section": "buildup", "speed": 1.0, "effects": []}
```

Conséquences, toutes voulues :

- **Aucun clip n'est consommé.** Les entrées noires n'entrent pas dans la mémoire
  anti-répétition : elles ne montrent rien, elles ne peuvent pas se répéter.
- **Aucun tirage n'est consommé** dans le générateur seedé. La branche noire sort
  avant la sélection de clip, comme le fait déjà celle des images.
- Au rendu, FFmpeg **génère** la matière : `-f lavfi -i color=c=black:s=WxH`.
  Aucun fichier ouvert, encodage quasi gratuit.

### 4. La punchline reste affichée sur le noir

Le `drawtext` s'applique par segment, dans la chaîne commune. Une entrée noire la
traverse donc et **conserve son texte**.

C'est délibéré : si la punchline disparaissait un demi-temps sur deux, elle
clignoterait à 2 Hz et deviendrait illisible. Sur fond noir elle est au contraire
parfaitement lisible — le noir devient un support de texte plutôt qu'un trou.

### 5. Le rendu d'un segment noir

`_segment_input_args` gagne une branche : pour `kind == "black"`, elle retourne
une source `lavfi` au lieu d'un fichier.

`_segment_filters` sort tôt pour ces entrées, avec une chaîne minimale : `fps`,
la punchline si elle existe, `setsar=1,format=yuv420p`, `tpad`. Pas de `crop`
de bandes noires, pas de `delogo`, pas de layout — la source est déjà aux bonnes
dimensions, et tout le reste n'aurait rien à traiter.

Le forçage du nombre de frames exact (`-frames:v`) reste en place : un segment
noir compte dans le montage comme n'importe quel autre, et la dérive
audio/vidéo ne peut pas naître de lui.

## Ce qui s'aligne sans code

Les segments de strobe durent `blackout_beats` beats, soit 0,23 s à 128 BPM —
**sous le `min_dur` de 0,25 s** des ramps de vitesse. Ils sont donc
automatiquement exemptés de ralenti, ce qui est le comportement voulu : un
ralenti sur un éclair de 0,23 s n'aurait aucun sens.

Avec un `blackout_beats` plus grand (à partir de `0.6` à 128 BPM), les éclairs
repasseraient au-dessus du seuil et pourraient recevoir un ralenti. C'est
cohérent — à cette durée ce ne sont plus des éclairs.

La scène de fin vit après le drop : aucune interaction.

## Conséquences assumées

**Le nombre de segments double.** Le rendu encode un fichier par entrée d'EDL.
Un build-up de 10 s à 128 BPM passe d'environ 6 segments à ~42 (21 éclairs
+ 21 noirs), portant l'EDL d'environ 45 à ~81 entrées. La génération s'allonge,
même si les noirs sont très rapides à encoder.

**La consommation de catalogue augmente nettement.** Chaque éclair bloque sa
durée plus la marge d'une seconde de l'anti-répétition, soit ~1,23 s de matière
pour 0,23 s montrées. Vingt-et-un éclairs mangent ~26 s de catalogue rien que
pour le build-up. Sur une niche à trois clips courts, cela rapproche le seuil
d'épuisement mesuré précédemment (~150 s de matière exploitable).

**Le clignotement.** Dix secondes d'alternance à environ 2 Hz, c'est long. C'est
un choix éditorial du propriétaire du projet, pris en connaissance de cause :
TikTok pénalise les contenus photosensibles et l'effet peut fatiguer avant
d'impressionner. L'option est désactivée par défaut et se juge sur pièce.

## Hors périmètre

- Cadence accélérée à l'approche du drop.
- Borne « N derniers beats » pour raccourcir le strobe. Si le build-up entier se
  révèle trop long à l'usage, l'ajouter sera une petite tâche — mais on ne
  l'invente pas avant d'avoir regardé.
- Couleur autre que le noir (blanc, couleur d'accent).
- Strobe après le drop : c'est déjà `strobe_beats`, avec une autre grammaire.

## Tests

Tous purs, sans FFmpeg :

- `blackout_boundaries` : alternance régulière au pas demandé ; le segment se
  terminant sur le drop est une image ; le segment de tête est une image quelle
  que soit sa parité ; la frontière du drop **conserve son indice de beat** et
  les frontières intermédiaires portent `-1` ; les frontières restent triées,
  quantifiées et espacées d'au moins une frame ; un `blackout_beats` absurde
  (plus long que le build-up) ne produit pas de grille vide.
- Le drop reste un impact : avec l'effet actif, le segment qui se termine sur le
  drop reçoit toujours son ralenti d'anticipation quand `effects.speed` est actif
  et que sa durée dépasse `min_dur`.
- `build_edl` : avec l'effet actif, le build-up alterne `video` et `black` ;
  aucune entrée noire ne porte de `clip_path` ; les entrées noires n'apparaissent
  pas dans la mémoire de consommation ; la section `drop` est inchangée.
- Non-régression : l'effet désactivé, ou sans drop dans la fenêtre, produit une
  EDL **identique** à celle d'avant ce spec, à seed et catalogue égaux.
- Reproductibilité : deux appels à seed égale donnent la même EDL.
- `_segment_input_args` : une entrée noire retourne une source `lavfi` et
  n'ouvre aucun fichier.
- `_segment_filters` : une entrée noire produit une chaîne sans `crop`, sans
  `delogo` et sans layout, mais **avec** la punchline quand elle est présente.
