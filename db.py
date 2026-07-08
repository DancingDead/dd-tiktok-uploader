"""db — persistance SQLite de la plateforme (membres, niches, presets, vidéos)."""

import json
import re
import sqlite3
import sys
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
  subtitles TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY,
  niche_id INTEGER NOT NULL REFERENCES niches(id),
  preset_id INTEGER,
  track TEXT NOT NULL,
  seed INTEGER NOT NULL,
  file TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'proposed',
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
    return conn


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


DB_PATH = Path(__file__).parent / "platform.db"


def add_member(conn: sqlite3.Connection, name: str, password: str) -> int:
    cur = conn.execute(
        "INSERT INTO members (name, password_hash) VALUES (?, ?)",
        (name, generate_password_hash(password)))
    conn.commit()
    return cur.lastrowid


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
    elif command == "list-members":
        print("\n".join(list_members(conn)) or "aucun membre")
    else:
        sys.exit("usage : python db.py add-member <name> | list-members")


if __name__ == "__main__":
    main()
