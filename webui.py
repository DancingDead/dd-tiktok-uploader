"""webui — interface locale de gestion de l'usine à vidéos.

Onglets : liens YouTube, tracks, comptes TikTok, plan & file, réglages.
Local uniquement (127.0.0.1) : manipule fichiers, tokens et secrets du projet.

    uv run python webui.py    puis  http://127.0.0.1:8765
"""

import json
import subprocess
import sys
import threading
import tomllib
import uuid
from pathlib import Path

from beatsync import DEFAULT_CONFIG, load_settings, merge_settings  # noqa: F401 (réexport)

ROOT = Path(__file__).parent
TRACKS_DIR = ROOT / "tracks"
QUEUE_DIR = ROOT / "queue"
LINKS_PATH = ROOT / "links.txt"
PLAN_PATH = ROOT / "plan.toml"
SETTINGS_PATH = ROOT / "settings.json"
TOKENS_DIR = ROOT / "tokens"

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aiff"}
# Réglages exposés dans l'onglet Paramètres (sous-ensemble sûr de DEFAULT_CONFIG)
EDITABLE_SETTINGS = [
    "effects", "accents", "delogo", "chrono", "min_presence",
    "buildup", "strobe_beats", "cut_mode", "cut_every",
]


# --- Logique pure ---------------------------------------------------------------


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    raise TypeError(f"type non sérialisable en TOML : {type(value)}")


def plan_to_toml(plan: dict) -> str:
    """Sérialise le plan (defaults + posts) en TOML relisible par tomllib."""
    lines = ["# Généré par l'interface web (webui.py)", ""]
    if plan.get("defaults"):
        lines.append("[defaults]")
        for key, value in plan["defaults"].items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    for post in plan.get("posts", []):
        lines.append("[[posts]]")
        for key, value in post.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)


# --- Jobs en arrière-plan (téléchargements, génération) --------------------------

_jobs: dict = {}
_jobs_lock = threading.Lock()


def _run_job(job_id: str, argv: list[str]) -> None:
    process = subprocess.Popen(
        argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
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
        "tracks": root / "tracks", "queue": root / "queue",
        "links": root / "links.txt", "plan": root / "plan.toml",
        "settings": root / "settings.json", "tokens": root / "tokens",
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

    @app.get("/")
    def index():
        return render_template("index.html")

    VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}

    @app.get("/api/state")
    def state():
        tracks_dir = paths["tracks"]
        tokens_dir = paths["tokens"]
        queue_dir = paths["queue"]
        links_path = paths["links"]
        plan_path = paths["plan"]

        tracks = sorted(
            (
                {"name": p.name, "size_mb": round(p.stat().st_size / 1e6, 1)}
                for p in tracks_dir.glob("*")
                if p.suffix.lower() in AUDIO_EXTENSIONS
            ),
            key=lambda t: t["name"],
        ) if tracks_dir.is_dir() else []

        accounts = []
        if tokens_dir.is_dir():
            for path in sorted(tokens_dir.glob("*.json")):
                data = json.loads(path.read_text())
                accounts.append(
                    {
                        "display_name": data.get("display_name", "?"),
                        "open_id": data.get("open_id", "?"),
                        "expires_at": data.get("expires_at", "?"),
                    }
                )

        plan = {"defaults": {"duration": 30, "caption": "{title} — OUT NOW 🔥",
                             "hashtags": ["hardstyle", "anime", "edit", "dancingdead"]},
                "posts": []}
        if plan_path.is_file():
            with open(plan_path, "rb") as f:
                loaded = tomllib.load(f)
            plan["defaults"].update(loaded.get("defaults", {}))
            plan["posts"] = loaded.get("posts", [])

        def sidecars(folder: str):
            directory = queue_dir / folder
            if not directory.is_dir():
                return []
            return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))]

        settings = load_settings(paths["settings"])
        with _jobs_lock:
            jobs = {jid: dict(job) for jid, job in _jobs.items()}

        conn = get_conn()
        try:
            niches = dbmod.list_niches(conn)
            presets = dbmod.list_presets(conn)
        finally:
            conn.close()

        for niche in niches:
            clips_dir = dbmod.niche_clips_dir(paths["data"], niche["slug"])
            niche["clips"] = sorted(
                ({"name": p.name, "size_mb": round(p.stat().st_size / 1e6, 1)}
                 for p in clips_dir.glob("*") if p.suffix.lower() in VIDEO_EXTS),
                key=lambda c: c["name"]) if clips_dir.is_dir() else []
            links = dbmod.niche_links_path(paths["data"], niche["slug"])
            niche["links"] = links.read_text() if links.is_file() else ""

        return jsonify(
            {
                "member": session["member"],
                "niches": niches,
                "presets": presets,
                "links": links_path.read_text() if links_path.is_file() else "",
                "tracks": tracks,
                "accounts": accounts,
                "plan": plan,
                "queue": {"pending": sidecars("pending"), "posted": sidecars("posted")},
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

    @app.post("/api/tracks")
    def upload_track():
        file = request.files["file"]
        name = Path(file.filename).name  # pas de traversée de chemin
        if Path(name).suffix.lower() not in AUDIO_EXTENSIONS:
            return jsonify({"error": f"format non supporté : {name}"}), 400
        paths["tracks"].mkdir(exist_ok=True)
        file.save(paths["tracks"] / name)
        return jsonify({"ok": True, "name": name})

    @app.post("/api/settings")
    def save_settings():
        overrides = {k: v for k, v in request.json.items() if k in EDITABLE_SETTINGS}
        paths["settings"].write_text(json.dumps(overrides, ensure_ascii=False, indent=2) + "\n")
        return jsonify({"ok": True})

    @app.post("/api/plan")
    def save_plan():
        paths["plan"].write_text(plan_to_toml(request.json))
        return jsonify({"ok": True})

    @app.post("/api/generate")
    def generate():
        try:
            job_id = start_job("generate", [sys.executable, "batch_generate.py"])
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"job_id": job_id})

    @app.delete("/api/queue/<stem>")
    def delete_pending(stem: str):
        stem = Path(stem).name
        removed = []
        for suffix in (".mp4", ".json"):
            path = paths["queue"] / "pending" / f"{stem}{suffix}"
            if path.is_file():
                path.unlink()
                removed.append(path.name)
        return jsonify({"removed": removed})

    @app.get("/api/auth/url")
    def auth_url():
        from tiktok_auth import REDIRECT_URI, _load_credentials, build_auth_url

        client_key, _ = _load_credentials()
        return jsonify({"url": build_auth_url(client_key, REDIRECT_URI, uuid.uuid4().hex[:12])})

    @app.post("/api/auth/code")
    def auth_code():
        from tiktok_auth import exchange_and_store, parse_code_input

        try:
            account = exchange_and_store(parse_code_input(request.json["code"]))
        except Exception as exc:  # erreurs API TikTok remontées telles quelles à l'UI
            return jsonify({"error": str(exc)}), 400
        return jsonify(account)

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
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.delete("/api/niches/<int:niche_id>")
    def delete_niche_ep(niche_id):
        conn = get_conn()
        try:
            dbmod.delete_niche(conn, niche_id)   # fichiers conservés sur disque
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

    @app.post("/api/niches/<int:niche_id>/clips")
    def upload_niche_clip(niche_id):
        niche = _niche_or_404(niche_id)
        if niche is None:
            return jsonify({"error": "niche inconnue"}), 404
        file = request.files["file"]
        name = Path(file.filename).name
        if Path(name).suffix.lower() not in VIDEO_EXTS:
            return jsonify({"error": f"format non supporté : {name}"}), 400
        target = dbmod.niche_clips_dir(paths["data"], niche["slug"])
        target.mkdir(parents=True, exist_ok=True)
        file.save(target / name)
        return jsonify({"ok": True, "name": name})

    @app.post("/api/niches/<int:niche_id>/links")
    def save_niche_links(niche_id):
        niche = _niche_or_404(niche_id)
        if niche is None:
            return jsonify({"error": "niche inconnue"}), 404
        links = dbmod.niche_links_path(paths["data"], niche["slug"])
        links.parent.mkdir(parents=True, exist_ok=True)
        links.write_text(request.json["text"])
        return jsonify({"ok": True})

    @app.post("/api/niches/<int:niche_id>/download")
    def download_niche_clips(niche_id):
        niche = _niche_or_404(niche_id)
        if niche is None:
            return jsonify({"error": "niche inconnue"}), 404
        links = dbmod.niche_links_path(paths["data"], niche["slug"])
        clips = dbmod.niche_clips_dir(paths["data"], niche["slug"])
        try:
            job_id = start_job(f"clips-{niche['slug']}",
                               [sys.executable, "fetch_tracks.py", str(links),
                                "--video", "--dest", str(clips)])
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"job_id": job_id})

    @app.post("/api/presets")
    def create_preset_ep():
        data = request.json or {}
        conn = get_conn()
        try:
            pid = dbmod.create_preset(conn, data["name"], data.get("overrides", {}))
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
            dbmod.update_preset(conn, preset_id, data["name"], data.get("overrides", {}))
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