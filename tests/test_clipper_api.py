import io

import pytest

from db import add_member, connect, create_clipper_clip, create_clipper_source
from webui import coerce_clipper, create_app


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


def test_coerce_borne_les_valeurs():
    out = coerce_clipper({"clip_count": "3", "min_dur": "20", "max_dur": "45"})
    assert out == {"clip_count": 3, "min_dur": 20.0, "max_dur": 45.0}
    assert coerce_clipper({"clip_count": 999})["clip_count"] == 30
    assert coerce_clipper({"min_dur": 0})["min_dur"] == 3.0


def test_coerce_refuse_le_non_numerique():
    with pytest.raises(ValueError):
        coerce_clipper({"clip_count": "<script>"})


def test_coerce_refuse_un_modele_whisper_inconnu():
    with pytest.raises(ValueError):
        coerce_clipper({"whisper_model": "'; DROP TABLE"})


def test_coerce_ignore_les_cles_inconnues():
    assert coerce_clipper({"inconnu": 1}) == {}


def test_upload_puis_liste(client, tmp_path):
    up = client.post("/api/clipper/sources", data={
        "file": (io.BytesIO(b"faux mp4"), "Interview Kernel.mp4")})
    assert up.status_code == 200
    sid = up.get_json()["id"]
    assert (tmp_path / "data/clipper/interview-kernel/source.mp4").is_file()

    sources = client.get("/api/state").get_json()["clipper_sources"]
    assert [s["id"] for s in sources] == [sid]
    assert sources[0]["status"] == "pending"
    assert sources[0]["clips"] == []


def test_upload_refuse_une_extension_non_video(client):
    up = client.post("/api/clipper/sources", data={
        "file": (io.BytesIO(b"x"), "notes.txt")})
    assert up.status_code == 400


def test_suppression_efface_le_dossier(client, tmp_path):
    sid = client.post("/api/clipper/sources", data={
        "file": (io.BytesIO(b"faux"), "Live.mp4")}).get_json()["id"]
    assert client.delete(f"/api/clipper/sources/{sid}").status_code == 200
    assert not (tmp_path / "data/clipper/live").exists()
    assert client.get("/api/state").get_json()["clipper_sources"] == []


def test_lecture_et_statut_d_un_clip(client, tmp_path):
    sid = client.post("/api/clipper/sources", data={
        "file": (io.BytesIO(b"faux"), "Live.mp4")}).get_json()["id"]
    clip_file = tmp_path / "data/clipper/live/clips/01-a.mp4"
    clip_file.parent.mkdir(parents=True, exist_ok=True)
    clip_file.write_bytes(b"clip")
    conn = connect(tmp_path / "platform.db")
    cid = create_clipper_clip(
        conn, source_id=sid, start=0.0, end=30.0, title="A", hook=80, flow=70,
        value=60, score=71.0, why="ok",
        file=str(clip_file.relative_to(tmp_path)))
    conn.close()

    assert client.get(f"/api/clipper/clips/{cid}").data == b"clip"
    assert client.post(f"/api/clipper/clips/{cid}/status",
                       json={"status": "approved"}).status_code == 200
    state = client.get("/api/state").get_json()["clipper_sources"][0]
    assert state["clips"][0]["status"] == "approved"

    assert client.post(f"/api/clipper/clips/{cid}/status",
                       json={"status": "n'importe quoi"}).status_code == 400

    assert client.delete(f"/api/clipper/clips/{cid}").status_code == 200
    assert not clip_file.is_file()


def test_un_clip_hors_de_data_n_est_pas_servi(client, tmp_path):
    """Garde anti-traversal : une ligne trafiquée ne doit pas exfiltrer un
    fichier arbitraire de la machine."""
    secret = tmp_path.parent / "secret.mp4"
    secret.write_bytes(b"secret")
    sid = client.post("/api/clipper/sources", data={
        "file": (io.BytesIO(b"faux"), "Live.mp4")}).get_json()["id"]
    conn = connect(tmp_path / "platform.db")
    cid = create_clipper_clip(
        conn, source_id=sid, start=0, end=30, title="X", hook=0, flow=0,
        value=0, score=0, why="", file="../secret.mp4")
    conn.close()
    assert client.get(f"/api/clipper/clips/{cid}").status_code == 404


def test_un_clip_hors_de_data_n_est_pas_efface(client, tmp_path):
    """Pendant de test_un_clip_hors_de_data_n_est_pas_servi, cote suppression."""
    secret = tmp_path.parent / "secret-a-garder.mp4"
    secret.write_bytes(b"secret")
    sid = client.post("/api/clipper/sources", data={
        "file": (io.BytesIO(b"faux"), "Live.mp4")}).get_json()["id"]
    conn = connect(tmp_path / "platform.db")
    cid = create_clipper_clip(
        conn, source_id=sid, start=0, end=30, title="X", hook=0, flow=0,
        value=0, score=0, why="", file="../secret-a-garder.mp4")
    conn.close()

    client.delete(f"/api/clipper/clips/{cid}")
    assert secret.is_file()      # la ligne part, le fichier hors de data/ reste


def test_clip_inconnu(client):
    assert client.get("/api/clipper/clips/999").status_code == 404
    assert client.post("/api/clipper/clips/999/status",
                       json={"status": "approved"}).status_code == 404


def test_reglages_clipper_persistes(client, tmp_path):
    import json
    assert client.post("/api/settings", json={
        "clipper": {"clip_count": "4", "min_dur": "20"}}).status_code == 200
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert saved["clipper"]["clip_count"] == 4


def test_reglages_clipper_invalides_rejetes(client):
    assert client.post("/api/settings", json={
        "clipper": {"clip_count": "<img onerror=x>"}}).status_code == 400


@pytest.mark.parametrize("valeur", ["clip_count", ["clip_count"], 42])
def test_reglages_clipper_non_objet_rejetes(client, valeur):
    """400, pas 500 : le contrat est « coercion serveur », pas « trace »."""
    assert client.post("/api/settings", json={"clipper": valeur}).status_code == 400


def test_analyse_d_une_source_inconnue(client):
    assert client.post("/api/clipper/sources/999/run").status_code == 404


@pytest.mark.parametrize("url", [
    "https://youtu.be/abc\n--exec\nbash -c 'id'",   # injection d'options yt-dlp
    "https://www.youtube.com/watch?v=a\n-o\n/tmp/x",
    "https://www.youtube.com/watch?v=a --exec id",
    "https://www.youtube.com/watch?v=a\ttruc",
])
def test_import_youtube_refuse_une_url_avec_espacement(client, url):
    """Chaque ligne du fichier de liens devient un argument positionnel de
    yt-dlp : un retour a la ligne suffit a injecter --exec."""
    assert client.post("/api/clipper/sources/link", json={"url": url}).status_code == 400


@pytest.mark.parametrize("url", [
    "https://www.youtube.com.attaquant.tld/watch?v=a",
    "http://www.youtube.com/watch?v=a",
    "https://vimeo.com/123",
    "",
])
def test_import_youtube_refuse_un_hote_non_youtube(client, url):
    assert client.post("/api/clipper/sources/link", json={"url": url}).status_code == 400


def test_import_youtube_accepte_une_url_normale(client, tmp_path, monkeypatch):
    """Chemin nominal : l'URL est ecrite dans le fichier de liens et le job part."""
    import webui
    monkeypatch.setattr(webui, "start_job", lambda name, argv: "job42")
    reponse = client.post("/api/clipper/sources/link",
                          json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    assert reponse.status_code == 200
    assert reponse.get_json()["job_id"] == "job42"
    liens = (tmp_path / "data" / "clipper" / "_inbox" / "links.txt").read_text()
    assert liens.strip() == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
