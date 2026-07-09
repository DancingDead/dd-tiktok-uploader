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


def test_delete_video_removes_row_and_file(client, tmp_path):
    from db import connect, create_niche, create_video, list_videos
    conn = connect(tmp_path / "platform.db")
    nid = create_niche(conn, tmp_path / "data", "Lib")
    vfile = tmp_path / "data" / "niches" / "lib" / "videos" / "v.mp4"
    vfile.parent.mkdir(parents=True, exist_ok=True)
    vfile.write_bytes(b"fake mp4")
    vid = create_video(conn, niche_id=nid, track="tracks/x.wav", seed=1,
                       file="data/niches/lib/videos/v.mp4",
                       created_at="2026-07-09T12:00:00")
    conn.close()

    assert client.delete(f"/api/videos/{vid}").status_code == 200
    assert not vfile.exists()                 # fichier effacé du disque
    conn = connect(tmp_path / "platform.db")
    assert list_videos(conn) == []            # ligne supprimée en base
    conn.close()

    assert client.delete(f"/api/videos/{vid}").status_code == 404  # id inconnu
