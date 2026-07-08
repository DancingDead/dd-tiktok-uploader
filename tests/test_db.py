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
