# Spec — Onglet « Clipper » : vidéo longue → shorts classés par pertinence

**Date** : 2026-08-07
**Statut** : validé, prêt pour plan d'implémentation
**Lot** : 1 sur 2 (le lot 2 couvrira les captations sans parole — voir « Hors périmètre »)

## Problème / objectif

L'usine sait fabriquer des edits beat-synchronisés à partir de **rushes muets**
(anime, images). Elle ne sait rien faire d'un contenu **parlé** : une interview
d'artiste du label, un live, un Q&A. Ce matériau existe et ne produit rien
aujourd'hui.

On veut un second front d'usine, indépendant du montage beatsync : on donne une
vidéo longue (upload ou lien YouTube), l'outil en extrait les meilleurs moments,
les **classe par pertinence** sur le contenu du propos, et rend des shorts 9:16
recadrés et sous-titrés, prêts à poster à la main.

Modèle de référence : Opus Clip. Sa note de viralité (0-99, non documentée
techniquement par l'éditeur) repose sur quatre signaux annoncés — **hook**,
**flow**, **value**, **trend**. On en retient trois ; `trend` est écarté, il
suppose une connaissance du marché en temps réel que rien ici ne fournit, et une
note inventée par un LLM local sur ce critère serait du bruit présenté comme un
chiffre.

## Décisions de cadrage (issues du brainstorming)

- **Sortie = shorts finis**, pas des extraits bruts à monter ailleurs. Recadrés,
  sous-titrés, versés dans une bibliothèque proposed/approved/rejected identique
  à celle des vidéos de niche.
- **Transcription locale** (faster-whisper), pas d'API payée ni de sous-titres
  YouTube. Cohérent avec le choix LM Studio : coût nul, et surtout Whisper donne
  les timestamps **au mot**, dont dépendent le recalage des coupes et les
  sous-titres karaoké. Les sous-titres YouTube n'ont ni l'un ni l'autre.
- **Recadrage à suivi de visage**, crop unique et lissé. Écarté : le
  split-screen à deux visages (il faut décider qui parle — un lot à lui seul) et
  le crop centré fixe (coupe des têtes sur la moitié des interviews).
- **Sous-titres mot à mot surlignés**. Whisper fournit déjà les timings ; un
  fichier ASS coûte un seul filtre ffmpeg. Le format l'exige : un short parlé se
  regarde en son coupé.
- **Trois étapes plutôt qu'un appel LLM unique** : le LLM propose, du code pur
  recale, le LLM note. Un modèle local rend des bornes approximatives et note
  mal quand il doit tout produire d'un coup ; la fenêtre glissante exhaustive a
  été écartée pour son coût (des centaines d'appels par heure de vidéo) et
  parce qu'elle note chaque fenêtre hors contexte.
- **Aucune modification de `beatsync.py`.** Le clipper n'en réutilise que
  `_call_llm`.

## Design

### 1. Modules

**`clipper.py`** — nouveau module, même moule que beatsync : logique pure au
centre, I/O isolée en périphérie pour être mockable. Trois fonctions d'I/O
seulement — `transcribe`, `track_faces`, `render_clip` — plus `_call_llm`
importé de beatsync (dispatch LM Studio/Anthropic, déjà en place et déjà mocké
dans `test_subtitles.py`).

**`clip_source.py <source_id> [<root>]`** — orchestrateur, calqué sur
`generate_niche.py`. Lancé en tâche de fond par l'UI via le `start_job`
existant. Écrit le statut en base à chaque étape pour que l'UI suive
l'avancement.

`faster-whisper` est ajouté aux dépendances et **importé paresseusement**, comme
librosa : coûteux, et inutile à toute la logique pure.

### 2. Données

Deux tables dans `platform.db`, créées par le mécanisme de migration existant
(`_migrate` / `CREATE TABLE IF NOT EXISTS` — rappel : `CREATE TABLE IF NOT
EXISTS` ne met pas à jour une base déjà créée, toute colonne ajoutée après coup
passe par `_ADDED_COLUMNS`).

`clipper_sources` :

| colonne | rôle |
|---|---|
| `id`, `title` | |
| `path` | la vidéo longue sur disque |
| `duration` | secondes |
| `status` | `pending` → `transcribing` → `analyzing` → `rendering` → `done` \| `failed` |
| `error` | message si `failed` |
| `created_at` | |

`clipper_clips` :

| colonne | rôle |
|---|---|
| `id`, `source_id` | |
| `start`, `end` | bornes **recalées**, en secondes |
| `title` | proposé par le LLM |
| `hook`, `flow`, `value` | notes 0-100 |
| `score` | note agrégée |
| `why` | justification en une phrase |
| `path` | le short rendu |
| `status` | `proposed` \| `approved` \| `rejected` \| `posted` |

Les sources sont **partagées**, pas rattachées à un membre : c'est la logique de
`tracks/` et `clips/`, et le projet n'a pas de notion de propriété d'asset.

Sur disque, un dossier par source, **hors des catalogues partagés** pour ne pas
polluer `clips/` :

```
data/clipper/<slug-de-la-source>/
    source.mp4          # upload ou yt-dlp
    transcript.json     # mots horodatés, mis en cache
    clips/03-le-moment-ou.mp4
```

### 3. Réglages

Quatre réglages, dans l'onglet Réglages existant (`settings.json`), sous une
clé `clipper` :

| clé | défaut | rôle |
|---|---|---|
| `whisper_model` | `"small"` | taille du modèle de transcription |
| `clip_count` | `8` | le **N** de « top N clips », partout dans cette spec |
| `min_dur` | `15` | durée minimale d'un clip, en secondes |
| `max_dur` | `60` | durée maximale |

`clip_count`, `min_dur` et `max_dur` sont coercés côté serveur (voir § 7).

### 4. Pipeline

**4.1 Acquisition.** Upload de fichier, ou lien YouTube via le `yt-dlp` déjà en
place (`--video`, ≤1080p mp4).

**4.2 Transcription.** faster-whisper, `word_timestamps=True`, langue
auto-détectée. Écrit `transcript.json` et **ne retranscrit jamais deux fois** la même
source : c'est de loin l'étape la plus lente (~1× le temps réel en CPU sur
`small`), donc elle est mise en cache sur disque comme l'est déjà le scan de
clips.

**4.3 Proposition.** `transcript_digest(words)` — **pure** — compacte les mots
en texte horodaté `[mm:ss] phrase`. Un appel LLM demande **1,5 × N candidats** :
on en propose plus qu'on n'en garde, pour que le classement ait de la matière à
trier.

**4.4 Recalage.** `snap_to_speech(candidate, words, config)` — **pure et
testée**. C'est la fonction qui sépare un résultat propre d'un résultat
amateur :

- le début se cale sur un début de phrase, la fin sur une fin de phrase ; jamais
  au milieu d'un mot ;
- les bornes sont étendues jusqu'au silence adjacent, pour ne pas ouvrir sur une
  demi-syllabe ;
- la durée est bornée (15-60 s par défaut, réglable) : un candidat trop long est
  tronqué à la frontière de phrase la plus proche, un candidat trop court est
  rejeté.

**4.5 Notation.** Un appel LLM par candidat, sur le texte **recalé** et non sur
le brut, rendant `{hook, flow, value, why}`. En cas d'échec du LLM, le candidat
garde un score nul et tombe en fin de liste : comme les punchlines qui dégradent
en `[]`, l'usine ne bloque jamais sur le LLM.

**4.6 Classement.** `rank_moments(scored)` — **pure** :

```
score = 0.4 · hook + 0.3 · flow + 0.3 · value
```

Le hook pèse le plus parce que c'est la seule des trois notes qui décide du
scroll ; les deux autres ne jouent qu'une fois le spectateur retenu. Les
candidats qui se chevauchent à plus de 50 % sont dédupliqués — on garde le
meilleur — puis on conserve le top N.

**4.7 Recadrage et rendu**, un clip à la fois :

- `track_faces(video, start, end)` — I/O : échantillonne à 2 fps et retourne le
  centre x du plus grand visage par frame. Cascade **visage réel** d'OpenCV, pas
  le `lbpcascade_animeface` de beatsync, qui ne détecte que les visages dessinés.
- `smooth_track(centroids, config)` — **pure et testée** : trous interpolés,
  moyenne glissante, et une **zone morte** — le cadre ne bouge que si le visage
  s'écarte de plus de ~8 % de la largeur. Sans elle, le crop respire en
  permanence et donne le mal de mer. Aucun visage sur tout le clip → repli sur un
  crop centré fixe.
- `crop_expr(track, ...)` — **pure et testée** : la trajectoire lissée est
  ramenée à un point par seconde et compilée en expression ffmpeg
  `crop=…:x='if(lt(t,1),320,if(lt(t,2),334,…))':eval=frame`, avec un nombre de
  paliers borné (expression lisible, ffmpeg qui ne rame pas).
- `build_ass(words, start, end, style)` — **pure et testée** : fichier ASS
  karaoké, 3-4 mots à l'écran, mot courant en `#ff1e46`, contour noir, ancré au
  tiers inférieur.
- `render_clip(...)` — I/O : **un seul** appel ffmpeg,
  `crop → scale 1080×1920 → subtitles=`, H.264, flags `bitexact` comme partout
  ailleurs dans le projet.

### 5. Reproductibilité

La seed est transmise aux deux appels LLM — le cache de `_call_llm` est déjà
indexé par `(backend, modèle, préprompt, count, seed)` — et tout le reste du
pipeline est déterministe. Relancer une source rend les mêmes clips.

### 6. Interface

Un onglet **« Clipper »**, cinquième après Niches / Presets / Catalogue /
Réglages, en `frontend/src/features/clipper/` (React + shadcn, même moule que
`catalogue/` et `niches/`) :

- `ClipperTab.tsx` — liste des sources avec badge d'étape (« transcription… » →
  « analyse… » → « rendu… » → « 8 clips »), et la zone d'ajout à deux entrées
  (upload / lien YouTube) identique à celle du Catalogue ;
- `SourceDetail.tsx` — les clips **triés par score décroissant**, chacun en
  carte : score en gros, les trois sous-notes, le titre proposé, la
  justification en une phrase, un lecteur, et valider / rejeter / télécharger.
  Réutilise `VideoLibrary.tsx` autant que possible : la mécanique
  proposed/approved/rejected est exactement la même.

Le fallback Jinja (`templates/index.html`) **ne reçoit pas** l'onglet : c'est un
secours de dev, pas une seconde UI à maintenir en double.

### 7. API

```
GET    /api/clipper/sources            liste + statuts
POST   /api/clipper/sources            upload d'un fichier
POST   /api/clipper/sources/link       import YouTube (tâche de fond yt-dlp)
POST   /api/clipper/sources/<id>/run   lance clip_source.py (tâche de fond)
DELETE /api/clipper/sources/<id>       ligne + dossier disque
GET    /api/clipper/clips/<id>         lecture, ?dl=1 pour télécharger
POST   /api/clipper/clips/<id>/status  approved | rejected | posted
DELETE /api/clipper/clips/<id>
```

Mêmes garde-fous que l'existant : `before_request` couvre déjà tout `/api/*` ;
garde anti-traversal sur les chemins de fichiers (un fichier directement sous le
dossier de la source, rien d'autre) ; **coercion serveur des champs numériques**
(durées min/max, N) → 400 si non convertible. `title` et `why` viennent du LLM,
donc sont des données non fiables : React les échappe, et ils sont en plus
tronqués en longueur côté serveur avant écriture en base.

### 8. Tests

Un fichier par unité pure, comme le reste du projet :

| fichier | couvre |
|---|---|
| `tests/test_snap.py` | recalage : jamais de coupe en plein mot, calage sur les phrases, min/max respectés, rejet de l'irrécupérable |
| `tests/test_rank.py` | pondération, dédup des chevauchements >50 %, tolérance à un score manquant |
| `tests/test_track.py` | lissage : trous interpolés, zone morte respectée, repli centré sans détection |
| `tests/test_crop_expr.py` | expression ffmpeg bien formée, paliers bornés, valeurs dans le cadre |
| `tests/test_ass.py` | ASS : timings au mot, fenêtre de 3-4 mots, échappement du texte |
| `tests/test_clipper_api.py` | endpoints via `test_client`, coercion et 400, anti-traversal |
| `tests/test_db.py` (étendu) | migration des deux nouvelles tables sur une base existante |

Whisper, ffmpeg et le LLM ne sont jamais appelés en test : ils sont derrière
`transcribe`, `track_faces`, `render_clip` et `_call_llm`.

## Vidéo sans parole

Hors périmètre du lot 1. Si le transcript revient quasi vide, la source passe en
`failed` avec un message explicite, plutôt que de produire des clips au hasard.

## Hors périmètre (lot 1)

- Mode « sets / rave » sans parole, et bascule automatique parlé/non-parlé — lot 2.
- Split-screen à deux visages.
- Édition manuelle des bornes d'un clip dans l'UI.
- B-roll, zooms automatiques, habillage.
- Note `trend` (voir « Problème / objectif »).
- Publication automatique — décision projet du 2026-07-08, inchangée.
