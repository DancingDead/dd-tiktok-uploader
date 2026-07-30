# 09 — webui.py

> Serveur Flask local · 656 lignes · 2 fonctions pures + 1 factory + 26 endpoints
> ← [08 db.py](08-db.md)

## Ce que fait ce fichier

Exposer une **API JSON** au front React, et servir ce front en prod. Trois blocs
distincts :

```
1. Validation d'entrée      l.26-135    coerce_overrides, coerce_subtitles   ← PUR
2. Jobs en arrière-plan     l.138-169   start_job, _run_job
3. L'application Flask      l.175-646   create_app + 26 endpoints
```

Le serveur ne monte rien lui-même : il **lance des sous-processus**
(`fetch_tracks.py`, `generate_niche.py`) et suit leur log.

---

## Bloc 1 — La validation (l.26-135) · **pur**

Deux fonctions qui ne font que **calculer** : une donnée en entrée, une donnée
corrigée en sortie. Aucun fichier lu, aucune base touchée, aucun réseau.

```python
coerce_overrides({"min_presence": 50})   →   {"min_presence": 1.0}
```

Testées en appels directs, sans démarrer Flask (dans `test_webui_platform.py`,
l.202+).

### Le problème concret

`min_presence` va de **0 à 1** — c'est le score minimal de « personnages à
l'écran » d'une plage de clip. À `50`, aucune plage ne passe le filtre : c'est
impossible d'atteindre 50 sur une échelle qui plafonne à 1.

Sans borne, cette valeur traverse la base, la génération, et **casse trois
minutes plus tard**, au fond de `build_edl`, avec un message qui parle de clips
trop courts. Tu chercherais la cause dans le catalogue, dans le morceau, dans
FFmpeg — partout sauf dans le preset.

C'est ce que dit le commentaire l.34-36 :

> Plages valides : au-delà, le rendu casse **en silence** (`min_presence` trop
> haut = plus aucun clip retenu → montage vide). On borne **à la source**, pour
> tous les clients (UI + API), plutôt que de compter sur des bornes UI.

`coerce_overrides` attrape la valeur au moment de l'enregistrement et la ramène
à `1.0`. L'erreur ne naît jamais.

### Pourquoi pas dans le formulaire ?

On pourrait écrire `<input type="number" min="0" max="1">` dans le React. Le
navigateur refuserait `50`. Mais **le formulaire n'est pas le seul chemin vers
la base** :

```bash
curl -X POST http://localhost:8765/api/presets \
     -d '{"name":"test","overrides":{"min_presence":50}}'
```

… plus un bug du front qui envoie la mauvaise valeur, ou un preset recopié
d'ailleurs. Dans ces cas, l'`<input max="1">` n'a jamais été affiché.

> Le formulaire est une **commodité** pour l'utilisateur.
> Le serveur est la **frontière** du système.

Le formulaire peut répéter la règle — c'est plus agréable, l'erreur s'affiche
tout de suite. Mais c'est le serveur qui décide, parce qu'il est le seul point
de passage obligatoire.

### `coerce_overrides(overrides)` — l.64

Six familles de contrôles :

| Ce qui est vérifié | Comportement |
|---|---|
| 6 clés numériques (`min_presence`, `cut_every`, `buildup`, `strobe_beats`, `grain`, `clip_speed`) | converties en nombre, **bornées** par `OVERRIDE_RANGES` |
| `color_grade` | doit être dans `ALLOWED_COLOR_GRADES` → sinon **`ValueError`** |
| `section` | `drop` ou `calm` → sinon `ValueError` |
| `format` | `vertical` ou `carre` → sinon `ValueError` |
| `accents.glitch` | converti en float s'il n'est ni bool ni nombre |
| `end_scene.{beats,freeze,speed}` | bornés ; `beats` forcé en `int` |
| `speed_ramp.{interpolate,slow_beats}` | bool ; `slow_beats` borné 1–8 |

**Deux régimes distincts** : les nombres sont **clampés** silencieusement (une
valeur hors plage est ramenée dans la plage), les énumérations **lèvent** (une
valeur inconnue est une erreur, pas quelque chose à corriger).

### Le piège `isinstance(True, int)` (l.106-108)

```python
value = speed_ramp["slow_beats"]
# isinstance(True, int) vaut True : sans cette garde, un booléen
# passerait pour un nombre de beats valide.
if not isinstance(value, (int, float)) or isinstance(value, bool):
    value = float(value)
```

En Python, **`bool` hérite de `int`**. Un test naïf `isinstance(value, (int, float))`
laisse passer `True`, qui vaut alors 1 beat. Le second test le rattrape et force
la conversion explicite.

Le même piège est traité dans `end_scene` (l.93) et dans `coerce_subtitles`
(l.128). Trois occurrences du même réflexe.

### Pourquoi `slow_beats` est borné à 8 (l.50-52)

> Longueur du segment ralenti, en beats. 1 = pas de fusion ; au-delà de
> `impact_beats` (8 par défaut) **la fusion avalerait toute la grille de coupe**.

Rappel du mécanisme dans [04 build_edl](04-build-edl.md) :
`merge_boundaries_before_impacts` retire les coupes avant chaque impact. Si
`slow_beats` dépasse l'espacement des impacts, il n'en reste aucune — le montage
devient un seul plan. La borne encode une contrainte du moteur, pas un goût.

### `coerce_subtitles(subtitles)` — l.120

```python
"""Le bloc `subtitles` de la niche est un blob JSON écrit tel quel : une valeur
non numérique ne casserait qu'au rendu FFmpeg, loin de la saisie."""
```

C'est **le** raisonnement à retenir. `subtitles` n'est validé par aucun schéma
SQL — c'est du JSON libre. Un `size: "grand"` traverserait la base, la
génération, et n'exploserait qu'au `drawtext`, dans un sous-processus, sur la
vidéo n° 7 d'un lot de 10.

Trois filets successifs, du plus tôt au plus tard :

```
coerce_subtitles (ici)  → 400 à la saisie              ← le bon endroit
_coerce (beatsync l.1401) → défaut au lieu de planter  ← dernière défense
generate_punchlines       → [] si le LLM échoue        ← ne bloque pas l'usine
```

---

## Bloc 2 — Les jobs (l.138-169)

Un registre en mémoire + un thread par job.

```python
_jobs: dict = {}
_jobs_lock = threading.Lock()
```

### `start_job(name, argv)` — l.161

```python
with _jobs_lock:
    for job in _jobs.values():
        if job["name"] == name and job["status"] == "running":
            raise RuntimeError(f"un job « {name} » tourne déjà")
    job_id = uuid.uuid4().hex[:8]
    _jobs[job_id] = {"name": name, "status": "running", "log": []}
threading.Thread(target=_run_job, args=(job_id, argv), daemon=True).start()
return job_id
```

**Le verrou d'unicité par nom** : deux générations simultanées sur la même niche
écriraient dans le même dossier avec le même horodatage. Le nom du job
(`gen-<slug>`) porte cette exclusion. `webui.py` convertit le `RuntimeError` en
**409 Conflict**.

`daemon=True` : le thread ne retient pas l'arrêt du serveur.

### `_run_job(job_id, argv)` — l.144 : le bug Windows

```python
env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
process = subprocess.Popen(argv, cwd=ROOT, stdout=PIPE, stderr=STDOUT,
                           text=True, encoding="utf-8", errors="replace", env=env)
```

Le commentaire l.145-147 :

> Mode UTF-8 forcé pour le sous-process : sans ça, sur Windows le job plante en
> cp1252 dès qu'un log contient un caractère hors Latin-1 (ex. la flèche « → »
> de beatsync).

Un `print("OK → out.mp4")` faisait tomber toute la génération sur la tour de
prod. **Quatre réglages** sont nécessaires, deux de chaque côté du tuyau :

| | Côté | Rôle |
|---|---|---|
| `PYTHONUTF8=1` | enfant | force le mode UTF-8 de l'interpréteur |
| `PYTHONIOENCODING=utf-8` | enfant | force l'encodage de stdout |
| `encoding="utf-8"` | parent | décodage du flux |
| `errors="replace"` | parent | un octet invalide ne tue pas le job |

### Lecture ligne par ligne (l.153)

```python
for line in process.stdout:
    with _jobs_lock:
        _jobs[job_id]["log"].append(line.rstrip())
```

Le flux est consommé **au fil de l'eau**, pas à la fin. C'est ce qui permet au
front d'afficher le log en direct par polling de `/api/jobs/<id>`.

Le `flush=True` de tous les `print` de `generate_niche.py` sert exactement ça :
sans lui, Python bufferise et le log arriverait d'un bloc à la fin.

### Le statut vient du code retour (l.158)

```python
_jobs[job_id]["status"] = "done" if process.returncode == 0 else "failed"
```

C'est pourquoi `generate_niche.py` fait `sys.exit(...)` quand aucune variante
n'a pu être produite : sans code retour non nul, l'UI annoncerait un succès pour
0 vidéo.

### Ce que ce design ne fait pas

Le registre est **en mémoire**. Redémarrer le serveur perd l'historique des
jobs, et un job en cours devient orphelin (le sous-processus continue, mais plus
personne ne lit son log).

Acceptable pour un outil local mono-utilisateur — le remplacer par une table
SQLite serait de la sur-ingénierie tant qu'on ne redémarre pas en pleine
génération.

---

## Bloc 3 — `create_app(root=None)` (l.175)

### Pourquoi une factory injectable

```python
def create_app(root: Path | None = None):
    root = root or ROOT
    paths = {"db": root / "platform.db", "data": root / "data", ...}
```

**Tous** les chemins dérivent de `root`. Conséquence directe : un test crée une
app sur un `tmp_path`, appelle le `test_client`, et ne touche jamais à ta vraie
base ni à tes vrais fichiers.

Sans cette injection, tester `webui.py` voudrait dire écrire dans
`platform.db`.

### La clé de session (l.193)

```python
secret_file = paths["data"] / "secret_key"
if not secret_file.is_file():
    secret_file.write_text(pysecrets.token_hex(32))
    secret_file.chmod(0o600)
app.secret_key = secret_file.read_text()
```

**Persistée**, pas régénérée au démarrage — sinon chaque redémarrage
déconnecterait tout le monde. `0600` = lisible par le seul propriétaire.

### `require_login` — l.205

```python
@app.before_request
def require_login():
    if not request.path.startswith("/api") or request.path == "/api/login":
        return None
    if "member" not in session:
        return jsonify({"error": "non connecté"}), 401
```

Un seul point de contrôle pour **toute** l'API. Les 25 endpoints n'ont aucun
décorateur d'auth : impossible d'en oublier un.

La logique est **en négatif** — tout `/api/*` est protégé sauf `/api/login`.
Ajouter un endpoint le protège automatiquement. L'inverse (une liste blanche)
laisserait passer les oublis.

Les routes non-`/api` (le front) ne sont pas protégées : ce sont des fichiers
statiques.

### `serve_spa(path)` — l.229 : trois niveaux de repli

```python
if path and not path.startswith("api"):
    candidate = (dist / path)
    if candidate.is_file():
        return send_from_directory(dist, path)      # 1. fichier du build
if path.startswith("api"):
    abort(404)                                      #    404 franc sur /api inconnu
if (dist / "index.html").is_file():
    return send_from_directory(dist, "index.html")  # 2. routing SPA
return render_template("index.html")                # 3. ancienne UI Jinja
```

| Cas | Réponse |
|---|---|
| `/assets/index-abc.js` existe | le fichier |
| `/niches/12` (route React) | `index.html`, React route côté client |
| `/api/inconnu` | **404**, jamais `index.html` |
| pas de build (`npm run build` pas lancé) | `templates/index.html`, l'ancienne UI vanilla |

Le `abort(404)` sur `/api` est important : sans lui, un endpoint mal orthographié
renverrait du HTML avec un code 200, et le front planterait sur un `JSON.parse`
incompréhensible.

---

## Les endpoints

### État global

| Route | Rôle |
|---|---|
| `GET /api/state` | **tout l'état en un appel** : membre, niches (+ leurs vidéos), presets, catalogues, liens, réglages, jobs |

`/api/state` (l.255) évite au front d'orchestrer 6 requêtes au chargement. Il
ajoute au passage `exists` sur chaque vidéo (l.300) — le fichier est-il toujours
là ? — pour que la bibliothèque n'affiche pas de lecteur mort.

### Authentification

| Route | |
|---|---|
| `POST /api/login` | vérifie, pose `session["member"]` |
| `POST /api/logout` | |

### Catalogue partagé — deux familles symétriques

| Sons | Clips | Rôle |
|---|---|---|
| `POST /api/tracks` | `POST /api/clips` | upload |
| `GET /api/tracks/<name>` | `GET /api/clips/<name>` | aperçu (écoute / visionnage) |
| `DELETE /api/tracks/<name>` | `DELETE /api/clips/<name>` | suppression |
| `POST /api/links` | `POST /api/clip-links` | enregistre la liste de liens |
| `POST /api/download` | `POST /api/clips/download` | lance `fetch_tracks.py` |
| | `GET /api/link-info` | titre + miniature YouTube |

Les deux familles partagent `_delete_asset` et `_serve_asset`, paramétrées par
dossier et extensions. Une seule implémentation des gardes de sécurité.

### Niches, presets, vidéos

| Route | |
|---|---|
| `POST/PATCH/DELETE /api/niches[/<id>]` | CRUD |
| `POST /api/niches/<id>/generate` | **lance le lot** |
| `POST/PATCH/DELETE /api/presets[/<id>]` | CRUD |
| `GET /api/videos/<id>` | lecture (`?dl=1` pour télécharger) |
| `GET /api/videos/<id>/poster` | vignette |
| `POST /api/videos/<id>/status` | valider / rejeter |
| `DELETE /api/videos/<id>` | ligne **+ fichier** |
| `GET /api/jobs/<id>` | statut + log |

---

## La sécurité des fichiers — le motif répété 4 fois

C'est le sujet le plus sensible du fichier : le serveur manipule des chemins qui
viennent du client.

### Sur les uploads (l.352, l.362)

```python
name = Path(file.filename).name  # pas de traversée de chemin
if Path(name).suffix.lower() not in AUDIO_EXTENSIONS:
    return jsonify({"error": ...}), 400
```

`Path("../../etc/passwd").name` vaut `"passwd"`. Le `.name` **écrase** toute
composante de répertoire. Puis liste blanche d'extensions.

### Sur la lecture et la suppression (l.373-381, l.409-415)

```python
safe = Path(name).name          # neutralise toute traversée
base = paths[dir_key].resolve()
target = (base / safe).resolve()
if target.parent != base:
    return jsonify({"error": "chemin invalide"}), 400
```

Trois barrières empilées : `.name`, puis `.resolve()` (qui suit les liens
symboliques), puis **`target.parent != base`** — le fichier doit être
*directement* sous le dossier catalogue, pas dans un sous-dossier.

### Sur les vidéos (l.482, l.530)

```python
path = (paths["data"].parent / row["file"]).resolve()
if not path.is_file() or paths["data"].resolve() not in path.parents:
    return jsonify({"error": "fichier introuvable"}), 404
```

Variante : ici on vérifie que `data/` est **quelque part** dans les parents (les
vidéos sont dans `data/niches/<slug>/videos/`). Même principe, granularité
adaptée.

Le chemin vient de la base, pas du client — mais on vérifie quand même. Une
ligne corrompue ne doit pas donner accès au disque.

---

## La cohérence catalogue ↔ niches

`_delete_asset` (l.369) fait **deux** choses :

```python
target.unlink()                                    # 1. efface le fichier
ref = prefix + safe
field = "tracks" if prefix == "tracks/" else "clips"
for niche in dbmod.list_niches(conn):
    if ref in niche[field]:
        dbmod.update_niche(conn, niche["id"],
                           **{field: [p for p in niche[field] if p != ref]})
```

Supprimer un asset du catalogue le retire des sélections de **toutes** les
niches. Sans ça, une niche garderait une référence morte et la génération
échouerait plus tard, loin de la cause.

C'est le seul endroit qui « requête » les colonnes JSON — par un parcours
complet. À l'échelle du projet (quelques dizaines de niches), c'est largement
suffisant.

**La distinction à garder en tête** : « retirer de la niche » (PATCH) ne
supprime pas le fichier ; « supprimer du catalogue » (DELETE) fait les deux.

---

## `/api/niches/<id>/generate` — l.450 : le point de jonction

```python
if not niche["tracks"]:
    return jsonify({"error": "aucun son sélectionné — ajoute au moins un morceau…"}), 400
if not niche["clips"]:
    return jsonify({"error": "aucun clip sélectionné — ajoute au moins un extrait…"}), 400
count = max(1, int((request.json or {}).get("count", niche["cadence"] or 1)))
job_id = start_job(f"gen-{niche['slug']}",
                   [sys.executable, "generate_niche.py", str(niche_id),
                    str(count), str(paths["data"].parent)])
```

Trois choses à noter :

**Les messages d'erreur nomment le remède**, pas seulement le problème — ils
citent le libellé exact de la carte à remplir dans l'UI.

**`sys.executable`**, pas `"python"` : le sous-processus tourne dans le même venv
que le serveur. Sinon il utiliserait le Python système, sans librosa ni numpy.

**Le `root` passé explicitement** (3ᵉ argument), et le commentaire l.461-463
explique pourquoi :

> Sans ça, le job de fond ouvre `ROOT/platform.db` (via `cwd=ROOT`) et croit la
> niche vide quand `create_app` est injecté avec un autre root (tests,
> multi-instances).

Un bug d'injection de dépendance : la factory est testable, mais le
sous-processus qu'elle lance ne l'était pas — il retombait sur le `ROOT` du
module.

---

## `/api/videos/<id>/poster` — l.488

```python
poster = cache / f"{video_id}.jpg"
if not poster.is_file() or poster.stat().st_mtime < video.stat().st_mtime:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                    "-frames:v", "1", "-q:v", "4", str(poster)], check=True, ...)
```

Extraction paresseuse de la frame 0, mise en cache, **invalidée par mtime** —
même stratégie que le cache de scan de `beatsync.py`.

```python
except Exception:
    return jsonify({"error": "poster indisponible"}), 404
```

Un `except Exception` large : une vignette absente n'est pas une erreur qui
mérite un 500. La bibliothèque affiche juste un placeholder.

---

## `/api/link-info` — l.330 : le seul appel sortant

```python
"""On ne contacte QUE youtube.com ; l'URL de l'utilisateur y est passée en
paramètre (pas de requête sortante vers une URL arbitraire)."""
oembed = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
```

Prévention **SSRF**. Un endpoint naïf qui ferait `urlopen(request.args["url"])`
laisserait un client interroger `http://localhost:8080/admin` ou un service
interne via le serveur.

Ici l'URL du client n'est jamais visitée : elle est **encodée en paramètre**
(`quote(url, safe="")` échappe tout, y compris les `/` et `:`) d'une requête vers
un domaine en dur.

Dégrade en `nulls` si indisponible — playlist, vidéo privée, réseau coupé.

---

## Le motif `try/finally` sur les connexions

```python
conn = get_conn()
try:
    ...
finally:
    conn.close()
```

Répété dans tous les endpoints qui touchent la base. **Une connexion par
requête**, fermée quoi qu'il arrive — y compris sur un `return` anticipé ou une
exception.

`get_conn()` appelle `dbmod.connect()`, qui rejoue le schéma et la migration à
chaque fois. C'est idempotent et bon marché sur SQLite ; le bénéfice est qu'une
base est toujours à jour, même après une mise à jour du code sans redémarrage.

---

## Index des fonctions

| Ligne | Fonction | | |
|---|---|---|---|
| 64 | `coerce_overrides(overrides)` | bornes + énumérations des presets | **pure** |
| 120 | `coerce_subtitles(subtitles)` | bornes du placement de texte | **pure** |
| 144 | `_run_job(job_id, argv)` | sous-processus + capture du log (UTF-8 forcé) | thread |
| 161 | `start_job(name, argv)` | unicité par nom → 409 | thread |
| 175 | `create_app(root=None)` | **la factory**, tout dérive de `root` | I/O |
| 202 | ↳ `get_conn()` | une connexion par requête | I/O |
| 206 | ↳ `require_login()` | `before_request`, protège tout `/api/*` | |
| 229 | ↳ `serve_spa(path)` | build React → SPA → ancienne UI | I/O |
| 369 | ↳ `_delete_asset(dir_key, prefix, exts, name)` | fichier **+** références en base | I/O |
| 403 | ↳ `_serve_asset(dir_key, exts, name)` | aperçu, gère les requêtes Range | I/O |
| 599 | ↳ `_niche_or_404(niche_id)` | | I/O |
| 649 | `main()` | 127.0.0.1:8765, `debug=False` | I/O |

\+ 26 endpoints imbriqués dans `create_app`.

**2 fonctions pures** — mais ce sont celles qui portent toute la validation, et
elles sont testables sans serveur.

---

## Ce qui est testé

| Fichier | Couvre |
|---|---|
| `test_webui_pure.py` | `merge_settings` — fusion des dicts imbriqués, clés inconnues ignorées |
| `test_webui_auth.py` | le 401 sur `/api/*`, le cycle login/logout, l'index servi sans login |
| `test_webui_platform.py` | **le gros du fichier** : les endpoints via `test_client`, **et** les `coerce_*` en appels directs |

`test_webui_platform.py` mélange deux natures de tests, ce qui surprend au
premier abord :

- **l.21-200** — endpoints via `test_client` sur un `tmp_path` : CRUD niches et
  presets, catalogue partagé (upload, images acceptées, formats refusés),
  suppression d'asset qui nettoie les sélections, `generate` qui passe bien le
  root, les deux 400 « aucun son / aucun clip », suppression de vidéo qui efface
  le fichier.
- **l.202+** — `coerce_overrides` et `coerce_subtitles` appelés **directement**,
  sans Flask : clamps numériques, glitch bool vs nombre, `color_grade` /
  `section` / `format` inconnus, bornes d'`end_scene`.

C'est `create_app(root=tmp_path)` qui rend la première moitié possible.

---

## Note sur `CLAUDE.md`

La section `webui.py` de `CLAUDE.md` décrit encore l'**ancienne UI vanilla** :
`esc()` sur les champs rendus via `innerHTML`, toasts et modales maison, icônes
SVG. Ce code existe toujours dans `templates/index.html` (1011 lignes) mais ne
sert plus que de repli quand `frontend/dist/` est absent.

Le front réel est le React sous `frontend/src/`. La défense XSS y est assurée par
React lui-même (échappement automatique du JSX) ; **la coercion serveur décrite
ici, elle, reste d'actualité** et vaut pour les deux fronts.
