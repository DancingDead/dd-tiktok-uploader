# 10 — clipper.py / clip_source.py

> `clipper.py` (629 lignes, logique pure + I/O) · `clip_source.py` (189
> lignes, orchestrateur) · endpoints `/api/clipper/*` dans `webui.py` l.597-795
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
explicite — pas de mode « sets / rave » sans voix (voir § 8).

---

## 2. Le pipeline en sept étapes

```
1. Import de la source          webui.py l.615-710 (upload / lien YouTube → inbox → promotion)
2. Transcription                clipper.transcribe            l.483 (I/O, mis en cache)
3. Proposition de moments       clipper.propose_moments        l.425 (I/O, LLM)
4. Recalage sur les phrases     clipper.snap_to_speech         l.55  (pur)
5. Notation                     clipper.score_moment           l.452 (I/O, LLM)
6. Classement                   clipper.rank_moments           l.125 (pur)
7. Rendu                        clipper.render_clip            l.587 (I/O, ffmpeg)
```

`clip_source.process(conn, root, source_id, config, log)` (l.42) enchaîne
2 → 7 pour une source, en écrivant le statut en base à chaque étape
(`transcribing`/`analyzing`/`rendering`/`done`/`failed`) pour que l'UI suive
l'avancement par polling (`GET /api/jobs/<job_id>`, mécanisme générique déjà
utilisé par `generate_niche.py`).

### 1. Import — `webui.py` l.615-710

Deux voies : upload direct (`POST /api/clipper/sources`, l.615) ou import
YouTube en **deux temps** (`POST /api/clipper/sources/link`, l.642, télécharge
dans `data/clipper/_inbox/` sous le nom choisi par yt-dlp — inconnu avant la
fin du téléchargement — puis `POST /api/clipper/inbox/<name>`, l.684,
**déplace** le fichier vers la source et crée la ligne `clipper_sources`).

La garde de validation d'URL (l.657-663), à connaître au bit près puisqu'elle
protège contre une injection d'options yt-dlp :

```python
url = (request.json or {}).get("url", "").strip()
if any(c.isspace() for c in url):
    return jsonify({"error": "lien YouTube attendu"}), 400
parsed = urlparse(url)
allowed_hosts = {"www.youtube.com", "youtube.com", "youtu.be", "m.youtube.com"}
if parsed.scheme != "https" or parsed.netloc not in allowed_hosts:
    return jsonify({"error": "lien YouTube attendu"}), 400
```

L'URL finit dans un fichier que `fetch_tracks.parse_links` découpe **une
ligne = un argument** passé à yt-dlp : un espace ou un retour à la ligne y
injecterait une option (`--exec`, `-o`, …). D'où le rejet de tout espacement
plutôt qu'un simple `strip()`, et la liste blanche de `netloc` plutôt qu'un
`startswith` — « plus facile à croire fiable qu'il ne l'est », dit la
docstring de `download_clipper_source` (l.644-654).

### 2. Transcription — `clipper.transcribe` l.483

`has_audio` (l.528) est sondée **avant** de charger le modèle Whisper — sans
ça, une source muette fait planter le décodeur audio de faster-whisper avec
une `IndexError` opaque plutôt que de dégrader proprement. faster-whisper
(`WhisperModel(model, device="cpu", compute_type="int8")`) produit des mots
horodatés, langue auto-détectée.

### 3. Proposition — `clipper.propose_moments` l.425

Un appel LLM (via `_call_json`, l.359, détaillé § 4 ci-dessous) demande
`count × OVERSHOOT` (1,5×) candidats à partir d'un digest
compact de la transcription (`transcript_digest`, l.404) : le classement
(étape 6) a besoin de matière à trier, et le recalage (étape 4) en rejette
une partie. Dégrade en `[]` si le LLM échoue — exactement comme
`beatsync.generate_punchlines`.

### 4. Recalage — `clipper.snap_to_speech` l.55

Étend/rétrécit chaque candidat sur des frontières de phrase entières, dans
`[min_dur, max_dur]`. `None` si impossible : un clip de moins vaut mieux qu'un
clip qui commence au milieu d'un mot. Le cœur de la fonction (l.76-88) :

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

### 5. Notation — `clipper.score_moment` l.452

Un appel LLM par candidat retenu, sur le texte **recalé** (pas le brut) :
trois notes 0-100 (hook / flow / value). Dégrade en zéros si le LLM échoue —
le moment tombe en fin de classement, il ne fait pas échouer la source.

### 6. Classement — `clipper.rank_moments` l.125

Garde les `count` meilleurs par score, en écartant les doublons de position
(deux candidats qui décrivent le même moment).

### 7. Rendu — `clipper.render_clip` l.587

Un seul appel ffmpeg par clip : suivi de visage (`track_faces`, l.548, OpenCV)
→ lissage (`smooth_track`, l.179) → crop 9:16 suivi (`crop_expr`, l.211) →
scale 1080×1920 → sous-titres karaoké incrustés (fichier `.ass` généré par
`build_ass`, l.274, supprimé après le rendu). Un clip qui échoue au rendu ne
fait pas perdre les autres (`try/except` dans la boucle de
`clip_source.process`, l.140-145) ; si **tous** les rendus échouent, la
source passe en `failed`.

L'expression de crop (`crop_expr`, l.211-242) illustre pourquoi la zone morte
de `smooth_track` (l.179, détaillée § 3 plus bas) n'est pas cosmétique : sans
elle, cette compilation
produirait un palier ffmpeg par frame plutôt qu'un par seconde de mouvement
réel. L'emboîtement en partant de la fin (l.237-241) :

```python
# Emboîtement en partant de la fin : chaque palier vaut jusqu'au suivant.
expr = str(merged[-1][1])
for (_, x), (next_time, _) in zip(reversed(merged[:-1]),
                                  reversed(merged[1:])):
    expr = f"if(lt(t,{next_time:g}),{x},{expr})"
```

---

## 3. Les fonctions pures, une ligne chacune

| Fonction | l. | Invariant |
|---|---|---|
| `sentences(words) -> list[(int,int)]` | 39 | Une phrase se termine sur ponctuation forte OU un blanc ≥ `SENTENCE_GAP` (0,6 s) — le silence est plus fiable que la ponctuation sur du français transcrit à l'oral |
| `snap_to_speech(start, end, words, min_dur, max_dur) -> (float,float)\|None` | 55 | Recale sur des frontières de phrase entières ; `None` si aucune combinaison ne rentre dans `[min_dur, max_dur]` |
| `moment_score(moment) -> float` | 110 | Moyenne pondérée `WEIGHTS` (hook 0,4 / flow 0,3 / value 0,3) ; une note absente ou `None` vaut 0 — un échec LLM fait tomber le moment en fin de liste, il ne plante pas le classement |
| `rank_moments(moments, count) -> list[dict]` | 125 | Top `count` par score décroissant (tie-break sur `start`, pour la reproductibilité) ; deux moments qui se chevauchent à plus de `OVERLAP_MAX` (50 %) ne sont jamais gardés ensemble ; ne mute pas l'entrée |
| `smooth_track(centers, default, dead_zone) -> list[float]` | 179 | Trous comblés (interpolation, ou valeur connue la plus proche en bord) puis moyenne glissante puis **zone morte** : le cadre ne bouge que si le centre s'écarte de plus de `dead_zone` de sa dernière position retenue |
| `crop_size(src_w, src_h) -> (int,int)` | 204 | Rectangle 9:16 le plus large qui tienne dans la source, dimensions paires (chroma 4:2:0) ; une source déjà plus verticale que 9:16 n'est pas recadrée en largeur |
| `crop_expr(track, sample_fps, crop_w, src_w) -> str` | 211 | Compile la trajectoire en expression ffmpeg `crop=x=…` ; une trajectoire immobile rend un simple nombre (pas d'`eval=frame` payé pour rien) ; un point par seconde, la zone morte a déjà supprimé ce qui bouge plus vite |
| `ass_time(seconds) -> str` | 255 | `H:MM:SS.cc` — **tronque** les centièmes, n'arrondit pas (`round()` ferait passer 1,999 s à `.100`, trois chiffres pour un champ qui en attend deux, et le sous-titre disparaît) |
| `build_ass(words, start, end, y=0.74, size=64) -> str` | 274 | Sous-titres karaoké ASS, temps rebasés sur `start` ; le mot en cours de prononciation passe en rouge, le reste de la ligne reste visible |
| `ffmpeg_path(path) -> str` | 309 | Chemin utilisable **à l'intérieur** d'une chaîne de filtre ffmpeg (`subtitles='<chemin>'`) : `:` échappé pour les lettres de lecteur Windows, apostrophe échappée en dernier (sinon le `\` qu'on introduit pour l'échapper serait lui-même mangé par le remplacement des séparateurs) |
| `transcript_digest(words, max_chars) -> str` | 404 | Transcript compacté en lignes `[mm:ss] phrase`, tronqué à `max_chars` — un modèle local a une fenêtre finie, mieux vaut couper proprement entre deux lignes que de faire couper la réponse au milieu d'un JSON |
| `moment_text(words, start, end) -> str` | 419 | Concatène les mots dont la fenêtre `[start, end]` chevauche celle du mot |

### Les couleurs ASS sont en BGR

Dans `build_ass` (l.245-249) :

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

## 4. Les quatre fonctions d'I/O, et le piège `_call_json`

| Fonction | l. | Rôle |
|---|---|---|
| `transcribe(video_path, model)` | 483 | faster-whisper, mots horodatés ; sonde `has_audio` avant de charger le modèle |
| `track_faces(video_path, start, end)` | 548 | OpenCV, cascade `haarcascade_frontalface_default.xml`, échantillonné à `SAMPLE_FPS` (2 fps) |
| `render_clip(video_path, start, end, out_path, *, words, config)` | 587 | un seul appel ffmpeg : crop suivi + scale + sous-titres |
| `_call_json(system, user, schema, seed, name)` | 359 | un appel LLM rendant du JSON conforme à `schema` |

`_call_json` **n'est pas** `beatsync._call_llm` : celui-ci a le prompt
punchline codé en dur et une signature sans schéma. `_call_json` prend
`system`/`user`/`schema` en paramètres, pour servir aussi bien la proposition
de moments (`_PROPOSE_SCHEMA`) que la notation (`_SCORE_SCHEMA`). Sa
docstring (l.359-365) le dit explicitement :

```python
def _call_json(system: str, user: str, schema: dict, seed: int, name: str) -> dict:
    """Un appel LLM rendant du JSON conforme à `schema`. Isolé pour être mocké.

    N'utilise PAS beatsync._call_llm : celui-ci a le prompt punchline codé en
    dur et une signature sans schéma. On réutilise en revanche son choix de
    backend et son chargement de .env, pour que LLM_BACKEND pilote les deux
    sous-systèmes de la même façon."""
```

Ce qui **est** réutilisé de `beatsync` : `_llm_backend()` (choix du backend
via `LLM_BACKEND`) et `_load_dotenv()`. Et un détail d'API, l.372-374 :

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

## 7. Ce qui est testé

`tests/test_clipper_*.py` + `tests/test_clip_source.py`, un fichier par
sous-domaine :

| Fichier | Couvre |
|---|---|
| `test_clipper_snap.py` | `sentences` et `snap_to_speech` : coupe sur ponctuation/silence, respiration, troncature/extension aux bornes, rejet de ce qui ne rentre pas |
| `test_clipper_rank.py` | `moment_score` (pondération, note absente) et `rank_moments` (tri, chevauchements majoritaires écartés, déterminisme sur ex æquo, non-mutation) |
| `test_clipper_track.py` | `_fill_holes`, `smooth_track` (zone morte, trous de bord), `crop_size`, `crop_expr` (paliers, bornes, valeurs paires) |
| `test_clipper_ass.py` | `ass_time`, `build_ass` (en-tête, un événement par mot, rebasage, surlignage, échappement), `ffmpeg_path` (chemins Windows/POSIX/apostrophe) |
| `test_clipper_llm.py` | `transcript_digest`, `moment_text`, `propose_moments` et `score_moment` **mockés** sur `_call_json` : conversion, entrées malformées ignorées, dégradation si le LLM échoue ou rend une racine non-objet |
| `test_clip_source.py` | `slug_for`, et `process` de bout en bout (mocké) : pipeline complet, cache du transcript, source muette, candidat irrécalable ignoré, échec de rendu isolé, remplacement des clips au relancement, non-perte des clips précédents si le LLM est muet, cache de transcript corrompu ignoré |
| `test_clipper_api.py` | Endpoints `webui.py` : `coerce_clipper` (bornes, rejet non-numérique/modèle inconnu), upload/suppression d'une source, lecture/statut/suppression d'un clip (avec garde anti-traversal), validation de l'URL YouTube (espacement, hôte) |

Ce qui n'est **pas** testé automatiquement, faute de pouvoir le faire tourner
en CI sans dépendances lourdes : les trois fonctions d'I/O `transcribe`
(faster-whisper), `track_faces` (OpenCV + décodage vidéo réel) et
`render_clip` (ffmpeg réel) — seule leur logique pure sous-jacente
(`smooth_track`, `crop_expr`, `build_ass`, `ass_time`, `ffmpeg_path`) est
testée. Idem côté React : `frontend/src/features/clipper/` (`ClipperTab.tsx`,
`SourceDetail.tsx`) n'a pas de test automatisé.

---

## 8. Ce qui n'est pas fait (lot 2), et pourquoi

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
