# 10 — clipper.py / clip_source.py

> `clipper.py` (908 lignes, logique pure + I/O) · `clip_source.py` (223
> lignes, orchestrateur) · endpoints `/api/clipper/*` dans `webui.py` l.670-901
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
1. Import de la source          webui.py l.670-785 (upload / lien YouTube → inbox → promotion)
2. Transcription                clipper.transcribe            l.715 (I/O, mis en cache)
3. Proposition de moments       clipper.propose_moments       l.618 (I/O, LLM)
4. Recalage sur les phrases     clipper.snap_to_speech        l.61  (pur)
5. Notation                     clipper.score_moment          l.680 (I/O, LLM)
6. Classement                   clipper.rank_moments          l.131 (pur)
7. Rendu                        clipper.render_clip           l.854 (I/O, ffmpeg)
```

`clip_source.process(conn, root, source_id, config, log)` (l.52) enchaîne
2 → 7 pour une source, en écrivant le statut en base à chaque étape
(`transcribing`/`analyzing`/`rendering`/`done`/`failed`) pour que l'UI suive
l'avancement par polling (`GET /api/jobs/<job_id>`, mécanisme générique déjà
utilisé par `generate_niche.py`).

### 1. Import — `webui.py` l.670-785

Deux voies : upload direct (`POST /api/clipper/sources`, l.670) ou import
YouTube en **deux temps** (`POST /api/clipper/sources/link`, l.696, télécharge
dans `data/clipper/_inbox/` sous le nom choisi par yt-dlp — inconnu avant la
fin du téléchargement — puis `POST /api/clipper/inbox/<name>`, l.748,
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
`DELETE /api/clipper/sources/<id>` (l.809) efface le dossier **avant** la
ligne, refuse en 409 tant qu'un job tourne (`transcribing`/`analyzing`/
`rendering`) et rend 409 plutôt que 500 si `rmtree` échoue (handle ouvert sous
Windows). Sans cet ensemble, un effacement raté laissait un dossier orphelin
dont un réupload du même nom récupérait le slug — et son `transcript.json` :
« Transcript en cache », puis des clips découpés aux timestamps d'une autre
vidéo, en silence.

La garde de validation d'URL (l.711-725), à connaître au bit près puisqu'elle
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
docstring de `download_clipper_source` (l.698-708).

### 2. Transcription — `clipper.transcribe` l.715

`has_audio` (l.760) est sondée **avant** de charger le modèle Whisper — sans
ça, une source muette fait planter le décodeur audio de faster-whisper avec
une `IndexError` opaque plutôt que de dégrader proprement. faster-whisper
(`WhisperModel(model, device="cpu", compute_type="int8")`) produit des mots
horodatés, langue auto-détectée.

### 3. Proposition — `clipper.propose_moments` l.618

Le transcript est d'abord découpé en **fenêtres** qui tiennent dans le contexte
du modèle (`transcript_windows` l.585, `digest_chars` caractères, coupé sur des
phrases entières). Un appel LLM (via `_call_json`, l.525, détaillé § 4
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
(`_propose_system` l.378) et rappelées dans le message utilisateur de chaque
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

### 4. Recalage — `clipper.snap_to_speech` l.61

Étend/rétrécit chaque candidat sur des frontières de phrase entières, dans
`[min_dur, max_dur]`. `None` si impossible : un clip de moins vaut mieux qu'un
clip qui commence au milieu d'un mot. Le cœur de la fonction (l.82-94) :

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

### 5. Notation — `clipper.score_moment` l.680

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

### 6. Classement — `clipper.rank_moments` l.131

Garde les `count` meilleurs par score, en écartant les doublons de position
(deux candidats qui décrivent le même moment).

### 7. Rendu — `clipper.render_clip` l.854

Un seul appel ffmpeg par clip : suivi de visage (`track_faces`, l.815, OpenCV)
→ lissage (`smooth_track`, l.189) → crop 9:16 suivi (`crop_expr`, l.239) →
scale 1080×1920 → sous-titres karaoké incrustés (fichier `.ass` généré par
`build_ass`, l.312, supprimé après le rendu, police **embarquée** passée par
`fontsdir=` — voir § 3). Un clip qui échoue au rendu ne
fait pas perdre les autres (`try/except` dans la boucle de
`clip_source.process`, l.174-179) ; si **tous** les rendus échouent, la
source passe en `failed`.

L'expression de crop (`crop_expr`, l.239-274) illustre pourquoi la zone morte
de `smooth_track` (l.189, détaillée § 3 plus bas) n'est pas cosmétique : sans
elle, cette compilation
produirait un point ffmpeg par frame plutôt qu'un par seconde de mouvement
réel. L'emboîtement en partant de la fin, chaque segment **interpolant**
jusqu'au point suivant :

```python
# Emboîtement en partant de la fin : chaque segment interpole jusqu'au point
# suivant ; après le dernier point, le cadre tient sa valeur.
expr = str(merged[-1][1])
for (time, x), (next_time, next_x) in zip(reversed(merged[:-1]),
                                          reversed(merged[1:])):
    expr = (f"if(lt(t,{next_time:g}),"
            f"{_ramp(time, x, next_time, next_x)},{expr})")
```

Des paliers secs ne lisseraient rien : le cadre resterait figé le temps que la
zone morte cède, puis bondirait de plusieurs pour cent de la largeur en une
frame — un saut de cadrage, pas un panoramique. `_ramp` (l.224) émet donc
`2*floor((X0+pente*(t-T0))/2)`, le `2*floor(…/2)` gardant `x` sur la grille
paire qu'exige le chroma yuv420p.

Et la zone morte se compte en pixels **source** : `render_clip` passe
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
forcément la ffmpeg du Mac de développement. `crop_supports_eval` (l.778) lit
la sortie de `ffmpeg -hide_banner -h filter=crop` et cherche une ligne
d'option dont le **nom** est `eval` (pas la sous-chaîne « eval », que les
descriptions contiennent : « when to evaluate »). Le résultat est mémorisé au
niveau du module — c'est un sous-processus, on ne le paie pas une fois par
clip. Une sonde en échec (ffmpeg absent, sortie inattendue) vaut « option
absente » : c'est le comportement des versions récentes, et il ne casse rien
sur elles. `render_clip` n'ajoute `:eval=frame` que si la sonde dit oui **et**
que l'expression dépend du temps.

---

## 3. Les fonctions pures, une ligne chacune

| Fonction | l. | Invariant |
|---|---|---|
| `sentences(words) -> list[(int,int)]` | 45 | Une phrase se termine sur ponctuation forte OU un blanc ≥ `SENTENCE_GAP` (0,6 s) — le silence est plus fiable que la ponctuation sur du français transcrit à l'oral |
| `snap_to_speech(start, end, words, min_dur, max_dur) -> (float,float)\|None` | 61 | Recale sur des frontières de phrase entières ; `None` si aucune combinaison ne rentre dans `[min_dur, max_dur]` |
| `moment_score(moment) -> float` | 116 | Moyenne pondérée `WEIGHTS` (hook 0,4 / flow 0,3 / value 0,3) ; une note absente ou `None` vaut 0 — un échec LLM fait tomber le moment en fin de liste, il ne plante pas le classement |
| `rank_moments(moments, count) -> list[dict]` | 131 | Top `count` par score décroissant (tie-break sur `start`, pour la reproductibilité) ; deux moments qui se chevauchent à plus de `OVERLAP_MAX` (50 %) ne sont jamais gardés ensemble ; ne mute pas l'entrée |
| `smooth_track(centers, default, dead_zone) -> list[float]` | 189 | Trous comblés (interpolation, ou valeur connue la plus proche en bord) puis moyenne glissante puis **zone morte** : le cadre ne bouge que si le centre s'écarte de plus de `dead_zone` de sa dernière position retenue |
| `crop_size(src_w, src_h) -> (int,int)` | 214 | Rectangle 9:16 le plus large qui tienne dans la source, **les deux** dimensions paires (chroma 4:2:0 : une hauteur source impaire ferait échouer l'encodage yuv420p) ; une source déjà plus verticale que 9:16 n'est pas recadrée en largeur |
| `crop_expr(track, sample_fps, crop_w, src_w) -> str` | 239 | Compile la trajectoire en expression ffmpeg `crop=x=…`, en **interpolant** linéairement d'un point au suivant (un palier sec téléporterait le cadre) ; une trajectoire immobile rend un simple nombre (rien à réévaluer par frame, cf. § 4 bis) ; un point par seconde, la zone morte a déjà supprimé ce qui bouge plus vite |
| `ass_time(seconds) -> str` | 293 | `H:MM:SS.cc` — **tronque** les centièmes, n'arrondit pas (`round()` ferait passer 1,999 s à `.100`, trois chiffres pour un champ qui en attend deux, et le sous-titre disparaît) |
| `build_ass(words, start, end, y=0.74, size=64) -> str` | 312 | Sous-titres karaoké ASS, temps rebasés sur `start` ; le mot en cours de prononciation passe en rouge, le reste de la ligne reste visible ; chaque `Dialogue` tient jusqu'au **mot suivant** du groupe (le borner sur la fin du mot éteint la ligne à chaque respiration, et le français parlé en est plein) ; le style nomme `Anton`, la police OFL **embarquée** que `beatsync` associe déjà au nom logique « impact » — « Impact » serait résolu par fontconfig et libass lui substituerait silencieusement une sans-serif |
| `ffmpeg_path(path) -> str` | 361 | Chemin utilisable **à l'intérieur** d'une chaîne de filtre ffmpeg (`subtitles='<chemin>'`) : `:` échappé pour les lettres de lecteur Windows, apostrophe échappée en dernier (sinon le `\` qu'on introduit pour l'échapper serait lui-même mangé par le remplacement des séparateurs) |
| `transcript_digest(words, max_chars) -> str` | 563 | Transcript compacté en lignes `[mm:ss] phrase`, tronqué à `max_chars` — un modèle local a une fenêtre finie, mieux vaut couper proprement entre deux lignes que de faire couper la réponse au milieu d'un JSON. **La première phrase sort toujours**, même seule plus longue que `max_chars` : c'est la fenêtre « phrase géante » de `transcript_windows`, et sans cette exception son digest était vide (voir § 3). `DIGEST_MAX_CHARS` (40 000) n'est plus qu'un plafond de sécurité pour l'usage direct : le découpage réel passe par `transcript_windows` |
| `transcript_windows(words, max_chars) -> list[list[dict]]` | 585 | Fenêtres consécutives dont le digest tient dans `max_chars`, **sans jamais couper une phrase** ; une phrase seule plus longue forme sa propre fenêtre (la jeter perdrait du transcript) ; la concaténation des fenêtres redonne la liste d'origine |
| `moment_text(words, start, end) -> str` | 612 | Concatène les mots dont la fenêtre `[start, end]` chevauche celle du mot |

### Les couleurs ASS sont en BGR

Au-dessus de `build_ass` (l.277-280) :

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
| `transcribe(video_path, model)` | 715 | faster-whisper, mots horodatés ; sonde `has_audio` avant de charger le modèle |
| `track_faces(video_path, start, end)` | 815 | OpenCV, cascade `haarcascade_frontalface_default.xml`, échantillonné à `SAMPLE_FPS` (2 fps) |
| `render_clip(video_path, start, end, out_path, *, words, config)` | 854 | un seul appel ffmpeg : crop suivi + scale + sous-titres |
| `_call_json(system, user, schema, seed, name)` | 525 | un appel LLM rendant du JSON conforme à `schema` |

`_call_json` **n'est pas** `beatsync._call_llm` : celui-ci a le prompt
punchline codé en dur et une signature sans schéma. `_call_json` prend
`system`/`user`/`schema` en paramètres, pour servir aussi bien la proposition
de moments (`_PROPOSE_SCHEMA`) que la notation (`_SCORE_SCHEMA`). Sa
docstring (l.525-533) le dit explicitement :

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
d'API, l.421-423 :

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
comme pour beatsync). C'est la **seule** définition de ces défauts :
`beatsync.DEFAULT_CONFIG["clipper"]` en est une copie
(`dict(CLIPPER_DEFAULTS)`), pour que le CLI et l'interface ne puissent pas
diverger en silence. Ils s'éditent dans l'onglet **Réglages**, carte
« Clipper » :

| Clé | Défaut | Rôle |
|---|---|---|
| `whisper_model` | `"small"` | taille du modèle faster-whisper |
| `clip_count` | `8` | nombre de shorts gardés par source |
| `min_dur` | `15.0` | s, en dessous un extrait n'a pas d'histoire |
| `max_dur` | `60.0` | s, au-delà ce n'est plus un short |
| `digest_chars` | `6000` | caractères de transcript envoyés en un appel LLM (≈ 2600 tokens, à ~2,3 caractères par token) |

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

`tests/test_clipper_*.py` + `tests/test_clip_source.py`, un fichier par
sous-domaine :

| Fichier | Couvre |
|---|---|
| `test_clipper_snap.py` | `sentences` et `snap_to_speech` : coupe sur ponctuation/silence, respiration, troncature/extension aux bornes, rejet de ce qui ne rentre pas |
| `test_clipper_rank.py` | `moment_score` (pondération, note absente) et `rank_moments` (tri, chevauchements majoritaires écartés, déterminisme sur ex æquo, non-mutation) |
| `test_clipper_track.py` | `_fill_holes`, `smooth_track` (zone morte, trous de bord), `crop_size` (dont hauteur impaire), `crop_expr` (interpolation, continuité à mi-chemin, bornes, valeurs paires) |
| `test_clipper_ass.py` | `ass_time`, `build_ass` (en-tête, un événement par mot, rebasage, surlignage, échappement, absence de trou entre deux mots, police embarquée), `ffmpeg_path` (chemins Windows/POSIX/apostrophe) |
| `test_clipper_llm.py` | `transcript_digest` (dont le digest **non vide** d'une fenêtre « phrase géante »), `transcript_windows` (découpe sur les phrases, fenêtre unique, phrase géante isolée, liste vide, aucun mot perdu), `moment_text`, `propose_moments` et `score_moment` **mockés** sur `_call_json` : conversion, entrées malformées ignorées, `moments` qui n'est pas une liste, bornes de durée réglées rappelées dans le prompt, boucle sur les fenêtres et seeds décalées, une fenêtre en échec ne perd pas les autres, dégradation si le LLM échoue ou rend une racine non-objet **avec journalisation de la cause** ; le repli `LLM_FALLBACK` de `_call_json` ; la remontée des erreurs LM Studio (corps d'un HTTP 400, `{"error": …}` sur un 200, réponse sans `choices`, JSON illisible dont on montre un extrait) via un `urlopen` monkeypatché ; et `crop_supports_eval` (aide ffmpeg 7 vs 8, sonde en échec, mise en cache) sur un `subprocess.run` monkeypatché |
| `test_clip_source.py` | `slug_for`, et `process` de bout en bout (mocké) : pipeline complet, cache du transcript, source muette, candidat irrécalable ignoré, échec de rendu isolé, remplacement des clips au relancement, non-perte des clips précédents si le LLM est muet, cache de transcript corrompu ignoré, budget de notation respecté **et annoncé**, progression `[i/n]` journalisée, bornes de durée transmises au LLM |
| `test_clipper_api.py` | Endpoints `webui.py` : `coerce_clipper` (bornes, rejet non-numérique/modèle inconnu, `min_dur > max_dur`), upload/suppression d'une source (409 pendant un job, échec de `rmtree`, slug d'un dossier orphelin non réattribué, durée sondée), promotion refusée sans piste audio, lecture/statut/suppression d'un clip (avec garde anti-traversal), projection sans chemins disque dans `/api/state`, validation de l'URL YouTube (espacement, hôte, casse, type) |

Ce qui n'est **pas** testé automatiquement, faute de pouvoir le faire tourner
en CI sans dépendances lourdes : les trois fonctions d'I/O `transcribe`
(faster-whisper), `track_faces` (OpenCV + décodage vidéo réel) et
`render_clip` (ffmpeg réel) — seule leur logique pure sous-jacente
(`smooth_track`, `crop_expr`, `build_ass`, `ass_time`, `ffmpeg_path`) et la
sonde `crop_supports_eval` (sur une aide ffmpeg factice) sont testées. La
chaîne ffmpeg complète a été vérifiée à la main sur une source réelle (AV1
1920×1080 sonore) après la correction `eval` : sortie 1080×1920 portant bien
une piste vidéo et une piste audio. Idem côté React : `frontend/src/features/clipper/` (`ClipperTab.tsx`,
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
