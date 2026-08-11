"""Tests des helpers purs de generate_niche : plan de variantes, nom de fichier."""

from pathlib import Path

from generate_niche import main, plan_variants, video_stem


def test_plan_variants_deterministic_and_distinct_seeds():
    tracks = ["tracks/a.mp3", "tracks/b.wav"]
    a = plan_variants(tracks, 3, base_seed=42)
    b = plan_variants(tracks, 3, base_seed=42)
    assert a == b                                   # reproductible à base_seed égal
    assert len(a) == 3
    seeds = [s for _, s in a]
    assert len(set(seeds)) == 3                      # seeds distinctes → variantes distinctes
    assert all(t in tracks for t, _ in a)            # morceaux tirés parmi ceux de la niche


def test_plan_variants_different_base_seed_differs():
    tracks = ["tracks/a.mp3", "tracks/b.wav", "tracks/c.mp3"]
    assert plan_variants(tracks, 3, 1) != plan_variants(tracks, 3, 2)


def test_plan_variants_empty_tracks():
    assert plan_variants([], 3, base_seed=1) == []


def test_video_stem_filesystem_safe():
    stem = video_stem("naruto-edits", "tracks/NLCK & HANNAH - Virus V4 (Radio Edit).wav",
                      seed=123, created_at="2026-07-09T12:00:00")
    assert "/" not in stem and " " not in stem
    assert stem.endswith("_s123")
    assert "naruto-edits" in stem


# --- Régression : pas de repli sur le texte fixe quand les sous-titres sont
# désactivés (finding #3 de la revue finale) -------------------------------


def test_disabled_fixed_subtitles_do_not_leak_into_video_captions(tmp_path, monkeypatch):
    """Sous-titres désactivés (`enabled: False`) mais mode « fixe » + texte
    encore en state (l'UI masque le champ sans le vider) : la vidéo enregistrée
    ne doit porter AUCUNE caption, pas le texte fixe fantôme."""
    import beatsync
    import db as dbmod

    root = tmp_path
    (root / "clips").mkdir()
    (root / "clips" / "c.mp4").write_bytes(b"x")

    conn = dbmod.connect(root / "platform.db")
    dbmod.add_member(conn, "theo", "s3cret")
    niche_id = dbmod.create_niche(
        conn, root / "data", "N", owner="theo", cadence=1,
        tracks=["tracks/s.mp3"], clips=["clips/c.mp4"],
        subtitles={"enabled": False, "mode": "fixe", "text": "LIEN EN BIO"})
    conn.close()

    dummy_clip = {"path": Path(root / "clips" / "c.mp4"), "kind": "video",
                  "duration": 30.0, "width": 1920, "height": 1080, "ratio": 16 / 9}
    monkeypatch.setattr(beatsync, "load_clips", lambda folder: [dummy_clip])
    monkeypatch.setattr(beatsync, "scan_clips", lambda clips, cache_dir=None: None)
    monkeypatch.setattr(
        beatsync, "generate_video",
        lambda *a, **k: {"captions": []})  # subtitles désactivées → apply_subtitles ne pose rien

    monkeypatch.setattr("sys.argv", ["generate_niche.py", str(niche_id), "1", str(root)])
    main()

    conn = dbmod.connect(root / "platform.db")
    videos = dbmod.list_videos(conn, niche_id=niche_id)
    conn.close()
    assert len(videos) == 1
    assert videos[0]["subtitles"]["lines"] == []


def test_video_records_whether_a_punchline_was_expected(tmp_path, monkeypatch):
    """`lines: []` est ambigu : sous-titres coupés, ou LLM en échec ? Vu en prod
    — une variante sur trois est sortie SANS texte, indiscernable dans la
    bibliothèque d'une vidéo volontairement muette. On enregistre donc si du
    texte était attendu, pour que l'UI puisse le signaler."""
    import generate_niche
    assert generate_niche.subtitles_record({"enabled": True}, []) == {
        "lines": [], "attendu": True}
    assert generate_niche.subtitles_record({"enabled": True}, ["A"]) == {
        "lines": ["A"], "attendu": True}
    # Sous-titres coupés : rien n'est attendu, donc rien à signaler.
    assert generate_niche.subtitles_record({"enabled": False}, []) == {
        "lines": [], "attendu": False}
    assert generate_niche.subtitles_record({}, []) == {"lines": [], "attendu": False}
