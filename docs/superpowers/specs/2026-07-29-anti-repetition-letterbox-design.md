# Spec — Anti-répétition des extraits et rognage des bandes noires

**Date** : 2026-07-29
**Statut** : validé, prêt pour plan d'implémentation

Deux corrections issues des premiers rendus réels de Théo, après validation de
la version (« la version a l'air stable »). Elles sont indépendantes l'une de
l'autre mais touchent les mêmes fonctions (`build_edl`, le scan, le cadrage),
d'où un spec unique en deux volets.

---

## Volet A — Ne jamais rejouer un passage déjà montré

### Problème

Des coupes tombent entre deux extraits quasi identiques du même plan, ce qui
donne un effet de retour en arrière et casse la fluidité.

Deux causes distinctes, toutes deux réelles :

1. **En mode `chrono`**, la seule garantie de progression est
   `clip_in = max(clip_in, last_clip_in[clip] + 0.1)` : **0,1 seconde**. Un clip
   réutilisé quelques plans plus loin rejoue donc presque exactement les mêmes
   images.
2. **Toujours en mode `chrono`**, la ligne suivante rabote `clip_in` dans les
   bornes de la plage — `min(max(clip_in, interval["start"]), interval["end"] -
   source_needed)` — **après** avoir appliqué la garantie. Quand la cible dépasse
   la fin de la plage, le point d'entrée redescend donc **sous** la position
   précédente. Ce n'est pas une impression de retour en arrière : c'en est un.
3. **Hors mode `chrono`**, `clip_in = rng.uniform(interval["start"], …)` tire sans
   aucune mémoire : deux extraits d'un même clip peuvent se recouvrir librement.

Le filtre `prev_path` n'empêche que la répétition **immédiatement consécutive**
du même fichier ; il ne dit rien d'un clip revu trois plans plus loin.

### Décision de cadrage (issue du brainstorming)

Règle retenue : **ne jamais rejouer un passage déjà montré**. Écartés : l'écart
minimum en secondes (deux passages distants de 3,1 s d'un plan fixe restent
quasi identiques) et la seule interdiction du retour en arrière (supprime le
symptôme le plus visible mais laisse la répétition vers l'avant).

### Design

#### A.1 Fenêtres libres

Nouvelle fonction **pure** :

```python
def free_windows(intervals: list[dict], consumed: list[tuple[float, float]],
                 source_needed: float, margin: float = 0.5) -> list[dict]
```

Elle soustrait des plages exploitables les portions déjà consommées, **élargies
de `margin` de chaque côté** — sans cette marge, un nouvel extrait pourrait coller
au précédent et rester visuellement identique. Elle ne retourne que les fenêtres
au moins aussi longues que `source_needed`.

Les dicts retournés ont la **même forme** que les plages d'entrée (`start`, `end`,
`motion`, `presence`) : `motion` et `presence` sont hérités de la plage parente,
puisqu'ils sont déjà des moyennes et qu'on ne dispose pas des données par
échantillon pour les recalculer. Les consommateurs en aval ne voient donc aucune
différence de structure.

#### A.2 Mémoire de consommation dans `build_edl`

`build_edl` tient `consumed: dict[Path, list[tuple[float, float]]]`. Après chaque
segment **vidéo** produit, la portion réellement consommée
`(clip_in, clip_in + source_needed)` y est ajoutée. Les images n'y entrent pas :
elles n'ont pas de position dans une source.

`free_windows` alimente ensuite les **trois** points de décision qui travaillaient
jusqu'ici en aveugle :

- le filtre des clips utilisables (`usable`) — un clip sans fenêtre libre assez
  longue sort du tirage ;
- le mapping du mode `chrono`, qui répartit la progression sur les fenêtres
  libres au lieu des plages brutes ;
- le tirage libre du mode non-chrono.

Le bricolage `+ 0.1` **disparaît**, et avec lui le rabotage qui pouvait faire
reculer le point d'entrée : la garantie n'est plus une correction appliquée après
coup, elle est dans le choix de la fenêtre.

Le mode `chrono` conserve en plus son ordre narratif : parmi les fenêtres libres,
seules celles qui se terminent après **la fin de la dernière portion consommée**
pour ce clip (soit `max(fin)` sur ses entrées dans `consumed`, `0.0` s'il n'a pas
encore servi) sont candidates. Sans ce filtre, un extrait pourrait légitimement
— car libre — tomber avant le précédent : pas un rejeu, mais une entorse à la
chronologie.

#### A.3 La scène de fin réserve sa portion

La scène de fin est choisie **avant** la boucle, au moment où les derniers beats
sont réservés. Son point d'entrée y est donc déjà calculable (`es_source` l'est
déjà, pour être passé à `find_final_scene`). On y calcule aussi son `clip_in` et
on **amorce `consumed`** avec sa portion, pour qu'aucun segment ordinaire ne
montre par avance le plan du climax. La boucle réutilise ensuite ces valeurs au
lieu de les recalculer.

#### A.4 Dégradation

Si plus **aucun** clip n'a de fenêtre libre assez longue, on recalcule `usable` en
ignorant `consumed` — la réutilisation redevient permise. L'erreur existante
(« aucun clip n'a de plage exploitable de N s ») ne se déclenche donc que si le
catalogue était de toute façon insuffisant, exactement comme aujourd'hui. Mieux
vaut un plan revu qu'un lot qui échoue.

#### A.5 Reproductibilité

`free_windows` est pure et sans RNG ; `consumed` est un état déterministe, dérivé
uniquement des décisions déjà prises dans l'ordre de la boucle. À seed et
catalogue égaux, la séquence est identique. Les seeds antérieures ne reproduiront
en revanche plus leurs vidéos : les candidats changent, donc les tirages changent.

---

## Volet B — Rogner les bandes noires

### Problème

Certains clips arrivent letterboxés — un extrait de film récupéré sur YouTube
porte deux bandes noires en haut et en bas. Le moteur les traite comme de l'image :

- le recadrage 9:16 ou 1:1 conserve les bandes, donc la vidéo finale en garde ;
- `ratio`, lu par `load_clips` sur les dimensions du conteneur, est faux : un film
  2.35:1 letterboxé dans du 16:9 est vu comme du 16:9, ce qui fausse les règles de
  layout (`blur` en carré, notamment) ;
- `delogo`, exprimé en fractions du cadre **entier**, vise à côté sur ces clips.

### Décision de cadrage (issue du brainstorming)

Détection **au scan**, rognage au rendu, et correction du `ratio`. Écartés : le
rognage seul sans corriger `ratio` (laisserait les règles de layout fausses sur
ces clips) et une détection à l'import via `cropdetect` (ajoute un état à
maintenir en base et ne couvre pas les clips déjà présents).

### Design

#### B.1 Détection

Nouvelle fonction **pure** :

```python
def content_rect(frames: np.ndarray) -> dict | None
```

Elle reçoit les frames que `_scan_one` décode déjà (N, 360, 640, 3) et retourne
`{"x", "y", "w", "h"}` en **fractions** du cadre, ou `None` si aucune bande n'est
détectée.

Méthode : luminance moyenne par ligne et par colonne, puis **95ᵉ percentile sur
l'ensemble des frames** — et non le maximum, sinon un sous-titre incrusté dans la
bande ou un flash isolé suffirait à masquer la détection. Une ligne (ou colonne)
est une bande si sa valeur reste sous `BAR_LUMA_MAX` (16 sur 255). On ne retient
que le **segment continu de tête** et le **segment continu de queue** de chaque
axe — une ligne sombre isolée au milieu de l'image n'est pas une bande, et ne doit
pas rogner quoi que ce soit. Un axe sans bande de tête donne simplement un décalage
nul. Les bandes latérales (pillarbox) sont traitées par le même code, sans effort
supplémentaire.

Deux garde-fous :
- une bande doit couvrir au moins **1,5 %** de la dimension pour compter (sinon
  c'est du bruit de compression sur une ligne) ;
- les bandes d'un même axe ne doivent pas dépasser **30 %** de la dimension. Au-delà,
  c'est une scène nocturne ou un fondu, pas un letterbox : on retourne `None`
  plutôt que de mutiler un plan sombre.

Les fractions sont indépendantes de la résolution : le scan force `scale=640:360`
sans préserver le ratio, mais cette mise à l'échelle est une transformation
linéaire du cadre entier, donc une fraction du cadre de scan est la même fraction
du cadre source.

#### B.2 Stockage et cache

`_scan_one` pose `clip["crop"]` (les fractions, ou `None`) et, quand un rognage
est détecté, **corrige `clip["ratio"]`** pour qu'il décrive le contenu :
`(w_frac × width) / (h_frac × height)`.

`_scan_payload` / `_apply_scan_payload` transportent la clé. Un **numéro de
version** est ajouté au cache (`SCAN_CACHE_VERSION`) et vérifié au chargement au
même titre que le `mtime` : les entrées écrites avant ce spec n'ont pas de `crop`
et doivent être recalculées, pas lues à moitié.

#### B.3 Rendu

L'entrée d'EDL gagne `crop`, en **pixels** cette fois (fractions × dimensions
réelles, calculé dans `build_edl` — la quantification et les conversions restent
du côté pur). `clip_w` / `clip_h` deviennent les dimensions **du contenu**.

Dans `_segment_filters`, le fragment `pre` commence par `crop=w:h:x:y`, **avant**
le `delogo`. L'ordre compte et corrige un défaut au passage : `delogo` est calculé
en fractions de `clip_w`/`clip_h`, donc une fois celles-ci devenues les dimensions
du contenu, il se recale tout seul sur le vrai coin de l'image.

#### B.4 Recalage du centre d'intérêt

`interest_x` est mesuré sur le cadre entier, bandes comprises. Pour un letterbox
horizontal (bandes haut/bas) cela ne change rien. Pour un **pillarbox**, `focus_x`
pointerait à côté après rognage. `frame_extract` remappe donc la valeur du cadre
entier vers le contenu :

```
focus_x = clip((focus_x - x_frac) / w_frac, 0.0, 1.0)
```

Sans ce remappage, le volet B corrigerait un défaut en en introduisant un autre.

### Limite connue, assumée

`_char_presence` détecte visages et contours sur le cadre entier, et son test de
« bande centrale 40 % » raisonne en x. Un pillarbox décale donc légèrement cette
bande. L'effet est second ordre — les bandes latérales sont rares dans le
catalogue — et le corriger demanderait de rejouer la détection sur le cadre
rogné, donc un scan plus coûteux. Hors périmètre.

---

## Interactions vérifiées

- **Volet A + scène de fin** : traité en A.3 — la scène réserve sa portion avant
  la boucle.
- **Volet A + images** : les images n'ont pas de position source et n'entrent pas
  dans `consumed`. Leur propre garde-fou (écart minimum de 3 segments) est
  inchangé.
- **Volet A + `min_presence`** : le filtre de présence s'applique après
  `free_windows`, sur les mêmes dicts — l'ordre des filtres ne change pas.
- **Volet B + images** : les images ne sont pas scannées, donc jamais de `crop`.
  Leur `ratio` reste celui du fichier, ce qui est correct.
- **Volet B + `split`** : le layout duel découpe la source en deux moitiés
  gauche/droite. Comme le rognage précède tout dans `pre`, les moitiés sont celles
  du contenu, pas du cadre à bandes.
- **Volet B + Ken Burns** : le `zoompan` des images opère après un `pre` vide (pas
  de crop sur une image) — inchangé.

## Hors périmètre

- Détection de bandes non noires (blanches, floutées, ou un fond uni très sombre
  mais non nul).
- Rognage manuel par clip dans l'interface : si la détection se trompe, on ajuste
  le seuil plutôt que d'ajouter un réglage par asset.
- Recalcul de `_char_presence` sur le cadre rogné (voir la limite ci-dessus).

## Tests

Tous purs, sans FFmpeg ni réseau :

**Volet A** — `free_windows` : soustraction d'une portion centrale (deux fenêtres
en sortie), d'une portion en tête ou en queue (une fenêtre), marge appliquée des
deux côtés, fenêtre résiduelle plus courte que `source_needed` écartée, plage
entièrement consommée disparaissant, liste `consumed` vide rendant les plages
intactes.
Intégration `build_edl` : deux segments d'un même clip ne se chevauchent jamais ;
en mode `chrono` la position d'entrée d'un clip ne décroît jamais ; un catalogue
trop pauvre dégrade en réutilisation au lieu de lever ; la portion de la scène de
fin n'est jamais montrée par un segment antérieur ; reproductibilité à seed égale.

**Volet B** — `content_rect` : letterbox pur, pillarbox pur, les deux à la fois,
absence de bande rendant `None`, scène uniformément sombre refusée par le seuil
des 30 %, bande d'un seul pixel ignorée par le seuil des 1,5 %, sous-titre clair
incrusté dans la bande ne masquant pas la détection (c'est le rôle du percentile).
`frame_extract` : `focus_x` remappé sur un clip à bandes latérales, inchangé sans
`crop`. `_segment_filters` : `crop=` présent en tête de `pre` et avant `delogo`.
Cache : une entrée sans numéro de version est traitée comme un miss.
