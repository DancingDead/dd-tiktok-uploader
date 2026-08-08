"""webui — interface locale de gestion de l'usine à vidéos.

Onglets : niches, presets, tracks, liens YouTube, plan & file, réglages.
Local uniquement (127.0.0.1) : manipule fichiers et secrets du projet.

    uv run python webui.py    puis  http://127.0.0.1:8765
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from beatsync import DEFAULT_CONFIG, load_settings, merge_settings  # noqa: F401 (réexport)

ROOT = Path(__file__).parent
TRACKS_DIR = ROOT / "tracks"
LINKS_PATH = ROOT / "links.txt"
SETTINGS_PATH = ROOT / "settings.json"

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aiff"}
# Réglages exposés dans l'onglet Paramètres (sous-ensemble sûr de DEFAULT_CONFIG)
EDITABLE_SETTINGS = [
    "effects", "accents", "delogo", "chrono", "min_presence",
    "buildup", "strobe_beats", "cut_mode", "cut_every", "clipper",
]
# Clés d'overrides de preset qui doivent être numériques (défense XSS : jamais de HTML stocké)
NUMERIC_OVERRIDE_KEYS = ("min_presence", "cut_every", "buildup", "strobe_beats",
                         "grain", "clip_speed", "blackout_beats", "blackout_lead")
# Plages valides : au-delà, le rendu casse en silence (min_presence trop haut =
# plus aucun clip retenu → montage vide). On borne à la source, pour tous les
# clients (UI + API), plutôt que de compter sur des bornes UI.
OVERRIDE_RANGES = {
    "min_presence": (0.0, 1.0),
    "cut_every": (1, 16),
    "buildup": (0.0, 30.0),
    "strobe_beats": (0, 64),
    "grain": (0.0, 1.0),
    "clip_speed": (0.5, 1.5),
    # En dessous de 0,25 beat le clignotement dépasse 4 Hz ; au-dessus de
    # 2 beats ce ne sont plus des éclairs.
    "blackout_beats": (0.25, 2.0),
    # Beats strobés avant chaque impact, quand le morceau n'a pas de drop.
    # 0 = repli désactivé ; au-delà d'`impact_beats` (8) les zones se
    # rejoindraient et le montage entier passerait sous strobe.
    "blackout_lead": (0, 8),
}
ALLOWED_COLOR_GRADES = ("neutre", "chaud", "froid", "delave")
ALLOWED_FORMATS = ("vertical", "carre", "horizontal")
# Bornes des champs de la scène de fin : au-delà, la scène avale la vidéo ou
# le figé dépasse le segment.
END_SCENE_RANGES = {"beats": (2, 32), "freeze": (0.0, 3.0), "speed": (0.5, 1.5)}
# Longueur du segment ralenti, en beats. 1 = pas de fusion ; au-delà de
# `impact_beats` (8 par défaut) la fusion avalerait toute la grille de coupe.
SLOW_BEATS_RANGE = (1, 8)
# Bornes des réglages du clipper. clip_count au-delà de 30 sature un modèle
# local ; un clip sous 3 s n'a pas d'histoire, au-delà de 180 s ce n'est plus un
# short. Modèles Whisper : ceux que faster-whisper sait résoudre.
CLIPPER_RANGES = {"clip_count": (1, 30), "min_dur": (3.0, 180.0),
                  "max_dur": (3.0, 180.0),
                  # Sous 1000 caractères une fenêtre ne porte plus de contexte
                  # exploitable ; au-delà de 60 000 aucun modèle local courant
                  # ne suit, et la fenêtre échouerait à chaque appel.
                  "digest_chars": (1000, 60000)}
CLIPPER_INT_KEYS = ("clip_count", "digest_chars")
ALLOWED_WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3")
# Statuts qui signalent un job en cours sur la source : on refuse de la
# supprimer tant qu'il tourne (il recréerait les dossiers derrière nous).
BUSY_CLIPPER_STATUSES = ("transcribing", "analyzing", "rendering")
# Champs exposés par /api/state : `path` et `file` en sont ABSENTS à dessein
# (l'arborescence disque de la machine n'a rien à faire dans une réponse HTTP),
# comme pour les vidéos des niches.
CLIPPER_SOURCE_FIELDS = ("id", "title", "slug", "duration", "status", "error",
                         "created_at")
CLIPPER_CLIP_FIELDS = ("id", "source_id", "start", "end", "title", "hook",
                       "flow", "value", "score", "why", "status")
# Types MIME explicites pour l'aperçu d'assets (send_file devine mal .flac/.aiff).
ASSET_MIMETYPES = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac",
    ".m4a": "audio/mp4", ".ogg": "audio/ogg", ".aiff": "audio/aiff",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
}
ALLOWED_SECTIONS = ("drop", "calm")


def coerce_overrides(overrides: dict) -> dict:
    """Force les clés numériques connues en nombres, coerce l'intensité de glitch
    et valide color_grade. ValueError/TypeError si non convertible/inconnu."""
    coerced = dict(overrides)
    for key in NUMERIC_OVERRIDE_KEYS:
        if key in coerced:
            if not isinstance(coerced[key], (int, float)):
                coerced[key] = float(coerced[key])
            lo, hi = OVERRIDE_RANGES[key]
            coerced[key] = max(lo, min(hi, coerced[key]))
    if "color_grade" in coerced and coerced["color_grade"] not in ALLOWED_COLOR_GRADES:
        raise ValueError(f"color_grade inconnu : {coerced['color_grade']!r}")
    if "section" in coerced and coerced["section"] not in ALLOWED_SECTIONS:
        raise ValueError(f"section inconnue : {coerced['section']!r}")
    accents = coerced.get("accents")
    if isinstance(accents, dict) and "glitch" in accents \
            and not isinstance(accents["glitch"], bool) \
            and not isinstance(accents["glitch"], (int, float)):
        accents = dict(accents)
        accents["glitch"] = float(accents["glitch"])
        coerced["accents"] = accents
    if "format" in coerced and coerced["format"] not in ALLOWED_FORMATS:
        raise ValueError(f"format inconnu : {coerced['format']!r}")
    end_scene = coerced.get("end_scene")
    if isinstance(end_scene, dict):
        end_scene = dict(end_scene)
        for key, (lo, hi) in END_SCENE_RANGES.items():
            if key in end_scene:
                value = end_scene[key]
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    value = float(value)
                end_scene[key] = max(lo, min(hi, value))
        if "beats" in end_scene:
            end_scene["beats"] = int(end_scene["beats"])
        coerced["end_scene"] = end_scene
    speed_ramp = coerced.get("speed_ramp")
    if isinstance(speed_ramp, dict):
        speed_ramp = dict(speed_ramp)
        if "interpolate" in speed_ramp:
            speed_ramp["interpolate"] = bool(speed_ramp["interpolate"])
        if "slow_beats" in speed_ramp:
            value = speed_ramp["slow_beats"]
            # isinstance(True, int) vaut True : sans cette garde, un booléen
            # passerait pour un nombre de beats valide.
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                value = float(value)
            lo, hi = SLOW_BEATS_RANGE
            speed_ramp["slow_beats"] = int(max(lo, min(hi, value)))
        coerced["speed_ramp"] = speed_ramp
    return coerced


ALLOWED_SUBTITLE_MODES = {"llm", "fixe"}
SUBTITLE_RANGES = {"x": (0.0, 1.0), "y": (0.0, 1.0), "size": (8, 200)}


def coerce_subtitles(subtitles: dict) -> dict:
    """Force et borne les champs de placement du texte. Le bloc `subtitles` de la
    niche est un blob JSON écrit tel quel : une valeur non numérique ne casserait
    qu'au rendu FFmpeg, loin de la saisie. ValueError si non convertible."""
    coerced = dict(subtitles)
    for key, (lo, hi) in SUBTITLE_RANGES.items():
        if key in coerced:
            value = coerced[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                value = float(value)
            coerced[key] = max(lo, min(hi, value))
    if "size" in coerced:
        coerced["size"] = int(coerced["size"])
    if "mode" in coerced and coerced["mode"] not in ALLOWED_SUBTITLE_MODES:
        raise ValueError(f"mode de sous-titres inconnu : {coerced['mode']!r}")
    return coerced


def coerce_clipper(settings: dict) -> dict:
    """Réglages du clipper, coercés et bornés côté serveur. Défense XSS et
    défense tout court : ces valeurs finissent dans une ligne de commande
    ffmpeg et dans un nom de modèle. Lève ValueError si non convertible."""
    if not isinstance(settings, dict):
        raise ValueError(f"réglages clipper invalides : {settings!r}")
    coerced = {}
    for key, (low, high) in CLIPPER_RANGES.items():
        if key not in settings:
            continue
        try:
            value = int(settings[key]) if key in CLIPPER_INT_KEYS \
                else float(settings[key])
        except (TypeError, ValueError):
            raise ValueError(f"valeur non numérique pour {key} : {settings[key]!r}")
        coerced[key] = max(low, min(high, value))
    if "whisper_model" in settings:
        model = str(settings["whisper_model"])
        if model not in ALLOWED_WHISPER_MODELS:
            raise ValueError(f"modèle Whisper inconnu : {model!r}")
        coerced["whisper_model"] = model
    # Une inversion des deux bornes est acceptable pour chacune prise seule,
    # mais `snap_to_speech` ne retiendrait alors plus aucun candidat : la source
    # finirait en `failed` avec un message accusant le recalage. On refuse à la
    # saisie, là où l'utilisateur peut encore comprendre.
    if coerced.get("min_dur", 0.0) > coerced.get("max_dur", float("inf")):
        raise ValueError(
            f"durée minimale ({coerced['min_dur']:g} s) supérieure à la durée "
            f"maximale ({coerced['max_dur']:g} s)")
    return coerced


# --- Jobs en arrière-plan (téléchargements, génération) --------------------------

_jobs: dict = {}
_jobs_lock = threading.Lock()


def _run_job(job_id: str, argv: list[str]) -> None:
    # Mode UTF-8 forcé pour le sous-process : sans ça, sur Windows le job plante
    # en cp1252 dès qu'un log contient un caractère hors Latin-1 (ex. la flèche
    # « → » de beatsync). On fixe aussi le décodage du flux côté parent.
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    process = subprocess.Popen(
        argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env,
    )
    for line in process.stdout:
        with _jobs_lock:
            _jobs[job_id]["log"].append(line.rstrip())
    process.wait()
    with _jobs_lock:
        _jobs[job_id]["status"] = "done" if process.returncode == 0 else "failed"


def start_job(name: str, argv: list[str]) -> str:
    with _jobs_lock:
        for job in _jobs.values():
            if job["name"] == name and job["status"] == "running":
                raise RuntimeError(f"un job « {name} » tourne déjà")
        job_id = uuid.uuid4().hex[:8]
        _jobs[job_id] = {"name": name, "status": "running", "log": []}
    threading.Thread(target=_run_job, args=(job_id, argv), daemon=True).start()
    return job_id


# --- Application Flask ------------------------------------------------------------


def create_app(root: Path | None = None):
    import secrets as pysecrets

    from flask import Flask, jsonify, render_template, request, session

    import db as dbmod

    root = root or ROOT
    paths = {
        "db": root / "platform.db", "data": root / "data",
        "tracks": root / "tracks",
        "clips": root / "clips",
        "links": root / "links.txt",
        "clip_links": root / "clip_links.txt",
        "settings": root / "settings.json",
        "clipper": root / "data" / "clipper",
        "dist": root / "frontend" / "dist",  # build React (mono-serveur en prod)
    }
    paths["data"].mkdir(exist_ok=True)
    secret_file = paths["data"] / "secret_key"
    if not secret_file.is_file():
        secret_file.write_text(pysecrets.token_hex(32))
        secret_file.chmod(0o600)

    app = Flask(__name__)
    app.secret_key = secret_file.read_text()
    app.config["PATHS"] = paths

    def get_conn():
        return dbmod.connect(paths["db"])

    @app.before_request
    def require_login():
        if not request.path.startswith("/api") or request.path == "/api/login":
            return None
        if "member" not in session:
            return jsonify({"error": "non connecté"}), 401

    @app.post("/api/login")
    def login():
        data = request.json or {}
        conn = get_conn()
        try:
            if dbmod.verify_member(conn, data.get("name", ""), data.get("password", "")):
                session["member"] = data["name"]
                return jsonify({"ok": True, "member": data["name"]})
        finally:
            conn.close()
        return jsonify({"error": "identifiants invalides"}), 401

    @app.post("/api/logout")
    def logout():
        session.pop("member", None)
        return jsonify({"ok": True})

    def serve_spa(path=""):
        """Sert le build React (frontend/dist) en prod : le fichier demandé s'il
        existe, sinon index.html (SPA). Retombe sur l'ancienne UI Jinja si le
        build est absent (pratique en dev sans `npm run build`). Les routes
        /api/* enregistrées ont priorité sur ce catch-all."""
        from flask import abort, send_from_directory

        dist = paths["dist"]
        if path and not path.startswith("api"):
            candidate = (dist / path)
            if candidate.is_file():
                return send_from_directory(dist, path)
        if path.startswith("api"):
            abort(404)
        if (dist / "index.html").is_file():
            return send_from_directory(dist, "index.html")
        return render_template("index.html")  # fallback dev (ancienne UI vanilla)

    app.add_url_rule("/", "index", serve_spa)
    app.add_url_rule("/<path:path>", "spa", serve_spa)

    VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
    IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
    # Le catalogue « clips » contient les deux : une image se monte en flash court.
    CLIP_EXTS = VIDEO_EXTS | IMAGE_EXTS

    @app.get("/api/state")
    def state():
        tracks_dir = paths["tracks"]
        links_path = paths["links"]

        tracks = sorted(
            (
                {"name": p.name, "size_mb": round(p.stat().st_size / 1e6, 1)}
                for p in tracks_dir.glob("*")
                if p.suffix.lower() in AUDIO_EXTENSIONS
            ),
            key=lambda t: t["name"],
        ) if tracks_dir.is_dir() else []

        clips_dir = paths["clips"]
        clips = sorted(
            (
                {"name": p.name, "size_mb": round(p.stat().st_size / 1e6, 1)}
                for p in clips_dir.glob("*")
                if p.suffix.lower() in CLIP_EXTS
            ),
            key=lambda c: c["name"],
        ) if clips_dir.is_dir() else []

        settings = load_settings(paths["settings"])
        with _jobs_lock:
            jobs = {jid: dict(job) for jid, job in _jobs.items()}

        conn = get_conn()
        try:
            niches = dbmod.list_niches(conn)
            presets = dbmod.list_presets(conn)
            videos_by_niche: dict[int, list] = {}
            for v in dbmod.list_videos(conn):
                videos_by_niche.setdefault(v["niche_id"], []).append(v)
            # Projection sur une liste blanche, comme les vidéos des niches
            # plus bas : `SELECT *` renverrait `path` et `file`, donc
            # l'arborescence disque de la machine, à un client qui n'en a pas
            # l'usage.
            clips_by_source: dict = {}
            for clip in dbmod.list_clipper_clips(conn):
                clips_by_source.setdefault(clip["source_id"], []).append(
                    {k: clip[k] for k in CLIPPER_CLIP_FIELDS})
            sources = [
                {**{k: s[k] for k in CLIPPER_SOURCE_FIELDS},
                 "clips": clips_by_source.get(s["id"], [])}
                for s in dbmod.list_clipper_sources(conn)]
        finally:
            conn.close()

        for niche in niches:
            # niche["clips"] = sélection de chemins (déjà chargée depuis la base) ;
            # le catalogue partagé est exposé au niveau racine ("clips").
            niche["videos"] = [
                {"id": v["id"], "status": v["status"], "seed": v["seed"],
                 "track": Path(v["track"]).name, "caption": v["caption"],
                 "subtitles": v["subtitles"], "created_at": v["created_at"],
                 "exists": (paths["data"].parent / v["file"]).is_file()}
                for v in videos_by_niche.get(niche["id"], [])]

        return jsonify(
            {
                "member": session["member"],
                "niches": niches,
                "presets": presets,
                "links": links_path.read_text() if links_path.is_file() else "",
                "clip_links": paths["clip_links"].read_text() if paths["clip_links"].is_file() else "",
                "tracks": tracks,
                "clips": clips,
                "settings": {k: settings[k] for k in EDITABLE_SETTINGS},
                "jobs": jobs,
                "clipper_sources": sources,
            }
        )

    @app.post("/api/links")
    def save_links():
        paths["links"].write_text(request.json["text"])
        return jsonify({"ok": True})

    @app.post("/api/download")
    def download():
        try:
            job_id = start_job("download", [sys.executable, "fetch_tracks.py"])
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"job_id": job_id})

    @app.get("/api/link-info")
    def link_info():
        """Titre + miniature d'un lien YouTube via l'oEmbed public (sans clé API).
        On ne contacte QUE youtube.com ; l'URL de l'utilisateur y est passée en
        paramètre (pas de requête sortante vers une URL arbitraire). Dégrade en
        nulls si indisponible (playlist, vidéo privée, réseau)."""
        import urllib.parse
        import urllib.request

        url = request.args.get("url", "")
        try:
            oembed = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
            with urllib.request.urlopen(oembed, timeout=6) as resp:
                data = json.loads(resp.read())
            return jsonify({"title": data.get("title"), "author": data.get("author_name"),
                            "thumbnail": data.get("thumbnail_url")})
        except Exception:
            return jsonify({"title": None, "author": None, "thumbnail": None})

    @app.post("/api/tracks")
    def upload_track():
        file = request.files["file"]
        name = Path(file.filename).name  # pas de traversée de chemin
        if Path(name).suffix.lower() not in AUDIO_EXTENSIONS:
            return jsonify({"error": f"format non supporté : {name}"}), 400
        paths["tracks"].mkdir(exist_ok=True)
        file.save(paths["tracks"] / name)
        return jsonify({"ok": True, "name": name})

    @app.post("/api/clips")
    def upload_clip():
        file = request.files["file"]
        name = Path(file.filename).name  # pas de traversée de chemin
        if Path(name).suffix.lower() not in CLIP_EXTS:
            return jsonify({"error": f"format non supporté : {name}"}), 400
        paths["clips"].mkdir(exist_ok=True)
        file.save(paths["clips"] / name)
        return jsonify({"ok": True, "name": name})

    def _delete_asset(dir_key, prefix, exts, name):
        """Efface un fichier du catalogue partagé (tracks/ ou clips/) et retire
        sa référence des niches qui le sélectionnaient. Garde anti-traversal :
        on ne touche qu'un fichier directement sous le dossier catalogue."""
        safe = Path(name).name  # neutralise toute traversée de chemin
        if Path(safe).suffix.lower() not in exts:
            return jsonify({"error": f"format non supporté : {safe}"}), 400
        base = paths[dir_key].resolve()
        target = (base / safe).resolve()
        if target.parent != base:
            return jsonify({"error": "chemin invalide"}), 400
        if not target.is_file():
            return jsonify({"error": "fichier introuvable"}), 404
        target.unlink()
        ref = prefix + safe
        field = "tracks" if prefix == "tracks/" else "clips"
        conn = get_conn()
        try:
            for niche in dbmod.list_niches(conn):
                if ref in niche[field]:
                    dbmod.update_niche(conn, niche["id"],
                                       **{field: [p for p in niche[field] if p != ref]})
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.delete("/api/tracks/<path:name>")
    def delete_track_ep(name):
        return _delete_asset("tracks", "tracks/", AUDIO_EXTENSIONS, name)

    @app.delete("/api/clips/<path:name>")
    def delete_clip_ep(name):
        return _delete_asset("clips", "clips/", CLIP_EXTS, name)

    def _serve_asset(dir_key, exts, name):
        """Sert un fichier du catalogue partagé pour aperçu (écoute/visionnage).
        Même garde anti-traversal que _delete_asset : uniquement un fichier
        directement sous le dossier catalogue. send_file gère les requêtes Range
        (scrub audio/vidéo) par défaut."""
        from flask import send_file
        safe = Path(name).name  # neutralise toute traversée de chemin
        if Path(safe).suffix.lower() not in exts:
            return jsonify({"error": f"format non supporté : {safe}"}), 400
        base = paths[dir_key].resolve()
        target = (base / safe).resolve()
        if target.parent != base or not target.is_file():
            return jsonify({"error": "fichier introuvable"}), 404
        return send_file(target, mimetype=ASSET_MIMETYPES.get(Path(safe).suffix.lower()),
                         download_name=safe)

    @app.get("/api/tracks/<path:name>")
    def serve_track_ep(name):
        return _serve_asset("tracks", AUDIO_EXTENSIONS, name)

    @app.get("/api/clips/<path:name>")
    def serve_clip_ep(name):
        return _serve_asset("clips", CLIP_EXTS, name)

    @app.post("/api/clip-links")
    def save_clip_links():
        paths["clip_links"].write_text(request.json["text"])
        return jsonify({"ok": True})

    @app.post("/api/clips/download")
    def download_clips():
        paths["clips"].mkdir(exist_ok=True)
        try:
            job_id = start_job("download-clips",
                               [sys.executable, "fetch_tracks.py",
                                str(paths["clip_links"]), "--video",
                                "--dest", str(paths["clips"])])
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"job_id": job_id})

    @app.post("/api/settings")
    def save_settings():
        overrides = {k: v for k, v in request.json.items() if k in EDITABLE_SETTINGS}
        if "clipper" in overrides:
            try:
                overrides["clipper"] = coerce_clipper(overrides["clipper"] or {})
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
        paths["settings"].write_text(json.dumps(overrides, ensure_ascii=False, indent=2) + "\n")
        return jsonify({"ok": True})

    @app.post("/api/niches/<int:niche_id>/generate")
    def generate_niche_videos(niche_id):
        niche = _niche_or_404(niche_id)
        if niche is None:
            return jsonify({"error": "niche inconnue"}), 404
        if not niche["tracks"]:
            return jsonify({"error": "aucun son sélectionné — ajoute au moins un morceau dans « Sons de la niche »"}), 400
        if not niche["clips"]:
            return jsonify({"error": "aucun clip sélectionné — ajoute au moins un extrait dans « Clips de la niche »"}), 400
        count = max(1, int((request.json or {}).get("count", niche["cadence"] or 1)))
        try:
            # On passe explicitement le root de l'instance : sans ça, le job de
            # fond ouvre ROOT/platform.db (via cwd=ROOT) et croit la niche vide
            # quand create_app est injecté avec un autre root (tests, multi-instances).
            job_id = start_job(f"gen-{niche['slug']}",
                               [sys.executable, "generate_niche.py", str(niche_id),
                                str(count), str(paths["data"].parent)])
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"job_id": job_id})

    @app.get("/api/videos/<int:video_id>")
    def serve_video(video_id):
        from flask import send_file
        conn = get_conn()
        try:
            row = conn.execute("SELECT file FROM videos WHERE id = ?", (video_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return jsonify({"error": "vidéo inconnue"}), 404
        path = (paths["data"].parent / row["file"]).resolve()
        if not path.is_file() or paths["data"].resolve() not in path.parents:
            return jsonify({"error": "fichier introuvable"}), 404
        return send_file(path, mimetype="video/mp4",
                         as_attachment=request.args.get("dl") == "1",
                         download_name=path.name)

    @app.get("/api/videos/<int:video_id>/poster")
    def serve_video_poster(video_id):
        """Vignette (frame 0) de la vidéo, pour éviter le rectangle gris de la
        bibliothèque. Extraite via ffmpeg à la première demande puis mise en
        cache (invalidée si la vidéo est plus récente)."""
        from flask import send_file
        conn = get_conn()
        try:
            row = conn.execute("SELECT file FROM videos WHERE id = ?", (video_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return jsonify({"error": "vidéo inconnue"}), 404
        video = (paths["data"].parent / row["file"]).resolve()
        if not video.is_file() or paths["data"].resolve() not in video.parents:
            return jsonify({"error": "fichier introuvable"}), 404
        cache = paths["data"] / "cache" / "posters"
        cache.mkdir(parents=True, exist_ok=True)
        poster = cache / f"{video_id}.jpg"
        if not poster.is_file() or poster.stat().st_mtime < video.stat().st_mtime:
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
                     "-frames:v", "1", "-q:v", "4", str(poster)],
                    check=True, capture_output=True,
                )
            except Exception:
                return jsonify({"error": "poster indisponible"}), 404
        return send_file(poster, mimetype="image/jpeg")

    @app.delete("/api/videos/<int:video_id>")
    def delete_video_ep(video_id):
        conn = get_conn()
        try:
            row = conn.execute("SELECT file FROM videos WHERE id = ?", (video_id,)).fetchone()
            if row is None:
                return jsonify({"error": "vidéo inconnue"}), 404
            dbmod.delete_video(conn, video_id)
        finally:
            conn.close()
        # efface le fichier sur disque, mais seulement sous data/ (garde anti-traversal)
        path = (paths["data"].parent / row["file"]).resolve()
        if path.is_file() and paths["data"].resolve() in path.parents:
            path.unlink()
        return jsonify({"ok": True})

    @app.post("/api/videos/<int:video_id>/status")
    def set_video_status_ep(video_id):
        status = (request.json or {}).get("status")
        if status not in ("proposed", "approved", "rejected", "posted"):
            return jsonify({"error": "statut invalide"}), 400
        conn = get_conn()
        try:
            dbmod.set_video_status(conn, video_id, status)
        finally:
            conn.close()
        return jsonify({"ok": True})

    # --- Clipper --------------------------------------------------------------

    def _source_or_404(source_id):
        conn = get_conn()
        try:
            return dbmod.get_clipper_source(conn, source_id)
        finally:
            conn.close()

    def _probe_duration(path: Path) -> float:
        """Durée de la source, 0 si la sonde échoue. Un fichier illisible par
        ffprobe ne doit pas faire échouer l'import : la colonne est
        informative, pas structurante."""
        import clipper

        try:
            return clipper.probe_duration(path)
        except Exception:
            return 0.0

    def _taken_slugs(conn) -> set[str]:
        """Slugs déjà pris : ceux en base ET ceux présents sur disque.

        Les deux ensembles peuvent diverger — un effacement de dossier qui
        échoue (handle ouvert sous Windows) laisse un dossier orphelin sans
        ligne en base. Réattribuer son slug ferait hériter la nouvelle source
        du `transcript.json` de l'ancienne : `process` annoncerait « Transcript
        en cache » et découperait les clips aux timestamps d'une autre vidéo,
        en silence."""
        slugs = {s["slug"] for s in dbmod.list_clipper_sources(conn)}
        clipper_dir = paths["clipper"]
        if clipper_dir.is_dir():
            slugs |= {p.name for p in clipper_dir.iterdir()
                      if p.is_dir() and not p.name.startswith("_")}
        return slugs

    def _clipper_file(rel: str):
        """Chemin absolu d'un fichier clipper, ou None s'il sort de data/.
        Même garde anti-traversal que serve_video : une ligne trafiquée en base
        ne doit pas exfiltrer un fichier arbitraire."""
        path = (paths["data"].parent / rel).resolve()
        if not path.is_file() or paths["data"].resolve() not in path.parents:
            return None
        return path

    @app.post("/api/clipper/sources")
    def upload_clipper_source():
        from datetime import datetime

        import clip_source

        file = request.files["file"]
        name = Path(file.filename).name  # pas de traversée de chemin
        if Path(name).suffix.lower() not in VIDEO_EXTS:
            return jsonify({"error": f"format non supporté : {name}"}), 400
        conn = get_conn()
        try:
            slug = clip_source.slug_for(Path(name).stem, _taken_slugs(conn))
            folder = dbmod.clipper_source_dir(paths["data"], slug)
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / ("source" + Path(name).suffix.lower())
            file.save(target)
            source_id = dbmod.create_clipper_source(
                conn, title=Path(name).stem, slug=slug,
                path=str(target.relative_to(paths["data"].parent)),
                duration=_probe_duration(target),
                created_at=datetime.now().isoformat(timespec="seconds"))
        finally:
            conn.close()
        return jsonify({"ok": True, "id": source_id, "slug": slug})

    @app.post("/api/clipper/sources/link")
    def download_clipper_source():
        """Importe un lien YouTube dans data/clipper/_inbox/ via yt-dlp. La
        source est ensuite ajoutée par l'utilisateur depuis l'inbox — on ne peut
        pas connaître le nom du fichier avant la fin du téléchargement.

        L'URL finit dans le fichier de liens que `fetch_tracks.parse_links`
        découpe ligne par ligne, chaque ligne devenant un argument positionnel
        passé à yt-dlp : un espace ou un retour à la ligne y injecterait une
        option (`--exec`, `-o`, ...) exécutée ou écrivant sur le disque. On
        rejette donc tout espacement, et on valide l'hôte par liste blanche
        plutôt que par préfixe de chaîne (un `startswith` est plus facile à
        croire fiable qu'il ne l'est)."""
        from urllib.parse import urlparse

        url = (request.json or {}).get("url", "")
        # Type vérifié avant .strip() : c'est le seul endpoint qui alimente une
        # ligne de commande, on n'y laisse pas remonter une AttributeError en 500.
        if not isinstance(url, str):
            return jsonify({"error": "lien YouTube attendu"}), 400
        url = url.strip()
        if any(c.isspace() for c in url):
            return jsonify({"error": "lien YouTube attendu"}), 400
        parsed = urlparse(url)
        allowed_hosts = {"www.youtube.com", "youtube.com", "youtu.be", "m.youtube.com"}
        # Hôte et schéma normalisés en minuscules : ils sont insensibles à la
        # casse (RFC 3986), et un WWW.YouTube.com collé depuis la barre
        # d'adresse était rejeté à tort.
        if parsed.scheme.lower() != "https" or parsed.netloc.lower() not in allowed_hosts:
            return jsonify({"error": "lien YouTube attendu"}), 400
        inbox = paths["clipper"] / "_inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        links = inbox / "links.txt"
        links.write_text(url + "\n")
        try:
            # --with-audio : sans lui, yt-dlp rend le meilleur flux VIDÉO SEULE
            # et la source arrive muette — le clipper ne traite que la parole.
            job_id = start_job("clipper-download",
                               [sys.executable, "fetch_tracks.py", str(links),
                                "--video", "--with-audio", "--dest", str(inbox)])
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"job_id": job_id})

    @app.get("/api/clipper/inbox")
    def clipper_inbox():
        """Fichiers téléchargés en attente d'être promus en source."""
        inbox = paths["clipper"] / "_inbox"
        names = sorted(p.name for p in inbox.glob("*")
                       if p.suffix.lower() in VIDEO_EXTS) if inbox.is_dir() else []
        return jsonify({"files": names})

    @app.post("/api/clipper/inbox/<path:name>")
    def promote_clipper_inbox(name):
        """Promeut un fichier de l'inbox en source (déplacement, pas copie)."""
        from datetime import datetime

        import clip_source

        import clipper

        safe = Path(name).name  # neutralise toute traversée de chemin
        origin = paths["clipper"] / "_inbox" / safe
        if origin.suffix.lower() not in VIDEO_EXTS or not origin.is_file():
            return jsonify({"error": "fichier introuvable"}), 404
        # Refus à la promotion plutôt qu'échec à l'analyse : une source muette
        # est condamnée d'avance (le lot 1 ne traite que la parole), et le
        # message d'échec de l'analyse accuserait le contenu, pas le fichier.
        try:
            muette = not clipper.has_audio(origin)
        except Exception:
            muette = False   # ffprobe indisponible : on ne bloque pas l'import
        if muette:
            return jsonify({"error": "cette vidéo n'a pas de piste audio : le "
                                     "clipper ne traite que le contenu parlé"}), 400
        conn = get_conn()
        try:
            slug = clip_source.slug_for(origin.stem, _taken_slugs(conn))
            folder = dbmod.clipper_source_dir(paths["data"], slug)
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / ("source" + origin.suffix.lower())
            origin.replace(target)
            source_id = dbmod.create_clipper_source(
                conn, title=origin.stem, slug=slug,
                path=str(target.relative_to(paths["data"].parent)),
                duration=_probe_duration(target),
                created_at=datetime.now().isoformat(timespec="seconds"))
        finally:
            conn.close()
        return jsonify({"ok": True, "id": source_id})

    @app.post("/api/clipper/sources/<int:source_id>/run")
    def run_clipper_source(source_id):
        source = _source_or_404(source_id)
        if source is None:
            return jsonify({"error": "source inconnue"}), 404
        try:
            job_id = start_job(f"clip-{source['slug']}",
                               [sys.executable, "clip_source.py", str(source_id),
                                str(paths["data"].parent)])
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        # Le sous-processus n'écrit son propre statut qu'à sa première étape
        # (transcription) : sans ça, entre le démarrage du job et cette
        # première écriture, la source reste "pending"/"done" et la garde 409
        # de la suppression ne voit rien à bloquer.
        conn = get_conn()
        try:
            dbmod.set_clipper_source_status(conn, source_id, "transcribing")
        finally:
            conn.close()
        return jsonify({"job_id": job_id})

    @app.delete("/api/clipper/sources/<int:source_id>")
    def delete_clipper_source_ep(source_id):
        import shutil

        conn = get_conn()
        try:
            source = dbmod.get_clipper_source(conn, source_id)
        finally:
            conn.close()
        if source is None:
            return jsonify({"error": "source inconnue"}), 404
        # Un job en cours survivrait à la suppression : il recréerait les
        # dossiers et écrirait des mp4 sans ligne en base — des fichiers
        # invisibles dans l'interface, que rien ne nettoiera jamais.
        if source["status"] in BUSY_CLIPPER_STATUSES:
            return jsonify({"error": "analyse en cours : attends qu'elle "
                                     "finisse avant de supprimer"}), 409
        folder = dbmod.clipper_source_dir(paths["data"], source["slug"]).resolve()
        # Le dossier part AVANT la ligne : si l'effacement échoue, la ligne
        # reste et la source est encore visible. Dans l'autre ordre, un dossier
        # orphelin subsistait avec son transcript.json, et un réupload
        # récupérant le même slug héritait du transcript d'une autre vidéo.
        #
        # Égalité stricte sur le parent (pas seulement « quelque part sous data/ »,
        # comme `_delete_asset`) : un slug vide résoudrait `folder` en
        # `data/clipper` lui-même, dont les parents contiennent bien `data` — et
        # rmtree effacerait alors le dossier clipper entier, donc les sources de
        # tous les membres.
        if folder.is_dir() and folder.parent == paths["clipper"].resolve():
            try:
                shutil.rmtree(folder)
            except OSError as exc:
                # Cas courant sur la tour Windows : un handle ouvert (lecteur
                # <video> du navigateur) fait échouer rmtree. Message clair
                # plutôt que 500, et la source reste intacte.
                return jsonify({"error": "impossible d'effacer le dossier de la "
                                         f"source (fichier ouvert ?) : {exc}"}), 409
        conn = get_conn()
        try:
            dbmod.delete_clipper_source(conn, source_id)  # cascade sur les clips
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.get("/api/clipper/clips/<int:clip_id>")
    def serve_clipper_clip(clip_id):
        from flask import send_file
        conn = get_conn()
        try:
            row = conn.execute("SELECT file FROM clipper_clips WHERE id = ?",
                               (clip_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return jsonify({"error": "clip inconnu"}), 404
        path = _clipper_file(row["file"])
        if path is None:
            return jsonify({"error": "fichier introuvable"}), 404
        return send_file(path, mimetype="video/mp4",
                         as_attachment=request.args.get("dl") == "1",
                         download_name=path.name)

    @app.post("/api/clipper/clips/<int:clip_id>/status")
    def set_clipper_clip_status_ep(clip_id):
        status = (request.json or {}).get("status")
        if status not in ("proposed", "approved", "rejected", "posted"):
            return jsonify({"error": "statut invalide"}), 400
        conn = get_conn()
        try:
            row = conn.execute("SELECT id FROM clipper_clips WHERE id = ?",
                               (clip_id,)).fetchone()
            if row is None:
                return jsonify({"error": "clip inconnu"}), 404
            dbmod.set_clipper_clip_status(conn, clip_id, status)
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.delete("/api/clipper/clips/<int:clip_id>")
    def delete_clipper_clip_ep(clip_id):
        conn = get_conn()
        try:
            row = conn.execute("SELECT file FROM clipper_clips WHERE id = ?",
                               (clip_id,)).fetchone()
            if row is None:
                return jsonify({"error": "clip inconnu"}), 404
            dbmod.delete_clipper_clip(conn, clip_id)
        finally:
            conn.close()
        path = _clipper_file(row["file"])
        if path is not None:
            path.unlink()
        return jsonify({"ok": True})

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id: str):
        with _jobs_lock:
            job = _jobs.get(job_id)
            return (jsonify(dict(job)), 200) if job else (jsonify({"error": "job inconnu"}), 404)

    @app.post("/api/niches")
    def create_niche_ep():
        data = request.json or {}
        conn = get_conn()
        try:
            subtitles = data.get("subtitles", {})
            if isinstance(subtitles, dict):
                subtitles = coerce_subtitles(subtitles)
            nid = dbmod.create_niche(
                conn, paths["data"], data["name"],
                owner=data.get("owner", session["member"]),
                cadence=int(data.get("cadence", 1)),
                caption_template=data.get("caption_template", "{title}"),
                hashtags=data.get("hashtags", []),
                preset_ids=data.get("preset_ids", []),
                subtitles=subtitles)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        finally:
            conn.close()
        return jsonify({"id": nid})

    @app.patch("/api/niches/<int:niche_id>")
    def update_niche_ep(niche_id):
        fields = dict(request.json or {})
        conn = get_conn()
        try:
            if isinstance(fields.get("subtitles"), dict):
                fields["subtitles"] = coerce_subtitles(fields["subtitles"])
            dbmod.update_niche(conn, niche_id, **fields)
        except (TypeError, ValueError) as exc:
            return jsonify({"error": f"champ invalide : {exc}"}), 400
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.delete("/api/niches/<int:niche_id>")
    def delete_niche_ep(niche_id):
        conn = get_conn()
        try:
            dbmod.delete_niche(conn, niche_id)   # fichiers conservés sur disque
        except sqlite3.IntegrityError as exc:
            return jsonify({"error": str(exc)}), 409
        finally:
            conn.close()
        return jsonify({"ok": True})

    def _niche_or_404(niche_id):
        conn = get_conn()
        try:
            niche = dbmod.get_niche(conn, niche_id)
        finally:
            conn.close()
        return niche

    @app.post("/api/presets")
    def create_preset_ep():
        data = request.json or {}
        conn = get_conn()
        try:
            pid = dbmod.create_preset(conn, data["name"],
                                      coerce_overrides(data.get("overrides", {})))
        except sqlite3.IntegrityError:
            return jsonify({"error": "ce nom de preset existe déjà"}), 409
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        finally:
            conn.close()
        return jsonify({"id": pid})

    @app.patch("/api/presets/<int:preset_id>")
    def update_preset_ep(preset_id):
        data = request.json or {}
        conn = get_conn()
        try:
            dbmod.update_preset(conn, preset_id, data["name"],
                                coerce_overrides(data.get("overrides", {})))
        except sqlite3.IntegrityError:
            return jsonify({"error": "ce nom de preset existe déjà"}), 409
        except (KeyError, TypeError, ValueError) as exc:
            return jsonify({"error": f"données invalides : {exc}"}), 400
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.delete("/api/presets/<int:preset_id>")
    def delete_preset_ep(preset_id):
        conn = get_conn()
        try:
            dbmod.delete_preset(conn, preset_id)
        finally:
            conn.close()
        return jsonify({"ok": True})

    return app


def main() -> None:
    app = create_app()
    print("Interface : http://127.0.0.1:8765  (Ctrl+C pour arrêter)")
    app.run(host="127.0.0.1", port=8765, debug=False)


if __name__ == "__main__":
    main()