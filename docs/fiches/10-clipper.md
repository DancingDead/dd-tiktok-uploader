# 10 — clipper.py / clip_source.py

> `clipper.py` (logique pure + I/O) · `clip_source.py` (orchestrateur)
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
| Cadrage | crop statique par segment (`frame_extract`) | crop **suivi** frame à frame (visage) |
| LLM | punchlines courtes, un appel | proposition + notation, deux rôles différents |

Le lot 1 (celui documenté ici) ne traite **que le contenu parlé**. Une source
sans piste audio ou sans parole détectée passe en `failed` avec un message
explicite — pas de mode « sets / rave » sans voix (voir § 6).

---

## 2. Le pipeline en sept étapes

```
1. Import de la source          webui.py (upload / lien YouTube → inbox → promotion)
2. Transcription                clipper.transcribe            (I/O, mis en cache)
3. Proposition de moments       clipper.propose_moments        (I/O, LLM)
4. Recalage sur les phrases     clipper.snap_to_speech         (pur)
5. Notation                     clipper.score_moment           (I/O, LLM)
6. Classement                   clipper.rank_moments           (pur)
7. Rendu                        clipper.render_clip            (I/O, ffmpeg)
```

`clip_source.process(conn, root, source_id, config, log)` enchaîne 2 → 7 pour
une source, en écrivant le statut en base à chaque étape
(`transcribing`/`analyzing`/`rendering`/`done`/`failed`) pour que l'UI suive
l'avancement par polling (`GET /api/jobs/<job_id>`, mécanisme générique déjà
utilisé par `generate_niche.py`).

### 1. Import — `webui.py`

Deux voies : upload direct (`POST /api/clipper/sources`) ou import YouTube en
**deux temps** (`POST /api/clipper/sources/link` télécharge dans
`data/clipper/_inbox/` sous le nom choisi par yt-dlp — inconnu avant la fin du
téléchargement — puis `POST /api/clipper/inbox/<name>` **déplace** le fichier
vers la source et crée la ligne `clipper_sources`). L'URL est validée par
liste blanche de `netloc` YouTube **et** rejet de tout caractère
d'espacement : elle finit dans un fichier que `fetch_tracks.parse_links`
découpe **une ligne = un argument** passé à yt-dlp, donc un espace ou un
retour à la ligne y injecterait une option (`--exec`, `-o`, …).

### 2. Transcription — `clipper.transcribe`

`has_audio` est sondée **avant** de charger le modèle Whisper — sans ça, une
source muette fait planter le décodeur audio de faster-whisper avec une
`IndexError` opaque plutôt que de dégrader proprement. faster-whisper
(`WhisperModel(model, device="cpu", compute_type="int8")`) produit des mots
horodatés, langue auto-détectée.

### 3. Proposition — `clipper.propose_moments`

Un appel LLM (via `_call_json`, § 4) demande `count × OVERSHOOT` (1,5×)
candidats à partir d'un digest compact de la transcription
(`transcript_digest`) : le classement (étape 6) a besoin de matière à trier,
et le recalage (étape 4) en rejette une partie. Dégrade en `[]` si le LLM
échoue — exactement comme `beatsync.generate_punchlines`.

### 4. Recalage — `clipper.snap_to_speech`

Étend/rétrécit chaque candidat sur des frontières de phrase entières, dans
`[min_dur, max_dur]`. `None` si impossible : un clip de moins vaut mieux qu'un
clip qui commence au milieu d'un mot.

### 5. Notation — `clipper.score_moment`

Un appel LLM par candidat retenu, sur le texte **recalé** (pas le brut) :
trois notes 0-100 (hook / flow / value). Dégrade en zéros si le LLM échoue —
le moment tombe en fin de classement, il ne fait pas échouer la source.

### 6. Classement — `clipper.rank_moments`

Garde les `count` meilleurs par score, en écartant les doublons de position
(deux candidats qui décrivent le même moment).

### 7. Rendu — `clipper.render_clip`

Un seul appel ffmpeg par clip : suivi de visage (`track_faces`, OpenCV) →
lissage (`smooth_track`) → crop 9:16 suivi (`crop_expr`) → scale 1080×1920 →
sous-titres karaoké incrustés (fichier `.ass` généré par `build_ass`, supprimé
après le rendu). Un clip qui échoue au rendu ne fait pas perdre les autres
(`try/except` dans la boucle de `clip_source.process`) ; si **tous** les
rendus échouent, la source passe en `failed`.

---

## 3. Les fonctions pures, une ligne chacune

| Fonction | Invariant |
|---|---|
| `sentences(words) -> list[(int,int)]` | Une phrase se termine sur ponctuation forte OU un blanc ≥ `SENTENCE_GAP` (0,6 s) — le silence est plus fiable que la ponctuation sur du français transcrit à l'oral |
| `snap_to_speech(start, end, words, min_dur, max_dur) -> (float,float)\|None` | Recale sur des frontières de phrase entières ; `None` si aucune combinaison ne rentre dans `[min_dur, max_dur]` |
| `rank_moments(moments, count) -> list[dict]` | Top `count` par score décroissant (tie-break sur `start`, pour la reproductibilité) ; deux moments qui se chevauchent à plus de `OVERLAP_MAX` (50 %) ne sont jamais gardés ensemble ; ne mute pas l'entrée |
| `moment_score(moment) -> float` | Moyenne pondérée `WEIGHTS` (hook 0,4 / flow 0,3 / value 0,3) ; une note absente ou `None` vaut 0 — un échec LLM fait tomber le moment en fin de liste, il ne plante pas le classement |
| `smooth_track(centers, default, dead_zone) -> list[float]` | Trous comblés (interpolation, ou valeur connue la plus proche en bord) puis moyenne glissante puis **zone morte** : le cadre ne bouge que si le centre s'écarte de plus de `dead_zone` de sa dernière position retenue |
| `crop_size(src_w, src_h) -> (int,int)` | Rectangle 9:16 le plus large qui tienne dans la source, dimensions paires (chroma 4:2:0) ; une source déjà plus verticale que 9:16 n'est pas recadrée en largeur |
| `crop_expr(track, sample_fps, crop_w, src_w) -> str` | Compile la trajectoire en expression ffmpeg `crop=x=…` ; une trajectoire immobile rend un simple nombre (pas d'`eval=frame` payé pour rien) ; un point par seconde, la zone morte a déjà supprimé ce qui bouge plus vite |
| `build_ass(words, start, end, y=0.74, size=64) -> str` | Sous-titres karaoké ASS, temps rebasés sur `start` ; le mot en cours de prononciation passe en rouge, le reste de la ligne reste visible |
| `ass_time(seconds) -> str` | `H:MM:SS.cc` — **tronque** les centièmes, n'arrondit pas (`round()` ferait passer 1,999 s à `.100`, trois chiffres pour un champ qui en attend deux, et le sous-titre disparaît) |
| `ffmpeg_path(path) -> str` | Chemin utilisable **à l'intérieur** d'une chaîne de filtre ffmpeg (`subtitles='<chemin>'`) : `:` échappé pour les lettres de lecteur Windows, apostrophe échappée en dernier (sinon le `\` qu'on introduit pour l'échapper serait lui-même mangé par le remplacement des séparateurs) |
| `transcript_digest(words, max_chars) -> str` | Transcript compacté en lignes `[mm:ss] phrase`, tronqué à `max_chars` — un modèle local a une fenêtre finie, mieux vaut couper proprement entre deux lignes que de faire couper la réponse au milieu d'un JSON |
| `moment_text(words, start, end) -> str` | Concatène les mots dont la fenêtre `[start, end]` chevauche celle du mot |

---

## 4. Ce qui est mis en cache, et pourquoi

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

Relancer une analyse après avoir changé `clip_count`, `min_dur`/`max_dur` ou
le préprompt ne repaie donc jamais la transcription — seules les étapes 3 à 7
retournent en jeu.

---

## 5. Les réglages et leurs bornes

`clipper.DEFAULTS`, fusionné avec `settings.json["clipper"]` (`load_settings`,
comme pour beatsync) :

| Clé | Défaut | Rôle |
|---|---|---|
| `whisper_model` | `"small"` | taille du modèle faster-whisper |
| `clip_count` | `8` | nombre de shorts gardés par source |
| `min_dur` | `15.0` | s, en dessous un extrait n'a pas d'histoire |
| `max_dur` | `60.0` | s, au-delà ce n'est plus un short |

Bornées côté serveur par `coerce_clipper` (`webui.py`, `CLIPPER_RANGES`) —
même motif que `coerce_overrides` pour beatsync : ces valeurs finissent dans
une ligne de commande ffmpeg et dans un nom de modèle, la défense est à la
source, pas au formulaire.

| Clé | Bornes | Pourquoi |
|---|---|---|
| `clip_count` | `[1, 30]` | au-delà de 30, ça sature un modèle local |
| `min_dur` / `max_dur` | `[3.0, 180.0]` | sous 3 s pas d'histoire, au-delà de 180 s ce n'est plus un short |
| `whisper_model` | `tiny\|base\|small\|medium\|large-v3` | liste blanche, seuls les modèles que faster-whisper sait résoudre |

`SEED` (1337) n'est **pas** un réglage exposé : il est fixe dans
`clip_source.py`, à la différence de beatsync où chaque variante tire sa
propre seed. La reproductibilité du clipper vaut au niveau de la **source** —
relancer une analyse doit rendre les mêmes clips, pour pouvoir comparer un
réglage à un autre sans le bruit d'un tirage différent.

`OVERSHOOT` (1,5) n'est pas non plus un réglage : c'est un ratio interne entre
candidats demandés et candidats gardés.

---

## 6. Ce qui n'est pas fait (lot 2), et pourquoi

Volontairement hors du lot 1, pour ne pas dériver de la spec :

- **Mode « sets / rave » sans parole, et bascule automatique parlé /
  non-parlé** — le lot 1 suppose une source parlée de bout en bout
  (`has_audio` + transcript vide → `failed`). Détecter qu'une vidéo est un
  live sans voix et basculer sur une autre logique de découpage est un
  problème différent, pas une variante de celui-ci.
- **Split-screen à deux visages** — `track_faces` ne suit que le plus grand
  visage détecté par frame ; gérer deux locuteurs simultanés changerait le
  crop, le suivi et le rendu.
- **Édition manuelle des bornes d'un clip dans l'UI** — les bornes sortent du
  recalage (`snap_to_speech`) et ne sont pas ajustables a posteriori ; il n'y
  a pas de ré-encodage à la demande.
- **B-roll, zooms automatiques, habillage** — le rendu est un crop suivi +
  sous-titres, rien de plus.
- **Note `trend`** — le classement ne juge que hook / flow / value.
- **Publication automatique** — même décision que pour beatsync
  (2026-07-08) : la sortie est une bibliothèque à poster à la main.
