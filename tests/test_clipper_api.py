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


def test_promotion_refuse_un_fichier_sans_piste_audio(client, tmp_path, monkeypatch):
    """Une source muette est condamnée d'avance : mieux vaut refuser à la
    promotion que créer une source qui échouera à l'analyse en accusant le
    contenu alors que le problème est le téléchargement."""
    import clipper

    inbox = tmp_path / "data" / "clipper" / "_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "muet.mp4").write_bytes(b"faux")
    monkeypatch.setattr(clipper, "has_audio", lambda p: False)

    reponse = client.post("/api/clipper/inbox/muet.mp4")
    assert reponse.status_code == 400
    assert "audio" in reponse.get_json()["error"]
    assert client.get("/api/state").get_json()["clipper_sources"] == []


def test_promotion_accepte_un_fichier_avec_audio(client, tmp_path, monkeypatch):
    import clipper

    inbox = tmp_path / "data" / "clipper" / "_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "parle.mp4").write_bytes(b"faux")
    monkeypatch.setattr(clipper, "has_audio", lambda p: True)
    monkeypatch.setattr(clipper, "probe_duration", lambda p: 120.0)

    assert client.post("/api/clipper/inbox/parle.mp4").status_code == 200
    assert (tmp_path / "data/clipper/parle/source.mp4").is_file()


@pytest.mark.parametrize("statut", ["transcribing", "analyzing", "rendering"])
def test_suppression_refusee_pendant_une_analyse(client, tmp_path, statut):
    """Le job survivrait à la suppression : il recréerait les dossiers et
    écrirait des mp4 sans ligne en base, invisibles et jamais nettoyés."""
    from db import set_clipper_source_status

    sid = client.post("/api/clipper/sources", data={
        "file": (io.BytesIO(b"faux"), "Podcast.mp4")}).get_json()["id"]
    conn = connect(tmp_path / "platform.db")
    set_clipper_source_status(conn, sid, statut)
    conn.close()

    assert client.delete(f"/api/clipper/sources/{sid}").status_code == 409
    assert (tmp_path / "data/clipper/podcast/source.mp4").is_file()
    assert len(client.get("/api/state").get_json()["clipper_sources"]) == 1


def test_lancer_l_analyse_pose_le_statut_avant_que_le_sous_processus_ecrive(
        client, tmp_path, monkeypatch):
    """Le statut `transcribing` est posé par start_job, l'endpoint qui lance
    le job — pas par le sous-processus, qui ne l'écrit qu'à sa première étape.
    Sans ça, une suppression lancée juste après « Analyser » passe à travers
    la garde 409 (fenêtre entre le démarrage du job et l'écriture différée)."""
    import webui
    monkeypatch.setattr(webui, "start_job", lambda name, argv: "job42")

    sid = client.post("/api/clipper/sources", data={
        "file": (io.BytesIO(b"faux"), "Podcast.mp4")}).get_json()["id"]

    reponse = client.post(f"/api/clipper/sources/{sid}/run")
    assert reponse.status_code == 200
    assert reponse.get_json()["job_id"] == "job42"

    sources = client.get("/api/state").get_json()["clipper_sources"]
    assert sources[0]["status"] == "transcribing"

    assert client.delete(f"/api/clipper/sources/{sid}").status_code == 409


def test_un_dossier_orphelin_ne_voit_pas_son_slug_reattribue(client, tmp_path):
    """Un effacement de dossier qui échoue (handle ouvert sous Windows) laisse
    un dossier sans ligne en base. Réattribuer son slug ferait hériter la
    nouvelle source du transcript.json de l'ancienne : « Transcript en cache »,
    puis des clips découpés aux timestamps d'une autre vidéo."""
    orphelin = tmp_path / "data" / "clipper" / "podcast"
    orphelin.mkdir(parents=True)
    (orphelin / "transcript.json").write_text('[{"word": "vieux"}]')

    client.post("/api/clipper/sources", data={
        "file": (io.BytesIO(b"faux"), "Podcast.mp4")})
    slug = client.get("/api/state").get_json()["clipper_sources"][0]["slug"]
    assert slug != "podcast"
    assert (orphelin / "transcript.json").read_text() == '[{"word": "vieux"}]'


def test_suppression_ne_perd_pas_la_ligne_si_l_effacement_echoue(
        client, tmp_path, monkeypatch):
    """Ordre inverse de l'ancien code : le dossier part avant la ligne, sinon
    un échec d'effacement laisse un orphelin et une source disparue."""
    import shutil

    sid = client.post("/api/clipper/sources", data={
        "file": (io.BytesIO(b"faux"), "Podcast.mp4")}).get_json()["id"]

    def refuse(*a, **k):
        raise PermissionError("fichier ouvert")
    monkeypatch.setattr(shutil, "rmtree", refuse)

    reponse = client.delete(f"/api/clipper/sources/{sid}")
    assert reponse.status_code == 409
    assert len(client.get("/api/state").get_json()["clipper_sources"]) == 1


def test_coerce_refuse_min_dur_superieur_a_max_dur():
    """Une inversion donne 0 clip et un `failed` qui accuse le recalage : on la
    refuse à la saisie, là où elle est encore compréhensible."""
    with pytest.raises(ValueError):
        coerce_clipper({"min_dur": 90, "max_dur": 30})
    # Bornes égales : légitime (tous les clips font exactement cette durée).
    assert coerce_clipper({"min_dur": 30, "max_dur": 30})["min_dur"] == 30.0


def test_import_youtube_refuse_une_url_non_chaine(client):
    """400, pas 500 : c'est le seul endpoint qui alimente une ligne de commande."""
    assert client.post("/api/clipper/sources/link",
                       json={"url": 42}).status_code == 400


def test_import_youtube_accepte_un_hote_en_majuscules(client, monkeypatch):
    """L'hôte est insensible à la casse : une URL collée depuis la barre
    d'adresse ne doit pas être rejetée à tort."""
    import webui
    monkeypatch.setattr(webui, "start_job", lambda name, argv: "job42")
    assert client.post("/api/clipper/sources/link", json={
        "url": "https://WWW.YouTube.com/watch?v=dQw4w9WgXcQ"}).status_code == 200


def test_etat_n_expose_pas_les_chemins_disque(client, tmp_path):
    """`path` et `file` décrivent l'arborescence de la machine : ils n'ont rien
    à faire dans une réponse HTTP, comme pour les vidéos des niches."""
    sid = client.post("/api/clipper/sources", data={
        "file": (io.BytesIO(b"faux"), "Live.mp4")}).get_json()["id"]
    conn = connect(tmp_path / "platform.db")
    create_clipper_clip(conn, source_id=sid, start=0.0, end=30.0, title="A",
                        hook=1, flow=1, value=1, score=1.0, why="",
                        file="data/clipper/live/clips/01-a.mp4")
    conn.close()
    source = client.get("/api/state").get_json()["clipper_sources"][0]
    assert "path" not in source
    assert "file" not in source["clips"][0]
    assert source["clips"][0]["title"] == "A"


def test_la_duree_de_la_source_est_renseignee(client, tmp_path, monkeypatch):
    import clipper
    monkeypatch.setattr(clipper, "probe_duration", lambda p: 3600.0)
    client.post("/api/clipper/sources", data={
        "file": (io.BytesIO(b"faux"), "Live.mp4")})
    assert client.get("/api/state").get_json()["clipper_sources"][0]["duration"] \
        == 3600.0


def test_une_sonde_de_duree_qui_echoue_ne_bloque_pas_l_upload(client, monkeypatch):
    import clipper

    def boom(path):
        raise RuntimeError("ffprobe absent")
    monkeypatch.setattr(clipper, "probe_duration", boom)
    assert client.post("/api/clipper/sources", data={
        "file": (io.BytesIO(b"faux"), "Live.mp4")}).status_code == 200
