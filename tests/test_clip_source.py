import json

import pytest

import clip_source
import clipper
from db import connect, create_clipper_source, get_clipper_source, list_clipper_clips


def w(text, start, end):
    return {"word": text, "start": start, "end": end}


# Trois phrases de 18 s bien séparées, puis une phrase courte de 2 s en queue —
# celle-ci sert à fabriquer un candidat que le recalage ne peut pas sauver.
WORDS = []
for i in range(3):
    base = i * 20.0
    WORDS += [w("phrase", base, base + 5.0), w("assez", base + 5.0, base + 10.0),
              w("longue.", base + 10.0, base + 18.0)]
WORDS.append(w("court.", 62.0, 64.0))


@pytest.fixture
def source(tmp_path):
    conn = connect(tmp_path / "platform.db")
    video = tmp_path / "data" / "clipper" / "essai" / "source.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"faux mp4")
    sid = create_clipper_source(
        conn, title="Essai", slug="essai",
        path=str(video.relative_to(tmp_path)), duration=80.0,
        created_at="2026-08-07T10:00:00")
    return conn, tmp_path, sid


def _mock_all(monkeypatch, tmp_path, moments):
    monkeypatch.setattr(clipper, "transcribe", lambda *a, **k: WORDS)
    monkeypatch.setattr(clipper, "propose_moments", lambda *a, **k: moments)
    monkeypatch.setattr(clipper, "score_moment",
                        lambda *a, **k: {"hook": 80, "flow": 70, "value": 60,
                                         "why": "ça tient"})
    rendered = []

    def fake_render(src, start, end, out, **kwargs):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"clip")
        rendered.append((start, end))
    monkeypatch.setattr(clipper, "render_clip", fake_render)
    return rendered


def test_slug_for_evite_les_collisions():
    assert clip_source.slug_for("Interview Kernel", set()) == "interview-kernel"
    assert clip_source.slug_for("Interview Kernel", {"interview-kernel"}) == \
        "interview-kernel-2"


def test_slug_for_titre_sans_caracteres_utilisables():
    assert clip_source.slug_for("???", set()).startswith("source")


def test_pipeline_complet(source, monkeypatch):
    conn, root, sid = source
    rendered = _mock_all(monkeypatch, root,
                         [{"start": 0.0, "end": 40.0, "title": "Un moment"}])

    produced = clip_source.process(conn, root, sid, clipper.DEFAULTS, log=lambda m: None)

    assert produced == 1
    assert len(rendered) == 1
    clips = list_clipper_clips(conn, sid)
    assert clips[0]["title"] == "Un moment"
    assert clips[0]["score"] == pytest.approx(0.4 * 80 + 0.3 * 70 + 0.3 * 60)
    assert clips[0]["status"] == "proposed"
    assert get_clipper_source(conn, sid)["status"] == "done"


def test_le_transcript_est_mis_en_cache(source, monkeypatch):
    conn, root, sid = source
    _mock_all(monkeypatch, root, [{"start": 0.0, "end": 40.0, "title": "M"}])
    clip_source.process(conn, root, sid, clipper.DEFAULTS, log=lambda m: None)

    cached = root / "data" / "clipper" / "essai" / "transcript.json"
    assert cached.is_file()
    assert json.loads(cached.read_text())[0]["word"] == "phrase"

    # Deuxième passage : transcribe ne doit plus être appelé du tout.
    def boom(*a, **k):
        raise AssertionError("transcribe rappelé malgré le cache")
    monkeypatch.setattr(clipper, "transcribe", boom)
    assert clip_source.process(conn, root, sid, clipper.DEFAULTS,
                               log=lambda m: None) == 1


def test_source_muette_echoue_proprement(source, monkeypatch):
    conn, root, sid = source
    monkeypatch.setattr(clipper, "transcribe", lambda *a, **k: [])

    assert clip_source.process(conn, root, sid, clipper.DEFAULTS,
                               log=lambda m: None) == 0
    updated = get_clipper_source(conn, sid)
    assert updated["status"] == "failed"
    assert "parole" in updated["error"]


def test_un_candidat_irrecalable_est_ignore_sans_faire_echouer(source, monkeypatch):
    conn, root, sid = source
    # Le second candidat ne couvre que la phrase de queue (2 s), et il n'y a
    # rien après pour l'étendre à min_dur : il tombe.
    rendered = _mock_all(monkeypatch, root, [
        {"start": 0.0, "end": 40.0, "title": "bon"},
        {"start": 62.0, "end": 64.0, "title": "trop court"},
    ])
    assert clip_source.process(conn, root, sid, clipper.DEFAULTS,
                               log=lambda m: None) == 1
    assert len(rendered) == 1


def test_un_echec_de_rendu_ne_perd_pas_les_autres_clips(source, monkeypatch):
    conn, root, sid = source
    _mock_all(monkeypatch, root, [
        {"start": 0.0, "end": 40.0, "title": "ok"},
        {"start": 40.0, "end": 64.0, "title": "casse"},
    ])
    original = clipper.render_clip     # = le faux rendu posé par _mock_all

    def flaky(src, start, end, out, **kwargs):
        if start > 30.0:               # le second clip démarre vers 39,85 s
            raise RuntimeError("ffmpeg a planté")
        original(src, start, end, out, **kwargs)
    monkeypatch.setattr(clipper, "render_clip", flaky)

    assert clip_source.process(conn, root, sid, clipper.DEFAULTS,
                               log=lambda m: None) == 1
    assert get_clipper_source(conn, sid)["status"] == "done"


def test_relancer_une_source_remplace_les_clips(source, monkeypatch):
    conn, root, sid = source
    _mock_all(monkeypatch, root, [{"start": 0.0, "end": 40.0, "title": "M"}])
    clip_source.process(conn, root, sid, clipper.DEFAULTS, log=lambda m: None)
    clip_source.process(conn, root, sid, clipper.DEFAULTS, log=lambda m: None)
    assert len(list_clipper_clips(conn, sid)) == 1
