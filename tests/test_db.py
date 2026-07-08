import sqlite3
from pathlib import Path

import pytest

from db import (add_member, connect, create_preset, delete_preset,
                effective_config, list_members, list_presets, slugify,
                update_preset, verify_member)


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
