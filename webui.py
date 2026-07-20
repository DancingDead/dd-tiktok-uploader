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
    "buildup", "strobe_beats", "cut_mode", "cut_every",
]
# Clés d'overrides de preset qui doivent être numériques (défense XSS : jamais de HTML stocké)
NUMERIC_OVERRIDE_KEYS = ("min_presence", "cut_every", "buildup", "strobe_beats",
                         "grain", "clip_speed")
ALLOWED_COLOR_GRADES = ("neutre", "chaud", "froid", "delave")
ALLOWED_SECTIONS = ("drop", "calm")


def coerce_overrides(overrides: dict) -> dict:
    """Force les clés numériques connues en nombres, coerce l'intensité de glitch
    et valide color_grade. ValueError/TypeError si non convertible/inconnu."""
    coerced = dict(overrides)
    for key in NUMERIC_OVERRIDE_KEYS:
        if key in coerced and not isinstance(coerced[key], (int, float)):
            coerced[key] = float(coerced[key])
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
                if p.suffix.lower() in VIDEO_EXTS
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
        if Path(name).suffix.lower() not in VIDEO_EXTS:
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
        return _delete_asset("clips", "clips/", VIDEO_EXTS, name)

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
            nid = dbmod.create_niche(
                conn, paths["data"], data["name"],
                owner=data.get("owner", session["member"]),
                cadence=int(data.get("cadence", 1)),
                caption_template=data.get("caption_template", "{title}"),
                hashtags=data.get("hashtags", []),
                preset_ids=data.get("preset_ids", []),
                subtitles=data.get("subtitles", {}))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        finally:
            conn.close()
        return jsonify({"id": nid})

    @app.patch("/api/niches/<int:niche_id>")
    def update_niche_ep(niche_id):
        conn = get_conn()
        try:
            dbmod.update_niche(conn, niche_id, **(request.json or {}))
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