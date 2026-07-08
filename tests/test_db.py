import sqlite3
from pathlib import Path

import pytest

from db import connect, slugify, add_member, list_members, verify_member


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
