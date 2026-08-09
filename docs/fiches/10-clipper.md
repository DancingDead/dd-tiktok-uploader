# 10 — clipper.py / clip_source.py / speaker.py

> `clipper.py` (807 lignes, logique pure + I/O) · `clip_source.py` (223
> lignes, orchestrateur) · `speaker.py` (744 lignes, cadrage : qui tient
> l'image et où couper) · endpoints `/api/clipper/*` dans `webui.py` l.689-929
> ← [09 webui.py](09-webui.md)

## 1. Ce que fait ce sous-système, et en quoi il diffère de beatsync

Le clipper part d'une **vidéo longue parlée** (interview, live, stream) et en
tire une bibliothèque de **shorts 9:16** recadrés sur le visage, sous-titrés en
karaoké, classés par pertinence. C'est un **second front** de l'usine : il ne
touche pas à `beatsync.py`, ne partage aucune donnée avec le montage
(`clipper_sources`/`clipper_clips` sont des tables séparées de `videos`), et
n'a pas les mêmes règles.

| | beatsync | clipper |
|---|---|---|
| Entrée | morceau + banque de clips vidéo | une seule vidéo longue |
| Rythme | calé sur les beats du morceau | calé sur les phrases parlées |
| Sortie | 1 montage par lancement (N variantes via `generate_niche.py`) | jusqu'à `clip_count` shorts par source, en un seul lancement |
| Musique | au centre | absente |
| Cadrage | crop statique par segment (`frame_extract`) | crop **découpé en plans** sur le locuteur actif (`speaker.py`, § 2 bis) |
| LLM | punchlines courtes, un appel | proposition + notation, deux rôles différents |

Le lot 1 (celui documenté ici) ne traite **que le contenu parlé**. Une source
sans piste audio ou sans parole détectée passe en `failed` avec un message
explicite — pas de mode « sets / rave » sans voix (voir § 8).

---

## 2. Le pipeline en sept étapes

```
1. Import de la source          webui.py l.689-813 (upload / lien YouTube → inbox → promotion)
2. Transcription                clipper.transcribe            l.596 (I/O, mis en cache)
3. Proposition de moments       clipper.propose_moments       l.499 (I/O, LLM)
4. Recalage sur les phrases     clipper.snap_to_speech        l.71  (pur)
5. Notation                     clipper.score_moment          l.561 (I/O, LLM)
6. Classement                   clipper.rank_moments          l.141 (pur)
7. Rendu                        clipper.render_clip           l.735 (I/O, ffmpeg)
```

`clip_source.process(conn, root, source_id, config, log)` (l.52) enchaîne
2 → 7 pour une source, en écrivant le statut en base à chaque étape
(`transcribing`/`analyzing`/`rendering`/`done`/`failed`) pour que l'UI suive
l'avancement par polling (`GET /api/jobs/<job_id>`, mécanisme générique déjà
utilisé par `generate_niche.py`).

### 1. Import — `webui.py` l.689-813

Deux voies : upload direct (`POST /api/clipper/sources`, l.699) ou import
YouTube en **deux temps** (`POST /api/clipper/sources/link`, l.725, télécharge
dans `data/clipper/_inbox/` sous le nom choisi par yt-dlp — inconnu avant la
fin du téléchargement — puis `POST /api/clipper/inbox/<name>`, l.777,
**déplace** le fichier vers la source et crée la ligne `clipper_sources`).

Le téléchargement passe `--video --with-audio` à `fetch_tracks.py` : le
sélecteur par défaut du catalogue beatsync (`bv*`) rend le meilleur flux
**vidéo seule** — sur YouTube, un DASH 1080p muet — et la source arriverait
sans son, donc condamnée avant la transcription. La promotion refuse
d'ailleurs en 400 un fichier d'inbox sans piste audio (`clipper.has_audio`) :
mieux vaut refuser à l'import que créer une source qui échouera à l'analyse
avec un message accusant le contenu.

Deux gardes sur les slugs et la suppression, qui se répondent : `slug_for`
reçoit l'**union** des slugs en base et des dossiers présents sur disque, et
`DELETE /api/clipper/sources/<id>` (l.838) efface le dossier **avant** la
ligne, refuse en 409 tant qu'un job tourne (`transcribing`/`analyzing`/
`rendering`) et rend 409 plutôt que 500 si `rmtree` échoue (handle ouvert sous
Windows). Sans cet ensemble, un effacement raté laissait un dossier orphelin
dont un réupload du même nom récupérait le slug — et son `transcript.json` :
« Transcript en cache », puis des clips découpés aux timestamps d'une autre
vidéo, en silence.

La garde de validation d'URL (l.740-754), à connaître au bit près puisqu'elle
protège contre une injection d'options yt-dlp :

```python
url = (request.json or {}).get("url", "")
if not isinstance(url, str):          # sinon .strip() rendrait un 500
    return jsonify({"error": "lien YouTube attendu"}), 400
url = url.strip()
if any(c.isspace() for c in url):
    return jsonify({"error": "lien YouTube attendu"}), 400
parsed = urlparse(url)
allowed_hosts = {"www.youtube.com", "youtube.com", "youtu.be", "m.youtube.com"}
# scheme et netloc sont insensibles à la casse (RFC 3986)
if parsed.scheme.lower() != "https" or parsed.netloc.lower() not in allowed_hosts:
    return jsonify({"error": "lien YouTube attendu"}), 400
```

L'URL finit dans un fichier que `fetch_tracks.parse_links` découpe **une
ligne = un argument** passé à yt-dlp : un espace ou un retour à la ligne y
injecterait une option (`--exec`, `-o`, …). D'où le rejet de tout espacement
plutôt qu'un simple `strip()`, et la liste blanche de `netloc` plutôt qu'un
`startswith` — « plus facile à croire fiable qu'il ne l'est », dit la
docstring de `download_clipper_source` (l.727-737).

### 2. Transcription — `clipper.transcribe` l.596

`has_audio` (l.641) est sondée **avant** de charger le modèle Whisper — sans
ça, une source muette fait planter le décodeur audio de faster-whisper avec
une `IndexError` opaque plutôt que de dégrader proprement. faster-whisper
(`WhisperModel(model, device="cpu", compute_type="int8")`) produit des mots
horodatés, langue auto-détectée.

### 3. Proposition — `clipper.propose_moments` l.499

Le transcript est d'abord découpé en **fenêtres** qui tiennent dans le contexte
du modèle (`transcript_windows` l.466, `digest_chars` caractères, coupé sur des
phrases entières). Un appel LLM (via `_call_json`, l.406, détaillé § 4
ci-dessous) **par fenêtre** demande `max(2, ceil(count × OVERSHOOT / n))`
candidats — au moins deux, sinon un passage tardif de l'épisode n'aurait qu'une
proposition à opposer à tout le reste ; le classement (étape 6) a besoin de
matière à trier et le recalage (étape 4) en rejette une partie. La seed de la
fenêtre `i` vaut `seed + i` : déterministe, mais distincte d'une fenêtre à
l'autre. Les timestamps du digest étant absolus, les candidats se concatènent
sans recalage.

Avant ce découpage, le prompt portait le transcript entier tronqué à 40 000
caractères : sur une source d'une heure (~82 000 caractères), la seconde moitié
de l'épisode n'était jamais vue du modèle, en silence — et 40 000 caractères
(~17 400 tokens) ne tiennent dans aucun contexte local raisonnable.

Une fenêtre part **toujours avec son contenu réel**. Le cas qui l'a fait
mentir : `transcript_windows` isole bien une phrase plus longue que `max_chars`
dans sa propre fenêtre, mais `transcript_digest` cassait ensuite à la première
ligne trop longue et rendait `""` — le prompt disait littéralement « Propose 2
moments. Transcription : (rien) ». Le modèle inventait alors des timestamps,
`snap_to_speech` les recalait sur de vraies phrases, et ces candidats hors-sol
concurrençaient les bons au classement pendant que le passage que la fenêtre
devait sauver restait invisible. La perte de transcript n'était pas corrigée par
le découpage, seulement déplacée d'un cran : `transcript_digest` émet désormais
sa première phrase quoi qu'il arrive.

Les bornes `min_dur`/`max_dur` sont **interpolées** dans le prompt système
(`_propose_system` l.259) et rappelées dans le message utilisateur de chaque
fenêtre. Codées en dur, elles mentaient à qui règle 20-45 s ; et le seul prompt
système ne suffit visiblement pas — le modèle local de la tour proposait des
fenêtres d'une seconde tant que la consigne n'était pas répétée près de la
question.

L'échec d'une fenêtre est journalisé et ne perd pas les autres ; la dégradation
en `[]` ne concerne que le cas où toutes échouent — exactement comme
`beatsync.generate_punchlines`. Le parsing de la réponse est **entièrement**
dans le `try` : `{"moments": 5}` ou `{"moments": null}` faisait s'échapper un
`TypeError` qui emportait le traitement de toute la source, alors qu'une seule
fenêtre sur quatorze était en cause. `propose_moments` et `score_moment` prennent un
`log=None` : elles dégradent toujours en silence côté valeur de retour, mais
**disent pourquoi** dans le journal du job, que l'interface affiche. Sans ça,
un contexte dépassé ressemblait à « aucun candidat proposé par le LLM ».

### 4. Recalage — `clipper.snap_to_speech` l.71

Étend/rétrécit chaque candidat sur des frontières de phrase entières, dans
`[min_dur, max_dur]`. `None` si impossible : un clip de moins vaut mieux qu'un
clip qui commence au milieu d'un mot. Le cœur de la fonction (l.92-104) :

```python
# Trop long : on retire des phrases par la fin (le début porte le hook, il
# ne se sacrifie jamais).
while last > first and (span(first, last)[1] - span(first, last)[0]) > max_dur:
    last -= 1
# Trop court : on absorbe la phrase suivante, tant qu'on reste sous max_dur.
while last + 1 < len(groups):
    s, e = span(first, last)
    if e - s >= min_dur:
        break
    nxt = span(first, last + 1)
    if nxt[1] - nxt[0] > max_dur:
        break
    last += 1
```

Deux boucles asymétriques : celle qui raccourcit sacrifie toujours la **fin**
(le hook, en tête, n'est jamais entamé) ; celle qui allonge n'absorbe qu'une
phrase à la fois et s'arrête dès que `max_dur` serait dépassé.

### 5. Notation — `clipper.score_moment` l.561

Un appel LLM par candidat retenu, sur le texte **recalé** (pas le brut) :
trois notes 0-100 (hook / flow / value). Dégrade en zéros si le LLM échoue —
le moment tombe en fin de classement, il ne fait pas échouer la source.

Un appel par candidat, donc **borné** : `clip_source.SCORE_BUDGET` (3) plafonne
le nombre de candidats notés à trois fois le lot demandé. Sans ce plafond, rien
ne bornait le total — le plancher de deux candidats par fenêtre l'emporte dès
qu'il y a plus de six fenêtres, soit ~28 notations sur une heure à
`digest_chars=6000` et ~246 à `digest_chars=1000` (la borne basse, justement
celle qu'on conseille au petit contexte). Les candidats gardés sont les
premiers dans l'ordre des fenêtres, donc étalés sur la source ; **le journal dit
combien tombent** — le projet interdit les troncatures silencieuses.

La boucle journalise aussi sa progression (`[i/n] notation…`), comme celle du
rendu. Sans ça, le journal restait muet des dizaines de minutes après
« 28 candidat(s) après recalage », statut `analyzing` : le symptôme « rien ne
se passe » exactement.

### 6. Classement — `clipper.rank_moments` l.141

Garde les `count` meilleurs par score, en écartant les doublons de position
(deux candidats qui décrivent le même moment).

### 7. Rendu — `clipper.render_clip` l.735

Un seul appel ffmpeg par clip : analyse du cadrage (`speaker.analyze_framing`,
`speaker.py` l.617 — § 2 bis ci-dessous) → compilation en expression de crop
(`speaker.crop_expr`, l.225) → crop 9:16 → scale 1080×1920 → sous-titres
karaoké incrustés (fichier `.ass` généré par `build_ass`, l.193, supprimé après
le rendu, police **embarquée** passée par `fontsdir=` — voir § 3). Un clip qui
échoue au rendu ne fait pas perdre les autres (`try/except` dans la boucle de
`clip_source.process`, l.174-179) ; si **tous** les rendus échouent, la
source passe en `failed`.

Le réglage `speaker_cuts` (défaut `True`, dans `clipper.DEFAULTS`) choisit le
chemin d'analyse. À `False`, `render_clip` revient au **repli** : suivi lissé
du plus grand visage (`track_faces`, l.696, OpenCV à `SAMPLE_FPS` = 2 fps) →
`speaker.smooth_track` (l.61) → `speaker.track_to_segments` (l.174), c'est-à-dire
le comportement d'avant le recadrage sur le locuteur. § 2 bis dit quand s'en
servir — ce n'est pas un mode dégradé honteux mais une porte de sortie sur du
contenu où la détection se comporte mal.

La zone morte de ce repli se compte en pixels **source** : `render_clip` passe
`DEAD_ZONE * crop_w`, pas `DEAD_ZONE * OUT_W`. Le crop est ensuite étiré vers
1080 ; exprimée en pixels de sortie, la même constante vaudrait 21 % de
l'image finale sur une source 720p contre 7 % en 4K, et deux interviews du
même invité se cadreraient différemment selon la définition du rush.

### `eval=frame` : une option qui n'existe plus, et qu'on sonde

Cette expression de crop dépend du temps, donc ffmpeg doit la réévaluer à
chaque frame. **Comment le demander dépend de la version installée**, et le
piège coûte tous les rendus d'un coup :

| | ffmpeg ≤ 7 | ffmpeg ≥ 8 |
|---|---|---|
| option `eval` du filtre `crop` | existe, vaut `init` par défaut | **supprimée** |
| sans `eval=frame` | expression évaluée une fois : le cadre reste figé à sa position de départ, en silence | `x`/`y` sont marquées runtime-tunable (`T`), réévaluées par frame nativement |
| avec `eval=frame` | correct | `Error applying option 'eval' to filter 'crop': Option not found` → **aucun clip ne sort** |

Il n'y a donc pas de camp à choisir : la tour de production Windows n'a pas
forcément la ffmpeg du Mac de développement. `crop_supports_eval` (l.659) lit
la sortie de `ffmpeg -hide_banner -h filter=crop` et cherche une ligne
d'option dont le **nom** est `eval` (pas la sous-chaîne « eval », que les
descriptions contiennent : « when to evaluate »). Le résultat est mémorisé au
niveau du module — c'est un sous-processus, on ne le paie pas une fois par
clip. Une sonde en échec (ffmpeg absent, sortie inattendue) vaut « option
absente » : c'est le comportement des versions récentes, et il ne casse rien
sur elles. `render_clip` n'ajoute `:eval=frame` que si la sonde dit oui **et**
que l'expression dépend du temps.

Ce second test est un piège à lui seul, et il a coûté une régression
silencieuse : chercher `"if(" in expr` **ne marche pas**. Un segment unique non
constant (« le visage dérive pendant tout le plan ») compile en une simple
rampe `2*floor((666+-0.214*t)/2)`, sans aucune condition, et dépend pourtant
bien de `t` — sans `eval=frame`, le cadre restait figé à sa position de départ
sur ffmpeg ≤ 7, sans le moindre message. D'où `speaker.expr_needs_eval`
(l.208), qui teste la seule propriété fiable : `crop_expr` ne rend jamais que
deux formes, un entier littéral (cadre figé) ou une expression qui référence
`t`.

---

## 2 bis. Le cadrage sur le locuteur actif — `speaker.py`

Le clipper ne suit plus « le plus grand visage » image par image. Il **découpe
le short en plans** : à chaque instant, un seul visage tient le cadre, le cadre
le suit doucement pendant son plan, et il **saute** d'un plan au suivant.

> **La dépendance est à sens unique : `clipper` importe `speaker`, jamais
> l'inverse.** Un import croisé rendrait les deux modules inchargeables
> séparément, et `speaker` est le plus bas niveau des deux. Si une fonction de
> `speaker` a besoin de quelque chose de `clipper`, c'est le signe qu'elle est
> du mauvais côté de la frontière.

### Le domaine de validité, avant tout le reste

| | plans découpés (montage YouTube, interview multicam) | plan large filmé à l'épaule |
|---|---|---|
| mesuré sur | fenêtre 150-180 s de la source d'essai | fenêtre 60-90 s de la même source |
| coupes relevées | **7, toutes vraies**, zéro fausse | 3 vraies, séparées des pics de flou de filé par le seul critère relatif |
| pistes d'habillage retenues | **0 sur 30 s** | 0, mais au prix des trois filtres de `usable_tracks` |
| longueur des plans | médiane **4,6 s** | **10 des 16 segments collés au plancher `MIN_SHOT`** |
| verdict | ça marche | ça ne marche pas |

La cause du second cas est mesurée et documentée dans la docstring de
`_mouth_activity` (l.571-603) : le signal d'agitation y est dominé par le **mouvement de caméra**,
et il est biaisé par la **taille du visage** — corrélation hauteur de rectangle
/ agitation de **−0,64** sur 16 pistes. Pendant un panoramique, les figurants
d'arrière-plan scorent 21,9 contre 13,7 pour l'interlocuteur au premier plan,
et le cadre choisit au hasard. Normaliser par un facteur **global à l'image**
(l'écart inter-images déjà calculé pour `detect_cuts`) ne corrige pas ce biais
et ne **peut pas** le corriger : `speaker_timeline` compare les pistes entre
elles sur les MÊMES images, or diviser tous les scores d'une image par le même
nombre en laisse l'ordre inchangé — essayé, mesuré, sortie identique segment par
segment. Un biais qui dépend de la région ne se corrige pas par un facteur
commun à toute l'image.

D'où `speaker_cuts` (§ 6) : le repli vers le suivi simple est **le** réglage à
connaître sur ce sous-système. Sur du plan large en mouvement, il donne un
résultat plus terne mais plus stable.

### La chaîne, dans l'ordre

`analyze_framing(video_path, start, end, src_w, src_h, min_shot, log)` (l.617)
est le seul point d'I/O ; tout le reste est pur et testé.

1. **Lecture** — le clip est décodé **une fois, séquentiellement, jamais par
   `seek`**. C'est le renversement qui rend tout le reste abordable : 1,4 ms
   l'image en lecture suivie contre **45 ms** par `seek`, mesuré sur la source
   d'essai. On peut donc échantillonner à `FRAME_FPS` = 10 images/s, ce qu'il
   faut : la parole agite la bouche à 5-10 Hz, et aux 2 images/s du scan
   beatsync l'information n'existe tout simplement pas.
2. **Cadence réelle** — `sampling_cadence` (l.536). **`FRAME_FPS` est une
   cadence VISÉE, pas obtenue.** On ne garde qu'une image sur un nombre
   **entier**, donc la cadence réelle vaut `source_fps / keep_every` : 12,5 sur
   du PAL à 25, 12 sur du cinéma à 24, 11,988 sur du NTSC. Confondre les deux
   étire toute la timeline du même facteur — 25 % sur du PAL — puisque la suite
   convertit des indices d'image en secondes, et le cadre suit alors l'orateur
   avec un retard croissant.
3. **Détection** — cascade Haar frontale, une passe toutes les `DETECT_EVERY`
   (0,5 s) en demi-résolution (`DETECT_SCALE` : 8 ms contre 25 ms pour la même
   détection à ces tailles de visage). Entre deux passes, les rectangles sont
   **tenus** à leur dernière position et l'on n'y mesure que l'agitation de la
   bouche. Chaque image reçoit ses propres objets, marqués `detected` : sans ce
   drapeau, une piste vue une seule fois paraîtrait durer une demi-seconde.
4. **Coupes du montage d'origine** — `detect_cuts` (l.559).
5. **Pistes** — `iou` (l.289) et `link_tracks` (l.300).
6. **Filtrage** — `usable_tracks` (l.373).
7. **Timeline** — `speaker_timeline` (l.439) : qui tient le cadre à chaque
   image.
8. **Géométrie** — `crop_segments` (l.127) puis `crop_expr` (l.225).

### Une piste non appariée n'est pas close, mais elle vieillit

`link_tracks` apparie gloutonnement, par recouvrement décroissant (`IOU_MIN`
0,3) : le meilleur couple est figé d'abord, pour qu'un visage ne vole pas
l'appariement d'un autre quand deux personnes se rapprochent.

Une piste sans appariement sur une image **n'est pas fermée** : la cascade rate
régulièrement un visage (tête tournée, flou de mouvement), et rouvrir une piste
ferait passer la même personne pour une nouvelle — donc une **coupe
injustifiée** au milieu de sa phrase. Elle reste candidate sur sa dernière
position connue.

Mais elle porte une **borne d'ancienneté** (`TRACK_MAX_AGE`, 30 images ≈ 3 s à
la cadence prévue) : sans elle, une personne qui sort du cadre et une autre qui
arrive longtemps après **au même endroit** fusionnent sous la même identité, et
le montage tient pour un seul plan ce qui est un changement d'interlocuteur.
Trois secondes couvrent les occlusions légitimes sans ouvrir cette porte.

### Les trois filtres de `usable_tracks`, et le contre-exemple qui les a écrits

Le contre-exemple est l'**habillage figé** de la source d'essai : quatre
vignettes de scores incrustées au bord gauche de l'image, immobiles pendant des
minutes, que la cascade détecte comme quatre visages parfaitement valides. Sans
filtre, le cadre se collait dessus — c'est exactement ce que fait encore le
chemin de repli, et on le voit à l'œil (§ 7 bis).

| Filtre | Constante | Unité, et pourquoi elle compte |
|---|---|---|
| Existence | `MIN_DETECTIONS` = 2 | **passes de détection**, pas images. Entre deux passes les rectangles sont des copies tenues, pas des preuves : compter les images laissait passer tous les faux positifs — mesuré sur 60-90 s, 39 pistes dont 23 vues sur une seule passe, et **zéro rejetée**. Une personne à l'écran est revue à la passe suivante ; un faux positif Haar, presque jamais |
| Taille | `MIN_FACE_FRACTION` = 0,06 de la hauteur d'image | jugé sur la **médiane** des hauteurs, pas le maximum — sinon un seul faux positif de grande taille sauve une piste composée à 90 % de vignettes (cas réel : dix rectangles à h=60 plus un à h=300). Un visage qui s'approche reste gardé dès qu'il est grand sur la moitié de sa piste |
| Immobilité | `STATIC_FRACTION` = 0,15 de la hauteur du rectangle | **rapporté à sa propre taille** : une tolérance absolue ne peut pas servir les deux échelles (10 px sur une vignette de 70 px, c'est de l'habillage ; sur un visage de 400 px, c'est un frémissement). Mesuré : les trois faux visages de l'habillage parcourent 2,6 / 5,9 / 10,2 % de leur hauteur, le vrai interlocuteur le plus statique en parcourt 19 % — le seuil est posé au milieu. Ne s'applique qu'à partir de `_STATIC_MIN_DETECTIONS` = 4 passes : sur une demi-seconde, un invité assis peut très bien ne pas bouger de 15 % |

Un quatrième rejet, sans constante : une piste dont le dict `activity` est vide
ou entièrement nul. C'est un cas réel (visage vu seulement à `t=0`) — sans
mesure comparant avec l'image précédente, rien ne prouve qu'elle parle.

### `MIN_SHOT` et `SWITCH_MARGIN` existent contre le clignotement

`speaker_timeline` élit à chaque image la piste la plus agitée dans une fenêtre
`ACTIVITY_WINDOW` (0,6 s : assez long pour lisser une syllabe, assez court pour
réagir à une prise de parole). Sans garde-fou, dans une conversation vive où la
parole alterne en moins d'une seconde, le cadre ferait des allers-retours qui
se lisent comme un **bug**, pas comme un montage. Deux planchers, donc :

- `SWITCH_MARGIN` (1,5) — le prétendant doit être une fois et demie plus agité
  que le tenant. En deçà, deux personnes qui se coupent la parole font osciller
  le cadre.
- `MIN_SHOT` (1,2 s) — durée minimale d'un plan, **ramenée à `CUT_MIN_SHOT`
  (0,4 s) sur une coupe de la source**, où le saut de cadrage est masqué par le
  saut du montage. Ce second plancher est nécessaire et non redondant : sans
  lui, une rafale de coupes rapprochées — voire une seule plage de coupes —
  désactiverait complètement `MIN_SHOT`. C'est un **assouplissement**, jamais un
  durcissement : le code prend `min(min_shot, CUT_MIN_SHOT)`, sans quoi un
  `min_shot` réglé sous 0,4 s (possible en éditant `settings.json` à la main,
  qui n'est pas coercé) verrait une coupe de la source *retarder* la bascule.

Deux raffinements que le code porte et qui ne se devinent pas : `track_id` ne
vaut `None` que faute de **toute** piste candidate ; dès qu'une piste existe on
tient le dernier cadrage plutôt que de recentrer, y compris en silence total,
où recentrer se lit comme une panne. Et le plancher ne s'**arme** qu'au premier
choix appuyé sur un score positif : le tout premier choix peut être fait à
l'aveugle (silence total, départage arbitraire sur l'identifiant le plus
petit), et le verrouiller pendant `min_shot` tiendrait le premier vrai locuteur
à l'écart.

### `crop_expr` interpole dans un segment, saute entre deux

`crop_segments` (l.127) rend des segments portant le `x` de leur début **et**
celui de leur fin, et `crop_expr` (l.225) les compile en une expression
imbriquée : **interpolation linéaire à l'intérieur d'un segment** (l'orateur
bouge pendant un plan long) et **saut sec entre deux** — c'est la coupe
demandée, un glissement d'un visage à l'autre se lirait comme une dérive de
cadreur, pas comme un montage. `_ramp` (l.96) émet
`2*floor((x0+pente*(t-t0))/2)`, le `2*floor(…/2)` gardant `x` sur la grille
paire qu'exige le chroma yuv420p, au prix d'un escalier de 2 px invisible.

Trois détails de la compilation, chacun contre une panne vue :

- après la fin du **dernier** segment, le cadre tient sa valeur finale au lieu
  de prolonger la rampe : ffmpeg évalue l'expression pour tout `t`, y compris
  au-delà de la timeline (arrondis de durée rendue), et une rampe non bornée
  extrapolerait hors de l'image ;
- `_merge_static` (l.191) fusionne les paliers immobiles adjacents — inutiles,
  et ils consomment le plafond `MAX_STEPS` pour rien (le chemin de repli à 2
  images/s produit un segment par image, presque tous immobiles) ;
- au-delà de `MAX_STEPS` (120), la liste est **décimée** (un segment gardé sur
  `k`, qui absorbe ceux qu'il remplace : bornes recollées, `x_end` pris sur le
  dernier absorbé) et non tronquée. Le cadre suit donc l'orateur jusqu'au bout
  du clip, simplement plus grossièrement. Tronquer en prolongeant le dernier
  segment retenu — ce que le code a fait un temps — figeait le cadre sur toute
  la queue : avec 300 segments sur 150 s (atteignable à `max_dur` 150 s sur une
  source à plans courts), le cadrage était juste 59,5 s puis immobile les 90 s
  restantes, l'orateur sortant du champ sans y revenir.

### La cascade est frontale : le repli n°2 est le cas courant

`haarcascade_frontalface_default.xml` ne détecte **que** les visages de face. Un
intervenant de profil — c'est-à-dire quiconque parle à quelqu'un d'autre qu'à
la caméra — n'est pas détecté du tout. Sa piste est simplement **tenue à sa
dernière position connue**, et `crop_segments` a trois niveaux de repli pour ça :

1. des rectangles **dans** le segment → cadrage normal, ancré sur le premier et
   le dernier ;
2. aucun rectangle dans le segment mais la piste en a **ailleurs** dans le clip
   → trou de détection : on tient la position connue la plus proche en temps
   (`_nearest_box`, l.116) plutôt que de recentrer. `speaker_timeline` refuse
   déjà ce recentrage en plein clip, le cadrage géométrique ne doit pas la
   contredire ;
3. piste inconnue ou sans le moindre rectangle → centre. Là, il n'y a rien à
   tenir.

Le repli n°2 n'est pas un cas rare, c'est le cas **courant** : sur la fenêtre
d'essai 150-180 s, les 4 pistes utilisables ne couvrent que 4,7 à 18,9 s des 30
secondes du clip, et les deux tiers du short sont cadrés par une position
tenue. Le résultat est bon (§ 7 bis) parce que l'interlocuteur revient au même
endroit du cadre d'un plan à l'autre — ce n'est pas garanti sur une autre
source.

### Quelles constantes sont empiriques, et sur quoi

Toutes calées sur **une seule source**,
`data/clipper/football-magouilles-compagnie-ep12`, aux fenêtres 60-90 s et
150-180 s. **Elles ne valent que par cette mesure** : les déplacer sans refaire
la mesure, c'est les remplacer par rien.

| Constante | Valeur | Calée sur |
|---|---|---|
| `CUT_RATIO` | 5,0 | fenêtre 60-90 s (caméra à l'épaule) : écart inter-images médian 0,037, vraies coupes à 0,189 / 0,196 / 0,217, pics de flou de filé jusqu'à 0,170. 5 × 0,037 = 0,185 les sépare ; un seuil absolu bas, non — une coupe est un écart qui **sort de la distribution du clip**, pas qui dépasse une valeur fixe |
| `CUT_FLOOR` | 0,15 | fenêtre 150-180 s (longs plans fixes) : médiane 0,017, donc 5 × médiane = 0,084 relèverait 17 candidats dont 10 faux. Vraies coupes de 0,177 à 0,199, pire flou de filé 0,125 : le plancher est posé entre les deux. Il coûte une vraie coupe sur huit (une transition douce à 0,129) — bon compromis, une coupe manquée laisse simplement `MIN_SHOT` s'appliquer, alors qu'une fausse coupe autorise un plan de 0,4 s là où aucun saut du montage ne le masque |
| `MIN_DETECTIONS` | 2 | fenêtre 60-90 s : 39 pistes, 23 vues sur une seule passe, zéro rejetée quand on comptait les images |
| `MIN_FACE_FRACTION` | 0,06 | trois « visages » de 70 px sur 1080 étaient l'habillage collé au bord |
| `STATIC_FRACTION` | 0,15 | 2,6 / 5,9 / 10,2 % pour l'habillage figé (140-380 s) contre 19 % pour le vrai interlocuteur le plus statique de 60-90 s |
| `_STATIC_MIN_DETECTIONS` | 4 | en deçà d'une demi-seconde, l'immobilité ne prouve rien |

Les autres constantes du module ne sont **pas** des mesures mais des choix de
confort visuel, qu'on peut discuter à vue : `IOU_MIN`, `TRACK_MAX_AGE`,
`ACTIVITY_WINDOW`, `SWITCH_MARGIN`, `MIN_SHOT`, `CUT_MIN_SHOT`, `DEAD_ZONE`,
`MAX_STEPS`.

---

## 3. Les fonctions pures, une ligne chacune

Celles de `clipper.py` d'abord ; celles de `speaker.py` dans le second tableau.

| Fonction (`clipper.py`) | l. | Invariant |
|---|---|---|
| `sentences(words) -> list[(int,int)]` | 55 | Une phrase se termine sur ponctuation forte OU un blanc ≥ `SENTENCE_GAP` (0,6 s) — le silence est plus fiable que la ponctuation sur du français transcrit à l'oral |
| `snap_to_speech(start, end, words, min_dur, max_dur) -> (float,float)\|None` | 71 | Recale sur des frontières de phrase entières ; `None` si aucune combinaison ne rentre dans `[min_dur, max_dur]` |
| `moment_score(moment) -> float` | 126 | Moyenne pondérée `WEIGHTS` (hook 0,4 / flow 0,3 / value 0,3) ; une note absente ou `None` vaut 0 — un échec LLM fait tomber le moment en fin de liste, il ne plante pas le classement |
| `rank_moments(moments, count) -> list[dict]` | 141 | Top `count` par score décroissant (tie-break sur `start`, pour la reproductibilité) ; deux moments qui se chevauchent à plus de `OVERLAP_MAX` (50 %) ne sont jamais gardés ensemble ; ne mute pas l'entrée |
| `ass_time(seconds) -> str` | 174 | `H:MM:SS.cc` — **tronque** les centièmes, n'arrondit pas (`round()` ferait passer 1,999 s à `.100`, trois chiffres pour un champ qui en attend deux, et le sous-titre disparaît) |
| `build_ass(words, start, end, y=0.74, size=64) -> str` | 193 | Sous-titres karaoké ASS, temps rebasés sur `start` ; le mot en cours de prononciation passe en rouge, le reste de la ligne reste visible ; chaque `Dialogue` tient jusqu'au **mot suivant** du groupe (le borner sur la fin du mot éteint la ligne à chaque respiration, et le français parlé en est plein) ; le style nomme `Anton`, la police OFL **embarquée** que `beatsync` associe déjà au nom logique « impact » — « Impact » serait résolu par fontconfig et libass lui substituerait silencieusement une sans-serif |
| `ffmpeg_path(path) -> str` | 242 | Chemin utilisable **à l'intérieur** d'une chaîne de filtre ffmpeg (`subtitles='<chemin>'`) : `:` échappé pour les lettres de lecteur Windows, apostrophe échappée en dernier (sinon le `\` qu'on introduit pour l'échapper serait lui-même mangé par le remplacement des séparateurs) |
| `transcript_digest(words, max_chars) -> str` | 444 | Transcript compacté en lignes `[mm:ss] phrase`, tronqué à `max_chars` — un modèle local a une fenêtre finie, mieux vaut couper proprement entre deux lignes que de faire couper la réponse au milieu d'un JSON. **La première phrase sort toujours**, même seule plus longue que `max_chars` : c'est la fenêtre « phrase géante » de `transcript_windows`, et sans cette exception son digest était vide (voir § 3). `DIGEST_MAX_CHARS` (40 000) n'est plus qu'un plafond de sécurité pour l'usage direct : le découpage réel passe par `transcript_windows` |
| `transcript_windows(words, max_chars) -> list[list[dict]]` | 466 | Fenêtres consécutives dont le digest tient dans `max_chars`, **sans jamais couper une phrase** ; une phrase seule plus longue forme sa propre fenêtre (la jeter perdrait du transcript) ; la concaténation des fenêtres redonne la liste d'origine |
| `moment_text(words, start, end) -> str` | 493 | Concatène les mots dont la fenêtre `[start, end]` chevauche celle du mot |

| Fonction (`speaker.py`) | l. | Invariant |
|---|---|---|
| `smooth_track(centers, default, dead_zone) -> list[float]` | 61 | Trous comblés (interpolation, ou valeur connue la plus proche en bord) puis moyenne glissante puis **zone morte** : le cadre ne bouge que si le centre s'écarte de plus de `dead_zone` de sa dernière position retenue. Chemin de **repli** uniquement (`speaker_cuts: False`) |
| `crop_size(src_w, src_h) -> (int,int)` | 86 | Rectangle 9:16 le plus large qui tienne dans la source, **les deux** dimensions paires (chroma 4:2:0 : une hauteur source impaire ferait échouer l'encodage yuv420p) ; une source déjà plus verticale que 9:16 n'est pas recadrée en largeur |
| `iou(a, b) -> float` | 289 | Recouvrement de deux rectangles rapporté à leur **union** (0 si disjoints, 0 si l'union est vide) |
| `link_tracks(detections, iou_min, max_gap) -> list[dict]` | 300 | Appariement glouton par recouvrement décroissant ; une piste non appariée reste candidate sur sa dernière position **tant qu'elle a moins de `max_gap` images** ; les identifiants suivent l'ordre d'apparition |
| `usable_tracks(tracks, frame_h) -> list[dict]` | 373 | Quatre rejets (existence, taille, agitation nulle, immobilité), ordre d'entrée conservé ; voir le tableau du § 2 bis |
| `speaker_timeline(tracks, cuts, n_frames, fps, min_shot) -> list[dict]` | 439 | Segments **contigus couvrant tout le clip** ; `track_id` à `None` seulement s'il n'y a aucune piste ; déterministe (départage sur l'identifiant le plus petit) |
| `crop_segments(timeline, tracks, crop_w, src_w, fps) -> list[dict]` | 127 | Un `x_start`/`x_end` par segment, **pairs et bornés dans l'image** ; trois niveaux de repli (rectangles du segment → position connue la plus proche → centre) |
| `track_to_segments(centers, sample_fps, crop_w, src_w) -> list[dict]` | 174 | Trajectoire plate → segments d'une image ; le `x_end` de chacun est l'ancre du **suivant**, sinon tout est immobile et `crop_expr` ne fait qu'un escalier de paliers secs |
| `crop_expr(segments, crop_w, src_w) -> str` | 225 | Expression ffmpeg `crop=x=…` : **interpole dans un segment, saute entre deux** ; une suite entièrement immobile rend un entier littéral ; borne la valeur au-delà du dernier segment ; jamais plus de `MAX_STEPS` paliers |
| `expr_needs_eval(expr) -> bool` | 208 | Vrai sauf si l'expression est un entier littéral. **Ne cherche pas `if(`** : une rampe de segment unique n'en contient pas et dépend pourtant de `t` |
| `sampling_cadence(source_fps, target_fps) -> (int, float)` | 536 | (une image sur N, **cadence réellement obtenue**) — `FRAME_FPS` est une cible, `source_fps / N` est le fait. Une cadence source inexploitable (0, négative, **NaN** — certains conteneurs en rendent, et NaN est *truthy*, donc un `or 30.0` chez l'appelant ne l'attrape pas) est ramenée à `FALLBACK_FPS` : sans ça `int(round(nan))` lève, personne n'attrape, et le clip est perdu au lieu d'être cadré au centre |
| `detect_cuts(diffs, ratio, floor) -> set[int]` | 559 | Seuil `max(ratio × médiane des écarts, floor)` ; `diffs[0]` n'a pas de sens (aucune image ne précède la première) et ne participe ni à la médiane ni au relevé |

### Les couleurs ASS sont en BGR

Au-dessus de `build_ass` (l.158-161) :

```python
# Rouge Dancing Dead #ff1e46. ASS code la couleur en BGR, pas en RGB : R=ff,
# G=1e, B=46 s'écrit &H461EFF&. Inverser donne du bleu, pas une erreur visible
# au test — d'où le commentaire.
ASS_HIGHLIGHT = "&H461EFF&"
```

Aucun test ne peut détecter une inversion R/B automatiquement (le rendu reste
un ASS syntaxiquement valide, juste de la mauvaise couleur) — c'est un piège
qui ne se voit qu'à l'œil, sur un rendu.

---

## 4. Les fonctions d'I/O, et le piège `_call_json`

| Fonction | l. | Rôle |
|---|---|---|
| `transcribe(video_path, model)` | 596 | faster-whisper, mots horodatés ; sonde `has_audio` avant de charger le modèle |
| `track_faces(video_path, start, end)` | 696 | OpenCV, cascade `haarcascade_frontalface_default.xml`, échantillonné à `SAMPLE_FPS` (2 fps). **Chemin de repli seulement** (`speaker_cuts: False`) |
| `render_clip(video_path, start, end, out_path, *, words, config)` | 735 | un seul appel ffmpeg : crop + scale + sous-titres |
| `_call_json(system, user, schema, seed, name)` | 406 | un appel LLM rendant du JSON conforme à `schema` |

Plus une cinquième dans `speaker.py` : `analyze_framing(video_path, start, end,
src_w, src_h, *, min_shot, log)` (l.617), la seule I/O du cadrage — décodage
séquentiel, détection Haar, mesure d'agitation, puis toute la chaîne pure du
§ 2 bis.

`_call_json` **n'est pas** `beatsync._call_llm` : celui-ci a le prompt
punchline codé en dur et une signature sans schéma. `_call_json` prend
`system`/`user`/`schema` en paramètres, pour servir aussi bien la proposition
de moments (`_PROPOSE_SCHEMA`) que la notation (`_SCORE_SCHEMA`). Sa
docstring (l.407-414) le dit explicitement :

```python
def _call_json(system: str, user: str, schema: dict, seed: int, name: str) -> dict:
    """Un appel LLM rendant du JSON conforme à `schema`. Isolé pour être mocké.

    N'utilise PAS beatsync._call_llm : celui-ci a le prompt punchline codé en
    dur et une signature sans schéma. On réutilise en revanche son choix de
    backend et son chargement de .env, et on honore comme lui `LLM_FALLBACK` —
    sans quoi `LLM_BACKEND` ne piloterait pas les deux sous-systèmes de la même
    façon, et un LM Studio éteint ferait échouer le clipper alors que beatsync
    aurait basculé sur Anthropic."""
```

Ce qui **est** réutilisé de `beatsync` : `_llm_backend()` (choix du backend
via `LLM_BACKEND`) et `_load_dotenv()` ; et la mécanique de repli est
reproduite à l'identique — un dict `_JSON_BACKENDS` nom → nom de fonction,
résolu par `globals()` au moment de l'appel pour rester monkeypatchable, et
un essai du backend nommé par `LLM_FALLBACK` si le primaire lève. Et un détail
d'API, l.302-304 :

```python
# L'API Messages n'expose pas de paramètre `seed` : on l'injecte dans le
# texte du prompt pour la reproductibilité, comme le fait _punchline_user_prompt.
user_with_seed = user + f"\n\n(variation n°{seed})"
```

---

## 5. Ce qui est mis en cache, et pourquoi

**Seul le transcript est mis en cache** — pas les réponses LLM (contrairement
à `beatsync.generate_punchlines`, qui cache par `(backend, modèle, préprompt,
count, seed)`). C'est délibéré : la transcription est de très loin l'étape la
plus lente (faster-whisper tourne en ~1× la durée de la vidéo sur CPU), les
appels LLM sont rapides en comparaison.

Le cache vit dans `data/clipper/<slug>/transcript.json`, un fichier par
source, jamais recalculé une fois écrit. Un cache **corrompu** (process tué en
pleine écriture, JSON tronqué) est traité comme un cache **absent** — même
convention que `beatsync.scan_clips` — et non comme une erreur : sans ça,
`cache.is_file()` resterait vrai pour toujours et la source ne serait plus
jamais retranscrite.

Relancer une analyse après avoir changé `clip_count` ou `min_dur`/`max_dur`
(les seuls réglages du clipper, § 6 — pas de préprompt configurable ici : les
prompts système `_PROPOSE_SYSTEM`/`_SCORE_SYSTEM` sont codés en dur) ne repaie
donc jamais la transcription — seules les étapes 3 à 7 retournent en jeu.

---

## 6. Les réglages et leurs bornes

`clipper.DEFAULTS`, fusionné avec `settings.json["clipper"]` (`load_settings`,
comme pour beatsync). `beatsync.DEFAULT_CONFIG["clipper"]` en est un **littéral
volontairement dupliqué** — il n'existe pas de constante partagée : importer
`clipper` depuis `beatsync` inverserait la dépendance (beatsync est le cœur du
montage, clipper un second front indépendant) et paierait cet import à chaque
`import beatsync`. Les deux littéraux sont tenus alignés par un test,
`test_clipper_defaults_match_beatsync` (`tests/test_clipper_llm.py`), pour que
le CLI et l'interface ne puissent pas diverger en silence. Ils s'éditent dans
l'onglet **Réglages**, carte « Clipper » :

| Clé | Défaut | Rôle |
|---|---|---|
| `whisper_model` | `"small"` | taille du modèle faster-whisper |
| `clip_count` | `8` | nombre de shorts gardés par source |
| `min_dur` | `15.0` | s, en dessous un extrait n'a pas d'histoire |
| `max_dur` | `60.0` | s, au-delà ce n'est plus un short |
| `digest_chars` | `6000` | caractères de transcript envoyés en un appel LLM (≈ 2600 tokens, à ~2,3 caractères par token) |
| `speaker_cuts` | `True` | recadrage sur le locuteur, avec coupes franches (§ 2 bis). À `False`, **repli** sur le suivi lissé du plus grand visage — la porte de sortie sur du plan large filmé à l'épaule, où la mesure d'agitation ne décide plus rien |
| `min_shot` | `1.2` | s, durée minimale d'un plan du recadrage |

Bornées côté serveur par `coerce_clipper` (`webui.py`, `CLIPPER_RANGES`) —
même motif que `coerce_overrides` pour beatsync : ces valeurs finissent dans
une ligne de commande ffmpeg et dans un nom de modèle, la défense est à la
source, pas au formulaire.

| Clé | Bornes | Pourquoi |
|---|---|---|
| `clip_count` | `[1, 30]` | au-delà de 30, ça sature un modèle local |
| `digest_chars` | `[1000, 60000]` | sous 1000 une fenêtre ne porte plus de contexte exploitable ; au-delà de 60 000, aucun modèle local courant ne suit et chaque fenêtre échouerait |
| `min_dur` / `max_dur` | `[3.0, 180.0]` | sous 3 s pas d'histoire, au-delà de 180 s ce n'est plus un short |
| `whisper_model` | `tiny\|base\|small\|medium\|large-v3` | liste blanche, seuls les modèles que faster-whisper sait résoudre |
| `min_shot` | `[0.4, 5.0]` | sous 0,4 s le cadre clignote (c'est déjà le plancher qui s'applique sur une coupe de la source, `CUT_MIN_SHOT`) ; au-delà de 5 s il reste sur quelqu'un qui a fini de parler depuis longtemps |
| `speaker_cuts` | booléen strict | un booléen JSON, ou les chaînes `"true"`/`"false"` ; tout le reste est refusé en 400 plutôt qu'interprété — `bool("peut-etre")` vaut `True`, une faute de frappe activerait la fonctionnalité |
| `min_dur` vs `max_dur` | `min_dur <= max_dur` | refusé en 400 : une inversion ne rend aucun clip et la source finit en `failed` avec un message accusant le recalage |

`SEED` (1337) n'est **pas** un réglage exposé : il est fixe dans
`clip_source.py`, à la différence de beatsync où chaque variante tire sa
propre seed. La reproductibilité du clipper vaut au niveau de la **source** —
relancer une analyse doit rendre les mêmes clips, pour pouvoir comparer un
réglage à un autre sans le bruit d'un tirage différent.

`OVERSHOOT` (1,5) n'est pas non plus un réglage : c'est un ratio interne entre
candidats demandés et candidats gardés. Ce n'est un « +50 % » que sur une source
assez courte pour tenir en **une seule fenêtre** de transcript : au-delà, le
plancher de deux candidats par fenêtre (§ 3) l'emporte, et c'est
`SCORE_BUDGET` (§ 5) qui borne réellement le travail.

---

## 7. Ce qui est testé

`tests/test_clipper_*.py` + `tests/test_clip_source.py` +
`tests/test_speaker_*.py`, un fichier par sous-domaine :

| Fichier | Couvre |
|---|---|
| `test_clipper_snap.py` | `sentences` et `snap_to_speech` : coupe sur ponctuation/silence, respiration, troncature/extension aux bornes, rejet de ce qui ne rentre pas |
| `test_clipper_rank.py` | `moment_score` (pondération, note absente) et `rank_moments` (tri, chevauchements majoritaires écartés, déterminisme sur ex æquo, non-mutation) |
| `test_speaker_geometry.py` | `_fill_holes`, `smooth_track` (zone morte, trous de bord, secousse isolée, liste vide), `crop_size` (dont hauteur impaire, plafonnement à la largeur source) |
| `test_speaker_tracks.py` | `iou`, `link_tracks` (identités conservées, croisement sans échange, trou court toléré, borne d'ancienneté réglable, ids dans l'ordre d'apparition), `usable_tracks` (les trois filtres, l'habillage figé et l'habillage qui frissonne écartés, la personne qui bouge et le visage qui s'approche conservés, un faux positif isolé ne sauve pas une piste de vignettes, ordre conservé), `detect_cuts` (coupe franche relevée, pic de mouvement dans un plan agité ignoré, plancher sur un clip calme, `diffs[0]` neutre), `sampling_cadence` (source multiple de la cible, PAL, cinéma, NTSC, source plus lente que la cible) |
| `test_speaker_timeline.py` | `speaker_timeline` (marge de bascule, `MIN_SHOT` contre le clignotement, rafale de coupes, cadrage initial à l'aveugle non verrouillant, silence conservant le cadrage, contiguïté, déterminisme), `crop_segments` (ancrage sur le visage du segment, interpolation d'une dérive, position tenue faute de rectangle, centre en dernier recours, x pairs et bornés), `crop_expr` (constante quand tout est immobile, saut sec entre deux segments, interpolation dans un segment, valeur tenue au-delà de la fin) et `expr_needs_eval` |
| `test_clipper_ass.py` | `ass_time`, `build_ass` (en-tête, un événement par mot, rebasage, surlignage, échappement, absence de trou entre deux mots, police embarquée), `ffmpeg_path` (chemins Windows/POSIX/apostrophe) |
| `test_clipper_llm.py` | `transcript_digest` (dont le digest **non vide** d'une fenêtre « phrase géante »), `transcript_windows` (découpe sur les phrases, fenêtre unique, phrase géante isolée, liste vide, aucun mot perdu), `moment_text`, `propose_moments` et `score_moment` **mockés** sur `_call_json` : conversion, entrées malformées ignorées, `moments` qui n'est pas une liste, bornes de durée réglées rappelées dans le prompt, boucle sur les fenêtres et seeds décalées, une fenêtre en échec ne perd pas les autres, dégradation si le LLM échoue ou rend une racine non-objet **avec journalisation de la cause** ; le repli `LLM_FALLBACK` de `_call_json` ; la remontée des erreurs LM Studio (corps d'un HTTP 400, `{"error": …}` sur un 200, réponse sans `choices`, JSON illisible dont on montre un extrait) via un `urlopen` monkeypatché ; et `crop_supports_eval` (aide ffmpeg 7 vs 8, sonde en échec, mise en cache) sur un `subprocess.run` monkeypatché |
| `test_clip_source.py` | `slug_for`, et `process` de bout en bout (mocké) : pipeline complet, cache du transcript, source muette, candidat irrécalable ignoré, échec de rendu isolé, remplacement des clips au relancement, non-perte des clips précédents si le LLM est muet, cache de transcript corrompu ignoré, budget de notation respecté **et annoncé**, progression `[i/n]` journalisée, bornes de durée transmises au LLM |
| `test_render_clip.py` | **Câblage** de `render_clip`, les quatre points d'I/O monkeypatchés (`probe_size`, `speaker.analyze_framing`, `crop_supports_eval`, `subprocess.run`) et la chaîne de filtres inspectée : arguments passés à `analyze_framing` et expression retrouvée dans le `crop=`, chemin de repli emprunté quand `speaker_cuts` est faux (et `analyze_framing` alors jamais appelée), `:eval=frame` présent si et seulement si `expr_needs_eval` **et** `crop_supports_eval` sont vrais, `.ass` effacé après le rendu. Ce n'est pas le rendu qui est testé mais l'accord des signatures — c'est là que le chantier a cassé deux fois |
| `test_clipper_api.py` | Endpoints `webui.py` : `coerce_clipper` (bornes, rejet non-numérique/modèle inconnu, booléen refusé sur les cinq champs numériques, `min_dur > max_dur`), bloc `clipper` d'un preset coercé par `coerce_overrides`, upload/suppression d'une source (409 pendant un job, échec de `rmtree`, slug d'un dossier orphelin non réattribué, durée sondée), promotion refusée sans piste audio, lecture/statut/suppression d'un clip (avec garde anti-traversal), projection sans chemins disque dans `/api/state`, validation de l'URL YouTube (espacement, hôte, casse, type) |

Ce qui n'est **pas** testé automatiquement, faute de pouvoir le faire tourner
en CI sans dépendances lourdes : le CORPS des quatre fonctions d'I/O
`transcribe` (faster-whisper), `track_faces` et `speaker.analyze_framing`
(OpenCV + décodage vidéo réel) et `render_clip` (ffmpeg réel) — seules leur
logique pure sous-jacente (toute la colonne de gauche des deux tableaux du
§ 3), la sonde `crop_supports_eval` (sur une aide ffmpeg factice) et le
**câblage** de `render_clip` (`test_render_clip.py`, tout monkeypatché) sont
testés. D'où le § 7 bis
ci-dessous : ce qui ne peut pas être testé doit être **regardé**. Idem côté
React : `frontend/src/features/clipper/` (`ClipperTab.tsx`, `SourceDetail.tsx`)
n'a pas de test automatisé.

---

## 7 bis. La vérification à l'œil, et ce qu'elle a montré

Rien de ce que fait `speaker.py` ne se voit dans un test unitaire : un cadre qui
rate le visage sort un mp4 parfaitement valide. Le seul contrôle possible est de
rendre un clip et de le regarder image par image. Fait sur la fenêtre 150-180 s
de la source d'essai, en comparant `speaker_cuts: True` et `False`, sur 13
images prises au milieu de chaque plan et de part et d'autre de chaque coupe
(rapport détaillé :
`.superpowers/sdd/2026-08-08-locuteur-actif/task-8-report.md`).

Ce que ça a donné, sans arrondi :

- **Le cadre tient un visage sur 12 images sur 13.** La seule à le rater est
  prise en plein **filé de caméra** (un mouvement de 0,2 s où toute l'image est
  floue) : le visage y sort par le bord gauche du crop. Aucune autre image ne
  perd son sujet.
- **Le cadre tient le visage de celui qui parle sur 8 images sur 13.** Les
  quatre autres sont des plans larges où celui qui parle est **l'animateur
  filmé de dos** : il n'y a alors de visage de locuteur **nulle part** dans la
  source, et le cadre tient ceux des personnes qui écoutent. C'est le
  comportement le moins mauvais possible, pas un ratage — mais il faut savoir
  qu'il existe avant de lire une statistique de « bon cadrage ».
- **L'habillage figé est hors champ sur 13 images sur 13.** Les vignettes
  occupent les 230 premiers pixels de la source ; le bord gauche du crop n'est
  jamais descendu sous 432. Le **repli**, lui, s'y colle : sur les mêmes
  instants, plusieurs images ne montrent **que** les quatre vignettes et un mur
  blanc. C'est la démonstration la plus nette de ce que `usable_tracks` apporte.
- Les coupes ne clignotent pas : 4 plans sur 30 s, le plus court à 2,1 s.
  `detect_cuts` a relevé **7 coupes, toutes vraies** (vérifiées une par une sur
  des paires d'images de part et d'autre).

Deux défauts réels, à connaître :

1. **Le cadrage retarde de 0,2 à 0,4 s sur une coupe de la source**, parce que
   la bascule est décidée sur l'agitation et non sur la coupe seule. Visible
   seulement si on cherche.
2. **Dans un segment long qui couvre plusieurs plans de la source**, `crop_expr`
   n'interpole qu'entre le premier et le dernier rectangle du segment : le
   sujet d'un plan intermédiaire se retrouve poussé vers le bord. Vu une fois
   sur les 13 images, sur un segment de 6,4 s.

---

## 8. Ce qui n'est pas fait (lot 2), et pourquoi

Volontairement hors du lot 1, pour ne pas dériver de la spec :

- **Mode « sets / rave » sans parole, et bascule automatique parlé /
  non-parlé** — le lot 1 suppose une source parlée de bout en bout
  (`has_audio` + transcript vide → `failed`). Détecter qu'une vidéo est un
  live sans voix et basculer sur une autre logique de découpage est un
  problème différent, pas une variante de celui-ci.
- **Split-screen à deux visages** — `speaker.py` élit un seul visage par
  instant ; montrer deux locuteurs simultanément changerait le crop, la
  timeline et le rendu.
- **Suivi des visages de profil** — la cascade est frontale, et sa piste est
  tenue à sa dernière position (§ 2 bis). Un détecteur de profil, ou un suivi
  visuel qui ne dépende pas d'une détection à chaque passe, est un autre
  chantier.
- **Diarisation audio et modèles de détection de locuteur actif** — écartés au
  profit de l'agitation de bouche, avec sa limite mesurée (§ 2 bis). C'est là
  qu'il faudra revenir le jour où le plan large en mouvement comptera.
- **Recadrage vertical** — le crop reste ancré en haut (`y=0`) ; seul le `x`
  suit.
- **Édition manuelle des bornes d'un clip dans l'UI** — les bornes sortent du
  recalage (`snap_to_speech`) et ne sont pas ajustables a posteriori ; il n'y
  a pas de ré-encodage à la demande.
- **B-roll, zooms automatiques, habillage** — le rendu est un crop suivi +
  sous-titres, rien de plus.
- **Note `trend`** — le classement ne juge que hook / flow / value.
- **Publication automatique** — même décision que pour beatsync
  (2026-07-08) : la sortie est une bibliothèque à poster à la main.
