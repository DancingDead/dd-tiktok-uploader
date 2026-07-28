"""Images fixes dans le catalogue de clips : chargement, scan, sélection au
montage, rendu. ffprobe est mocké — aucun média réel requis."""

import json
import subprocess
from pathlib import Path

import pytest

import beatsync
from beatsync import IMAGE_EXTENSIONS, IMAGE_MAX_DUR, load_clips, scan_clips


@pytest.fixture
def fake_ffprobe(monkeypatch):
    """ffprobe rendu déterministe : 1920x1080, 90 s pour tout le monde. La durée
    de format est absente pour une image, comme le vrai ffprobe sur un PNG."""
    def run(cmd, **kwargs):
        path = Path(cmd[-1])
        payload = {"streams": [{"width": 1920, "height": 1080}], "format": {}}
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            payload["format"]["duration"] = "90.0"
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
    monkeypatch.setattr(beatsync.subprocess, "run", run)


def make_catalog(tmp_path, names):
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    return tmp_path


def test_load_clips_accepts_images_alongside_videos(tmp_path, fake_ffprobe):
    folder = make_catalog(tmp_path, ["a.mp4", "b.png", "c.jpg", "notes.txt"])
    clips = load_clips(folder)
    assert [c["path"].name for c in clips] == ["a.mp4", "b.png", "c.jpg"]  # trié par nom


def test_images_are_tagged_and_have_no_duration(tmp_path, fake_ffprobe):
    clips = load_clips(make_catalog(tmp_path, ["a.mp4", "b.png"]))
    by_name = {c["path"].name: c for c in clips}
    assert by_name["a.mp4"]["kind"] == "video"
    assert by_name["a.mp4"]["duration"] == pytest.approx(90.0)
    assert by_name["b.png"]["kind"] == "image"
    assert by_name["b.png"]["duration"] is None
    assert by_name["b.png"]["ratio"] == pytest.approx(1920 / 1080)


def test_empty_folder_still_raises(tmp_path, fake_ffprobe):
    with pytest.raises(ValueError, match="aucun clip ni image"):
        load_clips(make_catalog(tmp_path, ["notes.txt"]))


def test_scan_skips_images(tmp_path, fake_ffprobe, monkeypatch):
    """Une image n'a rien à décoder : elle ressort SANS clé `intervals`, ce qui
    la rend entièrement utilisable (sémantique d'un clip non scanné)."""
    scanned = []
    monkeypatch.setattr(beatsync, "_scan_one",
                        lambda clip: (scanned.append(clip["path"].name),
                                      clip.update(intervals=[{"start": 0.0, "end": 9.0,
                                                              "motion": 0.5, "presence": 1.0}])))
    clips = load_clips(make_catalog(tmp_path, ["a.mp4", "b.png"]))
    scan_clips(clips)
    by_name = {c["path"].name: c for c in clips}
    assert scanned == ["a.mp4"]
    assert "intervals" not in by_name["b.png"]
    assert "intervals" in by_name["a.mp4"]


def test_image_max_dur_is_a_short_flash():
    assert 0.0 < IMAGE_MAX_DUR <= 1.0
