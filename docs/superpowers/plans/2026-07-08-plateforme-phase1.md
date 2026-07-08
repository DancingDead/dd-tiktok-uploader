# Plateforme d'équipe — Phase 1 : fondations

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Base SQLite + login membres + niches (avec banques de clips) + presets de montage + cache de scan, en évolution de `webui.py` — utilisable en local.

**Architecture:** Un nouveau module `db.py` (SQLite, CRUD pur, testable avec un fichier temporaire) ; `webui.py` refactoré en `create_app(root)` injectable pour être testé par `test_client` Flask, avec session-login et endpoints niches/presets ; le cache de scan vit dans `beatsync.py` (domaine du scan) ; le téléchargement de clips VIDÉO par niche réutilise `fetch_tracks.py` avec un flag `--video`.

**Tech Stack:** Python 3.11+, Flask (installé), sqlite3 (stdlib), werkzeug.security (via Flask) pour les mots de passe, pytest.

## Global Constraints

- Spec de référence : `docs/superpowers/specs/2026-07-08-plateforme-equipe-design.md`.
- Fusion de config TOUJOURS : `DEFAULT_CONFIG ← settings.json ← preset` (via `beatsync.merge_settings`).
- Statuts vidéo : `proposed | approved | rejected | posted | failed` (chaîne exacte).
- Stockage niche : `data/niches/<slug>/clips/` ; liens de la niche : `data/niches/<slug>/links.txt`.
- Jamais de secrets en DB ni en git : mots de passe hachés (werkzeug), `platform.db` et `data/` gitignorés.
- Équipe 2-4 égaux : pas de rôles. Membres créés par CLI (`python db.py add-member`), pas d'UI d'inscription.
- Pas d'usine ni de génération dans cette phase (phase 2) ; la table `videos` est créée mais seulement CRUD minimal.
- TDD sur toute logique pure ; endpoints testés via `test_client` ; UI validée par curl + navigateur.

---

### Task 1: db.py — connexion, schéma, slugify

**Files:**
- Create: `db.py`
- Test: `tests/test_db.py`
- Modify: `.gitignore` (ajouter `platform.db`, `data/`)

**Interfaces:**
- Produces: `connect(path: Path) -> sqlite3.Connection` (schéma initialisé, `row_factory=sqlite3.Row`, FK on) ; `slugify(text: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
import sqlite3
from pathlib import Path

import pytest

from db import connect, slugify


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    yield c
    c.close()


def test_connect_creates_schema(conn):
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"members", "presets", "niches", "videos"} <= tables


def test_connect_enforces_foreign_keys(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO videos (niche_id, track, seed, file, created_at)"
            " VALUES (999, 't', 1, 'f', 'now')")


def test_slugify():
    assert slugify("Naruto Edits — Sombre") == "naruto-edits-sombre"
    assert slugify("  Gym / Phonk!  ") == "gym-phonk"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py -q`
Expected: FAIL (ModuleNotFoundError: db)

- [ ] **Step 3: Write minimal implementation**

```python
# db.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_db.py -q` — Expected: 3 passed

- [ ] **Step 5: Ajouter `platform.db` et `data/` au .gitignore, puis commit**

```bash
printf "platform.db\ndata/\n" >> .gitignore
git add db.py tests/test_db.py .gitignore
git commit -m "feat(platform): db.py — schéma SQLite et slugify"
```

---

### Task 2: db.py — membres et bootstrap CLI

**Files:**
- Modify: `db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `connect` (Task 1).
- Produces: `add_member(conn, name, password) -> int` ; `verify_member(conn, name, password) -> bool` ; `list_members(conn) -> list[str]` ; CLI `python db.py add-member <name>` / `list-members`.

- [ ] **Step 1: Write the failing test** (ajouter à `tests/test_db.py`)

```python
from db import add_member, list_members, verify_member


def test_member_roundtrip(conn):
    add_member(conn, "theo", "s3cret")
    assert verify_member(conn, "theo", "s3cret") is True
    assert verify_member(conn, "theo", "mauvais") is False
    assert verify_member(conn, "inconnu", "s3cret") is False
    assert list_members(conn) == ["theo"]


def test_member_password_is_hashed(conn):
    add_member(conn, "theo", "s3cret")
    stored = conn.execute("SELECT password_hash FROM members").fetchone()[0]
    assert "s3cret" not in stored
```

- [ ] **Step 2: Run** `uv run pytest tests/test_db.py -q` — Expected: FAIL (ImportError add_member)

- [ ] **Step 3: Implementation** (ajouter à `db.py`)

```python
import sys
from werkzeug.security import check_password_hash, generate_password_hash

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
```

- [ ] **Step 4: Run** `uv run pytest tests/test_db.py -q` — Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat(platform): membres avec mots de passe hachés + CLI add-member"
```

---

### Task 3: db.py — presets et effective_config

**Files:**
- Modify: `db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `beatsync.load_settings`, `beatsync.merge_settings`.
- Produces: `create_preset(conn, name, overrides: dict) -> int` ; `list_presets(conn) -> list[dict]` (`{id, name, overrides}` dict décodé) ; `update_preset(conn, preset_id, name, overrides)` ; `delete_preset(conn, preset_id)` ; `effective_config(overrides: dict) -> dict`.

- [ ] **Step 1: Write the failing test** (ajouter)

```python
from db import (create_preset, delete_preset, effective_config, list_presets,
                update_preset)


def test_preset_crud(conn):
    pid = create_preset(conn, "strobo hard", {"cut_mode": "fixed", "cut_every": 1})
    assert list_presets(conn) == [
        {"id": pid, "name": "strobo hard",
         "overrides": {"cut_mode": "fixed", "cut_every": 1}}]
    update_preset(conn, pid, "strobo", {"cut_every": 2})
    assert list_presets(conn)[0]["name"] == "strobo"
    assert list_presets(conn)[0]["overrides"] == {"cut_every": 2}
    delete_preset(conn, pid)
    assert list_presets(conn) == []


def test_effective_config_merges_preset_over_defaults():
    config = effective_config({"effects": {"shake": False}, "cut_every": 4,
                               "clé_inconnue": 1})
    assert config["cut_every"] == 4
    assert config["effects"]["shake"] is False
    assert config["effects"]["zoom"] is True        # défaut préservé
    assert "clé_inconnue" not in config             # clés inconnues ignorées
```

- [ ] **Step 2: Run** `uv run pytest tests/test_db.py -q` — Expected: FAIL (ImportError)

- [ ] **Step 3: Implementation** (ajouter à `db.py`)

```python
import json

from beatsync import load_settings, merge_settings


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
```

- [ ] **Step 4: Run** `uv run pytest tests/test_db.py -q` — Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat(platform): presets de montage + effective_config"
```

---

### Task 4: db.py — niches (avec dossiers de banque)

**Files:**
- Modify: `db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `slugify` (Task 1).
- Produces: `create_niche(conn, data_root: Path, name, **fields) -> int` (crée `data/niches/<slug>/clips/`) ; `get_niche(conn, niche_id) -> dict | None` ; `list_niches(conn) -> list[dict]` ; `update_niche(conn, niche_id, **fields)` ; `delete_niche(conn, niche_id)` ; `niche_clips_dir(data_root, slug) -> Path` ; `niche_links_path(data_root, slug) -> Path`. Champs dict niche : `id, name, slug, owner, cadence, caption_template, hashtags (list), preset_ids (list), subtitles (dict)`.

- [ ] **Step 1: Write the failing test** (ajouter)

```python
from db import (create_niche, delete_niche, get_niche, list_niches,
                niche_clips_dir, niche_links_path, update_niche)


def test_niche_crud_and_folders(conn, tmp_path):
    nid = create_niche(conn, tmp_path, "Naruto Edits", owner="theo", cadence=2,
                       hashtags=["naruto", "edit"], preset_ids=[1],
                       subtitles={"enabled": True, "preprompt": "sombre"})
    niche = get_niche(conn, nid)
    assert niche["slug"] == "naruto-edits"
    assert niche["hashtags"] == ["naruto", "edit"]
    assert niche["preset_ids"] == [1]
    assert niche["subtitles"]["enabled"] is True
    assert niche_clips_dir(tmp_path, "naruto-edits").is_dir()

    update_niche(conn, nid, cadence=3, hashtags=["anime"])
    assert get_niche(conn, nid)["cadence"] == 3
    assert get_niche(conn, nid)["hashtags"] == ["anime"]

    assert [n["slug"] for n in list_niches(conn)] == ["naruto-edits"]
    delete_niche(conn, nid)
    assert get_niche(conn, nid) is None


def test_niche_slug_collision_rejected(conn, tmp_path):
    create_niche(conn, tmp_path, "Gym")
    import sqlite3 as sq
    import pytest as pt
    with pt.raises(sq.IntegrityError):
        create_niche(conn, tmp_path, "GYM !")   # même slug "gym"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_db.py -q` — Expected: FAIL (ImportError)

- [ ] **Step 3: Implementation** (ajouter à `db.py`)

```python
NICHE_JSON_FIELDS = {"hashtags": "[]", "preset_ids": "[]", "subtitles": "{}"}


def niche_clips_dir(data_root: Path, slug: str) -> Path:
    return data_root / "niches" / slug / "clips"


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
                 subtitles: dict | None = None) -> int:
    slug = slugify(name)
    cur = conn.execute(
        "INSERT INTO niches (name, slug, owner, cadence, caption_template,"
        " hashtags, preset_ids, subtitles) VALUES (?,?,?,?,?,?,?,?)",
        (name, slug, owner, cadence, caption_template,
         json.dumps(hashtags or [], ensure_ascii=False),
         json.dumps(preset_ids or []),
         json.dumps(subtitles or {}, ensure_ascii=False)))
    conn.commit()
    niche_clips_dir(data_root, slug).mkdir(parents=True, exist_ok=True)
    return cur.lastrowid


def get_niche(conn: sqlite3.Connection, niche_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM niches WHERE id = ?", (niche_id,)).fetchone()
    return _niche_row_to_dict(row) if row else None


def list_niches(conn: sqlite3.Connection) -> list[dict]:
    return [_niche_row_to_dict(r)
            for r in conn.execute("SELECT * FROM niches ORDER BY name")]


def update_niche(conn: sqlite3.Connection, niche_id: int, **fields) -> None:
    allowed = {"name", "owner", "cadence", "caption_template",
               "hashtags", "preset_ids", "subtitles"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
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
```

- [ ] **Step 4: Run** `uv run pytest tests/test_db.py -q` — Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat(platform): niches avec banques de clips par slug"
```

---

### Task 5: db.py — vidéos (schéma déjà créé, CRUD minimal)

**Files:**
- Modify: `db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `create_video(conn, *, niche_id, track, seed, file, preset_id=None, caption="", subtitles=None, created_at: str) -> int` ; `list_videos(conn, status=None, niche_id=None) -> list[dict]` ; `set_video_status(conn, video_id, status, error=None)`. `created_at` fourni par l'appelant (ISO).

- [ ] **Step 1: Write the failing test** (ajouter)

```python
from db import create_video, list_videos, set_video_status


def test_video_lifecycle(conn, tmp_path):
    nid = create_niche(conn, tmp_path, "Test")
    vid = create_video(conn, niche_id=nid, track="tracks/x.wav", seed=42,
                       file="data/niches/test/videos/v.mp4",
                       caption="X 🔥", subtitles={"hook": "..."},
                       created_at="2026-07-08T12:00:00")
    videos = list_videos(conn)
    assert videos[0]["status"] == "proposed"
    assert videos[0]["subtitles"] == {"hook": "..."}

    set_video_status(conn, vid, "approved")
    assert list_videos(conn, status="approved")[0]["id"] == vid
    assert list_videos(conn, status="rejected") == []
    assert list_videos(conn, niche_id=nid + 1) == []

    set_video_status(conn, vid, "failed", error="ffmpeg a explosé")
    assert list_videos(conn)[0]["error"] == "ffmpeg a explosé"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_db.py -q` — Expected: FAIL (ImportError)

- [ ] **Step 3: Implementation** (ajouter à `db.py`)

```python
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
```

- [ ] **Step 4: Run** `uv run pytest tests/test_db.py -q` — Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat(platform): table videos + cycle de statuts"
```

---

### Task 6: cache de scan dans beatsync

**Files:**
- Modify: `beatsync.py` (fonction `scan_clips`, extraction de `_scan_one`)
- Test: `tests/test_scan_cache.py`

**Interfaces:**
- Consumes: `scan_clips` existant (décode via FFmpeg, remplit `intervals`, `interest_x`, `dual`, `scan_dt`).
- Produces: `scan_clips(clips, cache_dir: Path | None = None)` — si `cache_dir` donné, résultat par clip mis en cache dans `<cache_dir>/<md5(path)>.json`, invalidé quand le mtime du fichier change. Helpers purs : `_scan_payload(clip) -> dict`, `_apply_scan_payload(clip, payload)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scan_cache.py
"""Cache de scan : round-trip pur + invalidation mtime (scan réel stubé)."""

import numpy as np
import pytest

import beatsync
from beatsync import _apply_scan_payload, _scan_payload, scan_clips


def make_scanned_clip(path):
    return {
        "path": path, "duration": 10.0, "width": 1920, "height": 1080,
        "ratio": 16 / 9,
        "intervals": [{"start": 1.0, "end": 9.0, "motion": 0.1, "presence": 0.8}],
        "interest_x": np.array([0.4, 0.6]),
        "dual": np.array([False, True]),
        "scan_dt": 0.5,
    }


def test_payload_roundtrip(tmp_path):
    clip = make_scanned_clip(tmp_path / "a.mp4")
    payload = _scan_payload(clip)
    restored = {"path": clip["path"], "duration": 10.0, "width": 1920,
                "height": 1080, "ratio": 16 / 9}
    _apply_scan_payload(restored, payload)
    assert restored["intervals"] == clip["intervals"]
    assert np.allclose(restored["interest_x"], clip["interest_x"])
    assert restored["dual"].dtype == bool and list(restored["dual"]) == [False, True]
    assert restored["scan_dt"] == 0.5


def test_cache_hit_and_mtime_invalidation(tmp_path, monkeypatch):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"fake")
    calls = []

    def fake_scan_one(clip):
        calls.append(clip["path"])
        clip.update({k: v for k, v in make_scanned_clip(video).items()
                     if k != "path"})

    monkeypatch.setattr(beatsync, "_scan_one", fake_scan_one)
    cache = tmp_path / "cache"

    clip = {"path": video, "duration": 10.0, "width": 1920, "height": 1080,
            "ratio": 16 / 9}
    scan_clips([dict(clip)], cache_dir=cache)
    scan_clips([dict(clip)], cache_dir=cache)
    assert len(calls) == 1                      # 2e appel servi par le cache

    video.write_bytes(b"fake modifié")          # mtime change
    import os
    os.utime(video, (video.stat().st_atime, video.stat().st_mtime + 10))
    scan_clips([dict(clip)], cache_dir=cache)
    assert len(calls) == 2                      # invalidé, re-scanné


def test_no_cache_dir_means_always_scan(tmp_path, monkeypatch):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"fake")
    calls = []
    monkeypatch.setattr(beatsync, "_scan_one",
                        lambda clip: calls.append(1) or clip.update(
                            {k: v for k, v in make_scanned_clip(video).items()
                             if k != "path"}))
    clip = {"path": video, "duration": 10.0, "width": 1920, "height": 1080,
            "ratio": 16 / 9}
    scan_clips([dict(clip)])
    scan_clips([dict(clip)])
    assert len(calls) == 2
```

- [ ] **Step 2: Run** `uv run pytest tests/test_scan_cache.py -q` — Expected: FAIL (ImportError `_scan_payload`)

- [ ] **Step 3: Implementation** — dans `beatsync.py`, extraire le corps actuel de la boucle de `scan_clips` dans `_scan_one(clip)`, puis :

```python
import hashlib


def _scan_one(clip: dict) -> None:
    """Scan réel d'un clip (décodage FFmpeg + détections). Mute le dict."""
    # <corps actuel de la boucle for de scan_clips, inchangé :
    #  subprocess ffmpeg rawvideo → frames → classify_frames →
    #  _char_presence → usable_intervals ; remplit clip["intervals"],
    #  clip["interest_x"], clip["dual"], clip["scan_dt"]>


def _scan_payload(clip: dict) -> dict:
    return {
        "intervals": clip["intervals"],
        "interest_x": [float(x) for x in clip["interest_x"]],
        "dual": [bool(d) for d in clip["dual"]],
        "scan_dt": clip["scan_dt"],
    }


def _apply_scan_payload(clip: dict, payload: dict) -> None:
    clip["intervals"] = payload["intervals"]
    clip["interest_x"] = np.array(payload["interest_x"], dtype=float)
    clip["dual"] = np.array(payload["dual"], dtype=bool)
    clip["scan_dt"] = payload["scan_dt"]


def scan_clips(clips: list[dict], cache_dir: Path | None = None) -> list[dict]:
    """Scan des clips, avec cache par fichier (clé md5 du chemin, invalidé
    par mtime) quand cache_dir est fourni — on ne re-décode pas 30 clips à
    chaque génération."""
    for clip in clips:
        cache_path = None
        if cache_dir is not None:
            digest = hashlib.md5(str(clip["path"]).encode()).hexdigest()
            cache_path = cache_dir / f"{digest}.json"
            if cache_path.is_file():
                cached = json.loads(cache_path.read_text())
                if cached.get("mtime") == clip["path"].stat().st_mtime:
                    _apply_scan_payload(clip, cached)
                    continue
        _scan_one(clip)
        if cache_path is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(
                {"mtime": clip["path"].stat().st_mtime, **_scan_payload(clip)},
                ensure_ascii=False))
    return clips
```

- [ ] **Step 4: Run** `uv run pytest -q` — Expected: TOUTE la suite passe (les tests existants de scan ne doivent pas bouger)

- [ ] **Step 5: Commit**

```bash
git add beatsync.py tests/test_scan_cache.py
git commit -m "feat(platform): cache de scan par clip (mtime)"
```

---

### Task 7: fetch_tracks --video (clips par niche)

**Files:**
- Modify: `fetch_tracks.py`
- Test: `tests/test_fetch_tracks.py`

**Interfaces:**
- Consumes: `parse_links`, `download_tracks` existants.
- Produces: `ytdlp_args(dest: Path, video: bool) -> list[str]` (pur) ; `download_tracks(urls, dest, video=False)` ; CLI `python fetch_tracks.py <links> --dest <dir> --video`.

- [ ] **Step 1: Write the failing test** (ajouter à `tests/test_fetch_tracks.py`)

```python
from pathlib import Path

from fetch_tracks import ytdlp_args


def test_ytdlp_args_audio_default():
    args = ytdlp_args(Path("tracks"), video=False)
    assert "--extract-audio" in args
    assert "mp3" in args


def test_ytdlp_args_video_mode():
    args = ytdlp_args(Path("clips"), video=True)
    assert "--extract-audio" not in args
    assert any("bv*[height<=1080]" in a for a in args)
    assert "--remux-video" in args and "mp4" in args
```

- [ ] **Step 2: Run** `uv run pytest tests/test_fetch_tracks.py -q` — Expected: FAIL (ImportError)

- [ ] **Step 3: Implementation** — dans `fetch_tracks.py`, extraire la construction des arguments :

```python
def ytdlp_args(dest: Path, video: bool) -> list[str]:
    """Arguments yt-dlp : audio mp3 par défaut, ou vidéo ≤1080p mp4 (clips)."""
    common = ["--restrict-filenames", "--no-overwrites", "--ignore-errors",
              "-o", str(dest / "%(title)s.%(ext)s")]
    if video:
        return ["-f", "bv*[height<=1080][ext=mp4]/bv*[height<=1080]/bv*",
                "--remux-video", "mp4", *common]
    return ["--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
            *common]


def download_tracks(urls: list[str], dest: Path, video: bool = False) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "yt_dlp", *ytdlp_args(dest, video), *urls])
    return result.returncode
```

Et dans `main()` : `parser.add_argument("--video", action="store_true", help="télécharge la VIDÉO (clips) au lieu de l'audio")` puis `download_tracks(urls, Path(args.dest), video=args.video)`.

- [ ] **Step 4: Run** `uv run pytest -q` — Expected: toute la suite passe

- [ ] **Step 5: Commit**

```bash
git add fetch_tracks.py tests/test_fetch_tracks.py
git commit -m "feat(platform): fetch_tracks --video pour les banques de clips"
```

---

### Task 8: webui — create_app injectable + login/session

**Files:**
- Modify: `webui.py`
- Test: `tests/test_webui_auth.py`

**Interfaces:**
- Consumes: `db.connect`, `db.add_member`, `db.verify_member`.
- Produces: `create_app(root: Path | None = None) -> Flask` — tous les chemins dérivés de `root` (`root/"platform.db"`, `root/"data"`, `root/"tracks"`, `root/"queue"`, `root/"links.txt"`, `root/"plan.toml"`, `root/"settings.json"`, `root/"tokens"`) via `app.config["PATHS"]` ; secret de session persisté dans `root/"data/secret_key"` (0600) ; `POST /api/login {name, password}` ; `POST /api/logout` ; garde `before_request` → 401 JSON sur `/api/*` sans session (sauf `/api/login`) ; `/api/state` gagne `"member": <nom>`, `"niches": [...]`, `"presets": [...]` (vides pour l'instant).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webui_auth.py
import pytest

from db import add_member, connect
from webui import create_app


@pytest.fixture
def client(tmp_path):
    conn = connect(tmp_path / "platform.db")
    add_member(conn, "theo", "s3cret")
    conn.close()
    app = create_app(root=tmp_path)
    app.config["TESTING"] = True
    return app.test_client()


def test_api_requires_login(client):
    assert client.get("/api/state").status_code == 401


def test_login_logout_cycle(client):
    bad = client.post("/api/login", json={"name": "theo", "password": "faux"})
    assert bad.status_code == 401

    ok = client.post("/api/login", json={"name": "theo", "password": "s3cret"})
    assert ok.status_code == 200

    state = client.get("/api/state")
    assert state.status_code == 200
    assert state.get_json()["member"] == "theo"
    assert state.get_json()["niches"] == []
    assert state.get_json()["presets"] == []

    client.post("/api/logout")
    assert client.get("/api/state").status_code == 401


def test_index_served_without_login(client):
    assert client.get("/").status_code == 200
```

- [ ] **Step 2: Run** `uv run pytest tests/test_webui_auth.py -q` — Expected: FAIL (`create_app() got an unexpected keyword argument 'root'` ou 401 manquant)

- [ ] **Step 3: Implementation** — refactor de `webui.py` :

```python
# En tête de create_app(root=None) :
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
    ...
```

Puis remplacer dans TOUS les endpoints existants les constantes module (`TRACKS_DIR`, `QUEUE_DIR`, `LINKS_PATH`, `PLAN_PATH`, `SETTINGS_PATH`, `TOKENS_DIR`) par `paths[...]`, et dans `/api/state` ajouter :

```python
        conn = get_conn()
        try:
            niches = dbmod.list_niches(conn)
            presets = dbmod.list_presets(conn)
        finally:
            conn.close()
        # dans le jsonify : "member": session["member"],
        #                   "niches": niches, "presets": presets,
```

Note : `load_settings()` lit `beatsync.SETTINGS_PATH` global — passer le chemin : `load_settings(paths["settings"])` dans `/api/state`, et écrire `paths["settings"]` dans `save_settings`.

- [ ] **Step 4: Run** `uv run pytest -q` — Expected: toute la suite passe

- [ ] **Step 5: Commit**

```bash
git add webui.py tests/test_webui_auth.py
git commit -m "feat(platform): login par membre + create_app injectable"
```

---

### Task 9: webui — API niches, presets, clips par niche

**Files:**
- Modify: `webui.py`
- Test: `tests/test_webui_platform.py`

**Interfaces:**
- Consumes: db (Tasks 3-4), `fetch_tracks --video` (Task 7), jobs existants (`start_job`).
- Produces: `POST /api/niches` (JSON champs de create_niche) ; `PATCH /api/niches/<id>` ; `DELETE /api/niches/<id>` (DB seulement, fichiers conservés) ; `POST /api/niches/<id>/clips` (multipart, extensions vidéo) ; `POST /api/niches/<id>/links {text}` (écrit links.txt de la niche) ; `POST /api/niches/<id>/download` (job `fetch_tracks <links> --video --dest <clips_dir>`) ; `POST /api/presets` / `PATCH /api/presets/<id>` / `DELETE /api/presets/<id>`. `/api/state` : chaque niche gagne `clips: [{name, size_mb}]` et `links: str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_webui_platform.py
import io

import pytest

from db import add_member, connect
from webui import create_app


@pytest.fixture
def client(tmp_path):
    conn = connect(tmp_path / "platform.db")
    add_member(conn, "theo", "s3cret")
    conn.close()
    app = create_app(root=tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    client.post("/api/login", json={"name": "theo", "password": "s3cret"})
    return client


def test_niche_crud_via_api(client, tmp_path):
    created = client.post("/api/niches", json={
        "name": "Naruto Edits", "cadence": 2, "hashtags": ["naruto"]})
    assert created.status_code == 200
    nid = created.get_json()["id"]
    assert (tmp_path / "data/niches/naruto-edits/clips").is_dir()

    client.patch(f"/api/niches/{nid}", json={"cadence": 3})
    state = client.get("/api/state").get_json()
    assert state["niches"][0]["cadence"] == 3
    assert state["niches"][0]["clips"] == []

    assert client.delete(f"/api/niches/{nid}").status_code == 200
    assert client.get("/api/state").get_json()["niches"] == []


def test_niche_clip_upload_and_links(client, tmp_path):
    nid = client.post("/api/niches", json={"name": "Gym"}).get_json()["id"]

    upload = client.post(f"/api/niches/{nid}/clips", data={
        "file": (io.BytesIO(b"fake video"), "extrait.mp4")},
        content_type="multipart/form-data")
    assert upload.status_code == 200
    assert (tmp_path / "data/niches/gym/clips/extrait.mp4").read_bytes() == b"fake video"

    bad = client.post(f"/api/niches/{nid}/clips", data={
        "file": (io.BytesIO(b"x"), "notes.txt")},
        content_type="multipart/form-data")
    assert bad.status_code == 400

    client.post(f"/api/niches/{nid}/links", json={"text": "https://youtu.be/xyz\n"})
    assert (tmp_path / "data/niches/gym/links.txt").read_text().startswith("https://")
    state = client.get("/api/state").get_json()
    assert state["niches"][0]["links"].startswith("https://")
    assert state["niches"][0]["clips"][0]["name"] == "extrait.mp4"


def test_preset_crud_via_api(client):
    created = client.post("/api/presets", json={
        "name": "strobo", "overrides": {"cut_every": 1}})
    pid = created.get_json()["id"]
    state = client.get("/api/state").get_json()
    assert state["presets"] == [{"id": pid, "name": "strobo",
                                 "overrides": {"cut_every": 1}}]
    client.patch(f"/api/presets/{pid}", json={"name": "strobo hard",
                                              "overrides": {"cut_every": 2}})
    assert client.get("/api/state").get_json()["presets"][0]["name"] == "strobo hard"
    client.delete(f"/api/presets/{pid}")
    assert client.get("/api/state").get_json()["presets"] == []
```

- [ ] **Step 2: Run** `uv run pytest tests/test_webui_platform.py -q` — Expected: FAIL (404 sur /api/niches)

- [ ] **Step 3: Implementation** — dans `create_app`, après les endpoints existants :

```python
    VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}

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
```

Et dans `/api/state`, enrichir chaque niche :

```python
        for niche in niches:
            clips_dir = dbmod.niche_clips_dir(paths["data"], niche["slug"])
            niche["clips"] = sorted(
                ({"name": p.name, "size_mb": round(p.stat().st_size / 1e6, 1)}
                 for p in clips_dir.glob("*") if p.suffix.lower() in VIDEO_EXTS),
                key=lambda c: c["name"]) if clips_dir.is_dir() else []
            links = dbmod.niche_links_path(paths["data"], niche["slug"])
            niche["links"] = links.read_text() if links.is_file() else ""
```

(`VIDEO_EXTS` doit être défini avant `/api/state` dans `create_app`.)

- [ ] **Step 4: Run** `uv run pytest -q` — Expected: toute la suite passe

- [ ] **Step 5: Commit**

```bash
git add webui.py tests/test_webui_platform.py
git commit -m "feat(platform): API niches/presets/clips par niche"
```

---

### Task 10: UI — login + onglets Niches et Presets

**Files:**
- Modify: `templates/index.html`

Pas de test automatisé (UI) — validation manuelle en Step 3. Structure à ajouter :

- [ ] **Step 1: Login overlay + gestion 401**

```html
<div id="login" style="display:none;position:fixed;inset:0;background:var(--bg);
     z-index:10;display:flex;align-items:center;justify-content:center">
  <div class="card" style="width:280px">
    <h2>Dancing Dead</h2>
    <input id="l-name" placeholder="membre" style="width:100%;margin:.3rem 0">
    <input id="l-pass" type="password" placeholder="mot de passe" style="width:100%;margin:.3rem 0">
    <button class="act" style="width:100%" onclick="doLogin()">Entrer</button>
    <div id="l-status" class="err"></div>
  </div>
</div>
```

```js
async function doLogin() {
  try {
    await api("/api/login", j({name: gv("l-name"), password: gv("l-pass")}));
    document.getElementById("login").style.display = "none";
    refresh();
  } catch (e) { document.getElementById("l-status").textContent = e.message; }
}
const gv = id => document.getElementById(id).value;
// Dans api() : si r.status === 401 → afficher #login et throw.
// Nav : afficher le nom du membre (STATE.member) + bouton Déconnexion → POST /api/logout puis reload.
```

- [ ] **Step 2: Onglets Niches et Presets**

Nav : `<button data-tab="niches">Niches</button>` + `<button data-tab="presets">Presets</button>`.

Section Niches : tableau (nom, owner, cadence, nb clips) + bouton « + Nouvelle niche » (prompt nom) ; panneau de détail par niche sélectionnée : champs cadence / caption_template / hashtags (virgules) / presets (cases à cocher sur STATE.presets) / subtitles (case enabled + textarea preprompt) avec bouton Enregistrer (PATCH) ; liste des clips ; upload de clip (input file + POST multipart) ; textarea liens YouTube + « Télécharger les clips » (POST links puis POST download, suivi via followJob).

Section Presets : liste + « + Nouveau preset » ; éditeur réutilisant les MÊMES contrôles que l'onglet Réglages (effets/accents/cadrage/rythme) mais préfixés `p-` + champ nom, Enregistrer → POST ou PATCH `/api/presets`.

Render appelé depuis `refresh()` : `renderNiches()` et `renderPresets()` sur `STATE.niches` / `STATE.presets`.

- [ ] **Step 3: Validation manuelle**

```bash
uv run python db.py add-member theo
uv run python webui.py &
# navigateur : http://127.0.0.1:8765
# - refus sans login, login ok, logout
# - créer niche "Naruto", uploader un clip, coller un lien, Télécharger, log visible
# - créer preset "strobo" (cut fixe 1), l'associer à la niche
# curl -s http://127.0.0.1:8765/api/state → 401 (pas de cookie) ✓
```

- [ ] **Step 4: Commit**

```bash
git add templates/index.html
git commit -m "feat(platform): UI login + onglets niches/presets"
```

---

### Task 11: validation d'ensemble, docs, push

**Files:**
- Modify: `README.md`, `CLAUDE.md`

- [ ] **Step 1:** `uv run pytest -q` — toute la suite verte.
- [ ] **Step 2:** Rejouer le flux complet de la Task 10 Step 3 sur données réelles (uploader un vrai clip d'animé dans une niche, télécharger un lien réel, vérifier que le scan cache se remplit : `ls data/cache/scan/` après une génération future — pour l'instant vérifier seulement l'upload/download).
- [ ] **Step 3:** README : section « Plateforme (phase 1) » — création de membres (`uv run python db.py add-member <nom>`), login, niches, presets. CLAUDE.md : `db.py` (module persistance, toutes fonctions pures testées), `create_app(root)` injectable, cache de scan `scan_clips(cache_dir=)`, `fetch_tracks --video`.
- [ ] **Step 4:** Commit + push :

```bash
git add README.md CLAUDE.md
git commit -m "docs: plateforme phase 1"
git push
```
