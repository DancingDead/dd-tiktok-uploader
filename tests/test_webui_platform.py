import io

import pytest

from db import add_member, connect
from webui import coerce_overrides, coerce_subtitles, create_app


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


def test_shared_clip_catalog_and_niche_selection(client, tmp_path):
    # upload d'un clip dans le catalogue partagé
    upload = client.post("/api/clips", data={
        "file": (io.BytesIO(b"fake video"), "extrait.mp4")},
        content_type="multipart/form-data")
    assert upload.status_code == 200
    assert (tmp_path / "clips" / "extrait.mp4").read_bytes() == b"fake video"

    bad = client.post("/api/clips", data={
        "file": (io.BytesIO(b"x"), "notes.txt")},
        content_type="multipart/form-data")
    assert bad.status_code == 400

    # le clip apparaît dans le catalogue partagé (state["clips"]), pas dans une niche
    state = client.get("/api/state").get_json()
    assert state["clips"][0]["name"] == "extrait.mp4"

    # une niche sélectionne le clip (aucun import dans la niche)
    nid = client.post("/api/niches", json={"name": "Gym"}).get_json()["id"]
    client.patch(f"/api/niches/{nid}", json={"clips": ["clips/extrait.mp4"]})
    state = client.get("/api/state").get_json()
    assert state["niches"][0]["clips"] == ["clips/extrait.mp4"]

    # liens YouTube du catalogue de clips
    client.post("/api/clip-links", json={"text": "https://youtu.be/xyz\n"})
    assert (tmp_path / "clip_links.txt").read_text().startswith("https://")
    assert client.get("/api/state").get_json()["clip_links"].startswith("https://")


def test_shared_clip_catalog_accepts_images(client, tmp_path):
    """Les images vivent dans clips/ comme les vidéos : même upload, même
    listing, même suppression."""
    upload = client.post("/api/clips", data={
        "file": (io.BytesIO(b"fake png"), "affiche.png")},
        content_type="multipart/form-data")
    assert upload.status_code == 200
    assert (tmp_path / "clips/affiche.png").is_file()

    names = [c["name"] for c in client.get("/api/state").get_json()["clips"]]
    assert "affiche.png" in names

    assert client.get("/api/clips/affiche.png").status_code == 200
    assert client.delete("/api/clips/affiche.png").status_code == 200
    assert not (tmp_path / "clips/affiche.png").is_file()


def test_shared_clip_catalog_still_rejects_unknown_formats(client):
    refused = client.post("/api/clips", data={
        "file": (io.BytesIO(b"nope"), "notes.txt")},
        content_type="multipart/form-data")
    assert refused.status_code == 400


def test_delete_catalog_asset_removes_file_and_niche_selection(client, tmp_path):
    # deux clips dans le catalogue partagé
    for fname in ("a.mp4", "b.mp4"):
        client.post("/api/clips", data={"file": (io.BytesIO(b"v"), fname)},
                    content_type="multipart/form-data")
    # une niche sélectionne les deux
    nid = client.post("/api/niches", json={"name": "Gym"}).get_json()["id"]
    client.patch(f"/api/niches/{nid}", json={"clips": ["clips/a.mp4", "clips/b.mp4"]})

    # suppression de a.mp4 : fichier effacé + retiré de la sélection de la niche
    assert client.delete("/api/clips/a.mp4").status_code == 200
    assert not (tmp_path / "clips" / "a.mp4").exists()
    assert (tmp_path / "clips" / "b.mp4").exists()
    state = client.get("/api/state").get_json()
    assert [c["name"] for c in state["clips"]] == ["b.mp4"]
    assert state["niches"][0]["clips"] == ["clips/b.mp4"]

    # id inconnu → 404, format non supporté → 400, traversée neutralisée → 404
    assert client.delete("/api/clips/a.mp4").status_code == 404
    assert client.delete("/api/clips/notes.txt").status_code == 400
    assert client.delete("/api/clips/..%2f..%2fsecret.mp4").status_code == 404


def test_delete_track_removes_file(client, tmp_path):
    client.post("/api/tracks", data={"file": (io.BytesIO(b"a"), "song.mp3")},
                content_type="multipart/form-data")
    assert (tmp_path / "tracks" / "song.mp3").exists()
    assert client.delete("/api/tracks/song.mp3").status_code == 200
    assert not (tmp_path / "tracks" / "song.mp3").exists()
    assert client.get("/api/state").get_json()["tracks"] == []


def test_generate_passes_root_to_job(client, tmp_path, monkeypatch):
    import webui
    captured = {}
    monkeypatch.setattr(webui, "start_job",
                        lambda name, argv: (captured.update(argv=argv) or "job1"))
    client.post("/api/tracks", data={"file": (io.BytesIO(b"a"), "s.mp3")},
                content_type="multipart/form-data")
    client.post("/api/clips", data={"file": (io.BytesIO(b"v"), "c.mp4")},
                content_type="multipart/form-data")
    nid = client.post("/api/niches", json={"name": "N"}).get_json()["id"]
    client.patch(f"/api/niches/{nid}", json={"tracks": ["tracks/s.mp3"], "clips": ["clips/c.mp4"]})

    r = client.post(f"/api/niches/{nid}/generate", json={"count": 1})
    assert r.status_code == 200
    # generate_niche.py doit recevoir le root de l'instance (sinon il ouvre la
    # mauvaise base et croit la niche vide) : argv = [py, script, id, count, root]
    assert captured["argv"][-1] == str(tmp_path)


def test_generate_requires_son_then_clip(client, tmp_path):
    # upload d'un son et d'un clip dans le catalogue
    client.post("/api/tracks", data={"file": (io.BytesIO(b"a"), "s.mp3")},
                content_type="multipart/form-data")
    client.post("/api/clips", data={"file": (io.BytesIO(b"v"), "c.mp4")},
                content_type="multipart/form-data")
    nid = client.post("/api/niches", json={"name": "N"}).get_json()["id"]

    # aucun son → message qui parle de « son » (pas juste « morceau »)
    r = client.post(f"/api/niches/{nid}/generate", json={"count": 1})
    assert r.status_code == 400
    assert "son" in r.get_json()["error"].lower()

    # un clip mais toujours aucun son → même garde (le clip ne suffit pas)
    client.patch(f"/api/niches/{nid}", json={"clips": ["clips/c.mp4"]})
    r = client.post(f"/api/niches/{nid}/generate", json={"count": 1})
    assert r.status_code == 400
    assert "son" in r.get_json()["error"].lower()

    # un son mais aucun clip → garde sur les clips
    client.patch(f"/api/niches/{nid}", json={"tracks": ["tracks/s.mp3"], "clips": []})
    r = client.post(f"/api/niches/{nid}/generate", json={"count": 1})
    assert r.status_code == 400
    assert "clip" in r.get_json()["error"].lower()


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


def test_coerce_overrides_numeric_ambiance_keys():
    out = coerce_overrides({"grain": "0.5", "clip_speed": "0.85"})
    assert out["grain"] == 0.5
    assert out["clip_speed"] == 0.85


def test_coerce_overrides_glitch_number_in_accents():
    out = coerce_overrides({"accents": {"rgb": True, "glitch": "0.35"}})
    assert out["accents"]["glitch"] == 0.35


def test_coerce_overrides_glitch_bool_preserved():
    out = coerce_overrides({"accents": {"glitch": True}})
    assert out["accents"]["glitch"] is True  # bool inchangé, coercé plus tard côté moteur


def test_coerce_overrides_rejects_unknown_color_grade():
    with pytest.raises(ValueError):
        coerce_overrides({"color_grade": "arc-en-ciel"})


def test_coerce_overrides_accepts_known_color_grade():
    assert coerce_overrides({"color_grade": "froid"})["color_grade"] == "froid"


def test_coerce_accepts_valid_section():
    assert coerce_overrides({"section": "calm"})["section"] == "calm"
    assert coerce_overrides({"section": "drop"})["section"] == "drop"


def test_coerce_rejects_unknown_section():
    with pytest.raises(ValueError):
        coerce_overrides({"section": "chill"})


def test_coerce_overrides_accepts_known_formats():
    for fmt in ("vertical", "carre"):
        assert coerce_overrides({"format": fmt})["format"] == fmt


def test_coerce_overrides_rejects_unknown_format():
    with pytest.raises(ValueError):
        coerce_overrides({"format": "panoramique"})


def test_coerce_overrides_clamps_end_scene_values():
    out = coerce_overrides({"end_scene": {"enabled": True, "beats": "64",
                                          "freeze": "9.0", "speed": "0.1"}})
    assert out["end_scene"]["beats"] == 32
    assert out["end_scene"]["freeze"] == pytest.approx(3.0)
    assert out["end_scene"]["speed"] == pytest.approx(0.5)
    assert out["end_scene"]["enabled"] is True


def test_coerce_overrides_rejects_non_numeric_end_scene():
    with pytest.raises(ValueError):
        coerce_overrides({"end_scene": {"beats": "beaucoup"}})


def test_coerce_overrides_speed_ramp_interpolate_stays_boolean():
    """`interpolate` est un booléen : il ne doit pas être converti en nombre,
    contrairement aux autres clés numériques du bloc."""
    out = coerce_overrides({"speed_ramp": {"interpolate": False}})
    assert out["speed_ramp"]["interpolate"] is False
    out2 = coerce_overrides({"speed_ramp": {"interpolate": True}})
    assert out2["speed_ramp"]["interpolate"] is True


def test_coerce_overrides_speed_ramp_without_interpolate_is_untouched():
    out = coerce_overrides({"speed_ramp": {"slow": 0.5}})
    assert out["speed_ramp"] == {"slow": 0.5}


def test_coerce_overrides_leaves_end_scene_absent_alone():
    assert "end_scene" not in coerce_overrides({"grain": 0.2})


def test_preset_with_format_and_end_scene_round_trips(client):
    created = client.post("/api/presets", json={
        "name": "Carré cinéma",
        "overrides": {"format": "carre",
                      "end_scene": {"enabled": True, "beats": 8,
                                    "freeze": 1.0, "speed": 0.5}}})
    assert created.status_code == 200
    presets = client.get("/api/state").get_json()["presets"]
    saved = next(p for p in presets if p["name"] == "Carré cinéma")
    assert saved["overrides"]["format"] == "carre"
    assert saved["overrides"]["end_scene"]["beats"] == 8


def test_preset_with_unknown_format_is_rejected(client):
    bad = client.post("/api/presets", json={
        "name": "Cassé", "overrides": {"format": "panoramique"}})
    assert bad.status_code == 400


def test_serve_catalog_asset_for_preview(client, tmp_path):
    client.post("/api/clips", data={"file": (io.BytesIO(b"fake video"), "extrait.mp4")},
                content_type="multipart/form-data")
    client.post("/api/tracks", data={"file": (io.BytesIO(b"fake audio"), "son.mp3")},
                content_type="multipart/form-data")

    # aperçu : le fichier est servi tel quel, avec un type MIME média
    r = client.get("/api/clips/extrait.mp4")
    assert r.status_code == 200
    assert r.data == b"fake video"
    assert r.headers["Content-Type"].startswith("video/")

    r = client.get("/api/tracks/son.mp3")
    assert r.status_code == 200
    assert r.data == b"fake audio"
    assert r.headers["Content-Type"].startswith("audio/")

    # absent -> 404 ; format non supporté -> 400 ; traversée de chemin -> 404
    assert client.get("/api/clips/absent.mp4").status_code == 404
    assert client.get("/api/tracks/notes.txt").status_code == 400
    assert client.get("/api/clips/..%2f..%2fsecret.mp4").status_code == 404


def test_video_poster_unknown_returns_404(client):
    assert client.get("/api/videos/99999/poster").status_code == 404


def test_coerce_clamps_out_of_range_numbers():
    c = coerce_overrides({"min_presence": 50, "cut_every": 0, "clip_speed": 9, "buildup": -5})
    assert c["min_presence"] == 1.0
    assert c["cut_every"] == 1
    assert c["clip_speed"] == 1.5
    assert c["buildup"] == 0.0


def test_create_preset_duplicate_name_conflicts(client):
    assert client.post("/api/presets", json={"name": "strobo", "overrides": {}}).status_code == 200
    r = client.post("/api/presets", json={"name": "strobo", "overrides": {}})
    assert r.status_code == 409
    assert "existe déjà" in r.get_json()["error"]


def test_coerce_subtitles_forces_numeric_fields():
    out = coerce_subtitles({"mode": "fixe", "text": "SALUT", "x": "0.25",
                            "y": "0.1", "size": "80"})
    assert out["x"] == pytest.approx(0.25)
    assert out["y"] == pytest.approx(0.1)
    assert out["size"] == 80


def test_coerce_subtitles_clamps_out_of_range_values():
    out = coerce_subtitles({"x": 5.0, "y": -2.0, "size": 10_000})
    assert out["x"] == pytest.approx(1.0)
    assert out["y"] == pytest.approx(0.0)
    assert out["size"] == 200


def test_coerce_subtitles_rejects_unknown_mode():
    with pytest.raises(ValueError):
        coerce_subtitles({"mode": "magique"})


def test_coerce_subtitles_rejects_non_numeric():
    with pytest.raises(ValueError):
        coerce_subtitles({"size": "gros"})


def test_patch_niche_rejects_invalid_subtitles(client):
    nid = client.post("/api/niches", json={"name": "Test", "cadence": 1}).get_json()["id"]
    bad = client.patch(f"/api/niches/{nid}", json={"subtitles": {"size": "gros"}})
    assert bad.status_code == 400


def test_create_niche_coerces_subtitles(client):
    """POST /api/niches doit coercer `subtitles` comme le fait PATCH — un client
    d'API qui envoie des champs mal formés dès la création ne doit pas
    contourner la validation en passant par le seul endpoint asymétrique."""
    bad = client.post("/api/niches", json={"name": "Test", "subtitles": {"size": "gros"}})
    assert bad.status_code == 400

    ok = client.post("/api/niches", json={"name": "Test2", "subtitles": {
        "mode": "fixe", "x": "0.25", "size": "72"}})
    assert ok.status_code == 200
    nid = ok.get_json()["id"]
    niches = client.get("/api/state").get_json()["niches"]
    subs = next(n["subtitles"] for n in niches if n["id"] == nid)
    assert subs["x"] == pytest.approx(0.25)
    assert subs["size"] == 72


def test_patch_niche_stores_a_fixed_caption(client):
    nid = client.post("/api/niches", json={"name": "Test", "cadence": 1}).get_json()["id"]
    ok = client.patch(f"/api/niches/{nid}", json={"subtitles": {
        "enabled": True, "mode": "fixe", "text": "LIEN EN BIO",
        "x": 0.5, "y": 0.2, "size": 72}})
    assert ok.status_code == 200
    subs = client.get("/api/state").get_json()["niches"][0]["subtitles"]
    assert subs["mode"] == "fixe"
    assert subs["text"] == "LIEN EN BIO"
    assert subs["size"] == 72
