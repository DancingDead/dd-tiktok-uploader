import sqlite3
from pathlib import Path

import pytest

from db import (add_member, connect, create_niche, create_preset,
                create_video, delete_niche, delete_preset, effective_config,
                get_niche, list_members, list_niches, list_presets,
                list_videos, niche_clips_dir, niche_links_path, set_video_status,
                slugify, update_niche, update_preset, verify_member)


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
    assert slugify("Édits Café Été") == "edits-cafe-ete"


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
    assert niche_links_path(tmp_path, "naruto-edits") == \
        tmp_path / "niches" / "naruto-edits" / "links.txt"

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


def test_delete_niche_cascades_videos(conn, tmp_path):
    nid = create_niche(conn, tmp_path, "Cascade Test")
    vid = create_video(conn, niche_id=nid, track="tracks/x.wav", seed=1,
                       file="data/niches/cascade-test/videos/v.mp4",
                       created_at="2026-07-08T12:00:00")
    delete_niche(conn, nid)
    assert get_niche(conn, nid) is None
    assert vid not in [v["id"] for v in list_videos(conn)]


def test_video_status_check_constraint(conn, tmp_path):
    nid = create_niche(conn, tmp_path, "Status Test")
    vid = create_video(conn, niche_id=nid, track="tracks/x.wav", seed=1,
                       file="data/niches/status-test/videos/v.mp4",
                       created_at="2026-07-08T12:00:00")
    with pytest.raises(sqlite3.IntegrityError):
        set_video_status(conn, vid, "bogus")
    set_video_status(conn, vid, "approved")
    assert list_videos(conn)[0]["status"] == "approved"
