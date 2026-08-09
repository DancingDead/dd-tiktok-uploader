# Spec — Recadrage sur le locuteur actif, avec coupes franches

**Date** : 2026-08-08
**Statut** : validé, prêt pour plan d'implémentation
**Prérequis** : le clipper (spec du 2026-07-08, branche `feat/clipper`)

## Problème / objectif

Le clipper recadre en 9:16 en suivant le **plus grand** visage détecté, par un
panoramique lissé. Sur un vrai épisode (podcast multi-caméra déjà monté), ça
donne trois défauts visibles :

- le cadre suit le plus grand visage, pas celui qui parle ;
- il **glisse par-dessus les coupes du montage d'origine** au lieu de couper,
  ce qui se lit comme une dérive molle et non comme un montage ;
- il n'écarte rien : sur la source d'essai, trois « visages » figés au bord
  gauche (~70 px, coordonnées identiques de t=140 s à t=380 s) sont de
  l'habillage, pas des interlocuteurs. Le code s'en sortait par chance, en
  prenant le plus grand.

Objectif : **le cadre tient celui qui parle, et change de personne par une
coupe franche**, comme le ferait un monteur.

## Mesures qui fondent la conception

Relevées sur la source d'essai (AV1 1920×1080, 30 fps, macOS, OpenCV) :

| opération | coût |
|---|---|
| `seek` + lecture d'une image (**méthode actuelle**) | **45 ms** |
| lecture **séquentielle** d'une image | **1,4 ms** |
| détection Haar en 1920×1080 | 25 ms |
| détection Haar en 960×540 | 8 ms |

Le `seek` coûte 32 fois la lecture séquentielle. `track_faces` fait aujourd'hui
un `seek` par échantillon, ce qui lui interdit d'échantillonner dense — or la
parole agite la bouche à 5-10 Hz : **à 2 images/s, l'information n'existe pas**.
En décodant le clip d'une traite, 10 images/s coûtent moins cher que les
2 images/s actuelles. C'est ce renversement qui rend la fonctionnalité
abordable, et il conditionne tout le reste.

## Décisions de cadrage (issues du brainstorming)

- **Agitation de la bouche** pour décider qui parle : moyenne de la différence
  absolue entre deux images sur le tiers inférieur de chaque visage. Aucune
  nouvelle dépendance, quelques millisecondes par image, testable en logique
  pure. Écarté : le flux optique (≈10× plus cher, et beatsync a déjà mesuré
  qu'il ne parallélise pas) et un modèle de détection de locuteur actif
  (dépendance lourde de plus après Whisper). Limite assumée : quelqu'un qui rit
  ou mange en silence peut être pris pour le locuteur.
- **Le cadre est fixe entre deux frontières.** Plus de panoramique par-dessus
  une coupe.
- **Trois types de contenu à couvrir** : déjà monté multi-caméra, plan fixe
  large à plusieurs, et face cam à une personne. D'où les deux mécanismes
  (détection de coupes de la source *et* détection du locuteur), l'un ne
  suffisant pas aux trois cas.
- **Repli explicite** : `speaker_cuts` désactivable, pour revenir au suivi
  simple sans attendre un correctif si la détection se comporte mal.

## Design

### 1. Un nouveau module `speaker.py`

`clipper.py` fait ~900 lignes et couvre déjà six sujets (transcript,
classement, géométrie de cadrage, sous-titres, pont LLM, I/O ffmpeg). Tout ce
qui touche aux visages, aux pistes et au découpage en plans va dans un module
séparé, `speaker.py` : une responsabilité, un fichier, et une frontière nette —
`clipper.render_clip` lui demande des segments de cadrage et ne sait rien de la
façon dont ils sont obtenus.

Y déménagent aussi les fonctions de géométrie de cadrage aujourd'hui dans
`clipper.py` (`crop_size`, `crop_expr`, `_even`, `_ramp`, `smooth_track`,
`_fill_holes`), qui relèvent du même sujet. `clipper.py` les réexporte le temps
de la transition si des tests existants les importent.

### 2. Chaîne de traitement

| fonction | rôle | nature |
|---|---|---|
| `read_frames` | décode le clip d'une traite, rend des images réduites en niveaux de gris | I/O |
| `detect_faces` | rectangles de visages sur une image | I/O (cv2) |
| `link_tracks` | relie les détections d'image en image en **pistes** persistantes | **pure** |
| `mouth_activity` | agitation du tiers inférieur d'un rectangle entre deux images | I/O léger |
| `frame_difference` | écart global entre deux images successives | I/O léger |
| `usable_tracks` | écarte l'habillage et les faux positifs | **pure** |
| `speaker_timeline` | décide qui tient le cadre, à chaque instant | **pure** |
| `crop_segments` | transforme la timeline en segments de cadrage | **pure** |
| `analyze_framing` | orchestre les précédentes pour un clip | I/O |

`link_tracks` est le maillon indispensable et non évident : « qui parle »
suppose une **identité** d'une image à l'autre, alors que le code actuel ne
produit que des détections indépendantes. Une piste relie les rectangles de
deux images consécutives par recouvrement (IoU au-dessus d'un seuil, le plus
recouvrant l'emporte) ; une détection sans correspondance ouvre une piste, une
piste sans correspondance est mise en sommeil et peut reprendre plus tard.

**Cadence** : détection Haar toutes les 0,5 s sur image réduite de moitié
(8 ms), agitation de bouche à 10 images/s sur les seuls rectangles connus
(quelques ms). Entre deux détections, les rectangles sont tenus à leur dernière
position — la bouche bouge, la tête pas assez en 0,5 s pour que ça compte.

### 3. Constantes

Réglages internes, non exposés dans l'interface — ils décrivent le
fonctionnement du détecteur, pas une préférence de montage :

| constante | valeur | pourquoi cette valeur |
|---|---|---|
| `FRAME_FPS` | `10.0` | la parole agite la bouche à 5-10 Hz ; en dessous, l'information disparaît |
| `DETECT_EVERY` | `0.5` s | une tête ne se déplace pas assez en un demi-quart de seconde pour justifier de repayer une détection Haar |
| `DETECT_SCALE` | `0.5` | Haar en 960×540 coûte 8 ms contre 25 ms en pleine résolution, pour la même détection à cette taille de visage |
| `IOU_MIN` | `0.3` | en dessous, deux visages voisins finiraient reliés dans la même piste |
| `MIN_FACE_FRACTION` | `0.06` | un visage sous 6 % de la hauteur est une vignette, pas un interlocuteur cadré |
| `STATIC_TOLERANCE` | `2` px | une personne vivante bouge toujours de plus de deux pixels ; en deçà, c'est un élément d'habillage |
| `ACTIVITY_WINDOW` | `0.6` s | assez long pour lisser une syllabe, assez court pour réagir à une prise de parole |
| `SWITCH_MARGIN` | `1.5` | le prétendant doit être une fois et demie plus agité ; en deçà, deux personnes qui se coupent la parole feraient osciller le cadre |
| `CUT_THRESHOLD` | `0.35` | écart global entre deux images (fraction de la dynamique) au-dessus duquel le montage d'origine a changé de plan |

### 4. Écarter ce qui n'est pas un interlocuteur

`usable_tracks` applique trois règles, toutes vérifiables sur une piste, donc
en logique pure :

- **trop petite** : hauteur sous `MIN_FACE_FRACTION` (6 %) de la hauteur de
  l'image — une vignette, pas un interlocuteur cadré ;
- **jamais agitée** : aucune agitation de bouche sur tout le clip — élimine les
  affiches, les visages de dos et les faux positifs ;
- **parfaitement immobile** : rectangle qui ne varie pas de plus de
  `STATIC_TOLERANCE` pixels sur des dizaines de secondes — de l'habillage.

C'est ce qui manquait entièrement au code actuel.

### 5. Politique de coupe

Deux sortes de frontières, traitées différemment :

- **coupe de la source** (`frame_difference` au-dessus de `CUT_THRESHOLD`) : le
  montage d'origine a changé de plan, on a le droit de sauter au même instant —
  toute l'image change déjà, le saut est invisible ;
- **changement de locuteur** dans un plan continu : c'est nous qui coupons, en
  franc.

Entre deux frontières, **le cadre est fixe**. Trois garde-fous :

- **`min_shot`** (1,2 s par défaut) : dans une conversation vive la parole
  alterne en moins d'une seconde ; sans plancher, le cadre ferait des
  allers-retours qui se lisent comme un bug et non comme un montage ;
- **`SWITCH_MARGIN`** : on ne bascule que si le prétendant domine d'au moins
  ce facteur sur une fenêtre glissante (`ACTIVITY_WINDOW`, 0,6 s), sinon deux
  personnes qui se coupent la parole feraient osciller le cadre ;
- **en cas de doute, on tient** : silence, aucun visage, ou deux pistes à
  égalité → cadrage courant conservé. Revenir au centre par défaut se lit comme
  une panne.

Si aucune piste exploitable n'existe sur tout le clip, repli sur le
comportement actuel : cadrage centré fixe.

### 6. Conséquence sur `crop_expr`

L'interpolation ajoutée à `crop_expr` le 2026-08-07 devient **partiellement le
mauvais comportement** : elle a été écrite pour supprimer les téléportations,
or on veut désormais des sauts, aux bons endroits.

Résolution : `crop_expr` ne reçoit plus une trajectoire plate mais des
**segments** `{start, end, x_start, x_end}`. Elle **interpole à l'intérieur**
d'un segment (utile si l'orateur bouge pendant un plan long) et **saute sec**
entre deux segments. Le travail n'est pas perdu, il est recadré.

Même chose pour la zone morte de `smooth_track` : elle reste utile *dans* un
segment, mais ce n'est plus elle qui porte la stabilité — c'est le découpage en
plans.

Les invariants existants sont conservés : valeurs paires (`_even`), bornées dans
l'image, plafond `MAX_STEPS`, et une trajectoire immobile rend un simple nombre
(donc pas d'`eval=frame` inutile, cf. la sonde `crop_supports_eval` pour
ffmpeg 8).

### 7. Réglages

Deux réglages, à ajouter partout où vivent les cinq existants
(`clipper.DEFAULTS`, `beatsync.DEFAULT_CONFIG["clipper"]`, `CLIPPER_RANGES` et
`coerce_clipper` côté `webui.py`, la carte « Clipper » des Réglages côté
React) :

| clé | défaut | bornes | rôle |
|---|---|---|---|
| `speaker_cuts` | `True` | booléen | recadrage sur le locuteur ; désactivé, on retombe sur le suivi simple |
| `min_shot` | `1.2` | `[0.4, 5.0]` | durée minimale d'un plan, en secondes |

`speaker_cuts` étant un booléen, `coerce_clipper` — aujourd'hui purement
numérique — doit apprendre à en valider un, en refusant ce qui n'est ni un
booléen ni `"true"`/`"false"`.

### 8. Tests

Sur les fonctions pures, en `tests/test_speaker.py` :

| couvre | cas |
|---|---|
| `link_tracks` | deux visages reliés correctement d'une image à l'autre ; une piste qui disparaît puis revient ; deux pistes qui se croisent sans échanger d'identité ; aucune détection |
| `usable_tracks` | habillage immobile rejeté ; visage trop petit rejeté ; piste jamais agitée rejetée ; piste normale conservée |
| `speaker_timeline` | bascule quand la domination dépasse la marge ; `min_shot` respecté ; maintien en cas d'égalité ; segment redémarré sur une coupe de la source ; repli centré quand aucune piste |
| `crop_segments` / `crop_expr` | saut net à la frontière ; interpolation à l'intérieur d'un segment ; bornes et parité conservées ; plafond `MAX_STEPS` ; trajectoire immobile → constante |

Les fonctions d'I/O (`read_frames`, `detect_faces`, `mouth_activity`,
`frame_difference`, `analyze_framing`) ne sont pas testées automatiquement, sur
le même principe que les I/O existantes du clipper.

### 9. Vérification à l'œil, obligatoire

Rien de tout ceci ne se juge sur des tests. Un clip est rendu depuis la source
d'essai `data/clipper/football-magouilles-compagnie-ep12/` et **regardé**, avec
un compte rendu franc sur trois points :

1. le cadre tient-il le bon visage ?
2. les coupes tombent-elles aux bons moments, sans clignoter ?
3. l'habillage du bord gauche est-il bien ignoré ?

C'est ce qui a manqué au premier lot du clipper — trois pannes livrées faute
d'avoir exécuté la chaîne sur du vrai contenu. On ne refait pas l'erreur.

## Hors périmètre

- Diarisation audio (qui parle, d'après le son) et modèles de détection de
  locuteur actif : écartés au profit de l'agitation de bouche.
- Suivi de visages de profil : la cascade utilisée est frontale ; quelqu'un
  filmé de profil ne sera pas détecté, sa piste sera tenue à sa dernière
  position connue.
- Split-screen à deux visages (déjà hors périmètre du lot 1 du clipper).
- Recadrage vertical (le crop reste ancré en haut, `y=0`).
