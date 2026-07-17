"""db — persistance SQLite de la plateforme (membres, niches, presets, vidéos)."""

import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from beatsync import load_settings, merge_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS presets (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  overrides TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS niches (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  owner TEXT NOT NULL DEFAULT '',
  cadence INTEGER NOT NULL DEFAULT 1,
  caption_template TEXT NOT NULL DEFAULT '{title}',
  hashtags TEXT NOT NULL DEFAULT '[]',
  preset_ids TEXT NOT NULL DEFAULT '[]',
  tracks TEXT NOT NULL DEFAULT '[]',
  clips TEXT NOT NULL DEFAULT '[]',
  subtitles TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY,
  niche_id INTEGER NOT NULL REFERENCES niches(id) ON DELETE CASCADE,
  preset_id INTEGER,
  track TEXT NOT NULL,
  seed INTEGER NOT NULL,
  file TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'proposed'
    CHECK (status IN ('proposed','approved','rejected','posted','failed')),
  caption TEXT NOT NULL DEFAULT '',
  subtitles TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  error TEXT
);
"""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Colonnes ajoutées après la création initiale du schéma : ADD COLUMN si absentes
# (CREATE TABLE IF NOT EXISTS ne met pas à jour une base existante).
_ADDED_COLUMNS = {
    "niches": {"tracks": "TEXT NOT NULL DEFAULT '[]'",
               "clips": "TEXT NOT NULL DEFAULT '[]'"},
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in columns.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


DB_PATH = Path(__file__).parent / "platform.db"


def add_member(conn: sqlite3.Connection, name: str, password: str) -> int:
    cur = conn.execute(
        "INSERT INTO members (name, password_hash) VALUES (?, ?)",
        (name, generate_password_hash(password)))
    conn.commit()
    return cur.lastrowid


def set_password(conn: sqlite3.Connection, name: str, password: str) -> None:
    cur = conn.execute(
        "UPDATE members SET password_hash = ? WHERE name = ?",
        (generate_password_hash(password), name))
    if cur.rowcount == 0:
        raise KeyError(f"membre inconnu : {name}")
    conn.commit()


def verify_member(conn: sqlite3.Connection, name: str, password: str) -> bool:
    row = conn.execute(
        "SELECT password_hash FROM members WHERE name = ?", (name,)).fetchone()
    return bool(row) and check_password_hash(row["password_hash"], password)


def list_members(conn: sqlite3.Connection) -> list[str]:
    return [r["name"] for r in conn.execute("SELECT name FROM members ORDER BY name")]


def create_preset(conn: sqlite3.Connection, name: str, overrides: dict) -> int:
    cur = conn.execute(
        "INSERT INTO presets (name, overrides) VALUES (?, ?)",
        (name, json.dumps(overrides, ensure_ascii=False)))
    conn.commit()
    return cur.lastrowid


def list_presets(conn: sqlite3.Connection) -> list[dict]:
    return [
        {"id": r["id"], "name": r["name"], "overrides": json.loads(r["overrides"])}
        for r in conn.execute("SELECT * FROM presets ORDER BY name")
    ]


def update_preset(conn: sqlite3.Connection, preset_id: int, name: str, overrides: dict) -> None:
    conn.execute(
        "UPDATE presets SET name = ?, overrides = ? WHERE id = ?",
        (name, json.dumps(overrides, ensure_ascii=False), preset_id))
    conn.commit()


def delete_preset(conn: sqlite3.Connection, preset_id: int) -> None:
    conn.execute("DELETE FROM presets WHERE id = ?", (preset_id,))
    conn.commit()


NICHE_JSON_FIELDS = {"hashtags": "[]", "preset_ids": "[]", "tracks": "[]",
                     "clips": "[]", "subtitles": "{}"}


def niche_clips_dir(data_root: Path, slug: str) -> Path:
    return data_root / "niches" / slug / "clips"


def niche_videos_dir(data_root: Path, slug: str) -> Path:
    return data_root / "niches" / slug / "videos"


def niche_links_path(data_root: Path, slug: str) -> Path:
    return data_root / "niches" / slug / "links.txt"


def _niche_row_to_dict(row: sqlite3.Row) -> dict:
    niche = dict(row)
    for field in NICHE_JSON_FIELDS:
        niche[field] = json.loads(niche[field])
    return niche


def create_niche(conn: sqlite3.Connection, data_root: Path, name: str, *,
                 owner: str = "", cadence: int = 1,
                 caption_template: str = "{title}",
                 hashtags: list | None = None, preset_ids: list | None = None,
                 tracks: list | None = None, clips: list | None = None,
                 subtitles: dict | None = None) -> int:
    slug = slugify(name)
    cur = conn.execute(
        "INSERT INTO niches (name, slug, owner, cadence, caption_template,"
        " hashtags, preset_ids, tracks, clips, subtitles)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (name, slug, owner, cadence, caption_template,
         json.dumps(hashtags or [], ensure_ascii=False),
         json.dumps(preset_ids or []),
         json.dumps(tracks or [], ensure_ascii=False),
         json.dumps(clips or [], ensure_ascii=False),
         json.dumps(subtitles or {}, ensure_ascii=False)))
    niche_clips_dir(data_root, slug).mkdir(parents=True, exist_ok=True)
    conn.commit()
    return cur.lastrowid


def get_niche(conn: sqlite3.Connection, niche_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM niches WHERE id = ?", (niche_id,)).fetchone()
    return _niche_row_to_dict(row) if row else None


def list_niches(conn: sqlite3.Connection) -> list[dict]:
    return [_niche_row_to_dict(r)
            for r in conn.execute("SELECT * FROM niches ORDER BY name")]


def update_niche(conn: sqlite3.Connection, niche_id: int, **fields) -> None:
    allowed = {"name", "owner", "cadence", "caption_template",
               "hashtags", "preset_ids", "tracks", "clips", "subtitles"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    if "cadence" in updates:
        updates["cadence"] = int(updates["cadence"])  # ValueError/TypeError si non numérique
    assignments, values = [], []
    for key, value in updates.items():
        assignments.append(f"{key} = ?")
        values.append(json.dumps(value, ensure_ascii=False)
                      if key in NICHE_JSON_FIELDS else value)
    conn.execute(f"UPDATE niches SET {', '.join(assignments)} WHERE id = ?",
                 (*values, niche_id))
    conn.commit()


def delete_niche(conn: sqlite3.Connection, niche_id: int) -> None:
    conn.execute("DELETE FROM niches WHERE id = ?", (niche_id,))
    conn.commit()


def create_video(conn: sqlite3.Connection, *, niche_id: int, track: str,
                 seed: int, file: str, created_at: str, preset_id: int | None = None,
                 caption: str = "", subtitles: dict | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO videos (niche_id, preset_id, track, seed, file, caption,"
        " subtitles, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (niche_id, preset_id, track, seed, file, caption,
         json.dumps(subtitles or {}, ensure_ascii=False), created_at))
    conn.commit()
    return cur.lastrowid


def list_videos(conn: sqlite3.Connection, status: str | None = None,
                niche_id: int | None = None) -> list[dict]:
    query, params = "SELECT * FROM videos WHERE 1=1", []
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    if niche_id is not None:
        query += " AND niche_id = ?"
        params.append(niche_id)
    rows = conn.execute(query + " ORDER BY created_at DESC", params)
    videos = []
    for row in rows:
        video = dict(row)
        video["subtitles"] = json.loads(video["subtitles"])
        videos.append(video)
    return videos


def set_video_status(conn: sqlite3.Connection, video_id: int, status: str,
                     error: str | None = None) -> None:
    conn.execute("UPDATE videos SET status = ?, error = ? WHERE id = ?",
                 (status, error, video_id))
    conn.commit()


def delete_video(conn: sqlite3.Connection, video_id: int) -> None:
    """Retire la ligne vidéo (le fichier sur disque est géré par l'appelant)."""
    conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    conn.commit()


def effective_config(overrides: dict) -> dict:
    """DEFAULT_CONFIG ← settings.json ← preset (ordre de la spec)."""
    return merge_settings(load_settings(), overrides)


def main() -> None:
    import getpass

    command = sys.argv[1] if len(sys.argv) > 1 else ""
    conn = connect(DB_PATH)
    if command == "add-member" and len(sys.argv) == 3:
        password = getpass.getpass(f"mot de passe pour {sys.argv[2]} : ")
        add_member(conn, sys.argv[2], password)
        print(f"membre ajouté : {sys.argv[2]}")
    elif command == "set-password" and len(sys.argv) == 3:
        password = getpass.getpass(f"nouveau mot de passe pour {sys.argv[2]} : ")
        try:
            set_password(conn, sys.argv[2], password)
        except KeyError as exc:
            sys.exit(str(exc.args[0]))
        print(f"mot de passe mis à jour : {sys.argv[2]}")
    elif command == "list-members":
        print("\n".join(list_members(conn)) or "aucun membre")
    else:
        sys.exit("usage : python db.py add-member <name> | set-password <name> | list-members")


if __name__ == "__main__":
    main()
