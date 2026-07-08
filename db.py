"""db — persistance SQLite de la plateforme (membres, niches, presets, vidéos)."""

import re
import sqlite3
from pathlib import Path

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
