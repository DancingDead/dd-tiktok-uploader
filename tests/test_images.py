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


# --- Sélection au montage ---------------------------------------------------

import numpy as np  # noqa: E402

from beatsync import DEFAULT_CONFIG, build_edl  # noqa: E402

BPM = 128.0
BEAT = 60.0 / BPM
DURATION = 60.0


def make_analysis():
    beats = np.arange(0.0, DURATION, BEAT)
    times = np.linspace(0.0, DURATION, 601)
    energy = np.where(
        times < DURATION / 2,
        np.interp(times, [0.0, DURATION / 2], [0.05, 0.20]),
        np.interp(times, [DURATION / 2, DURATION], [0.80, 1.00]),
    )
    return {"duration": DURATION, "bpm": BPM, "beats": beats,
            "energy": energy, "energy_times": times}


def video(name, duration=90.0):
    return {"path": Path(f"/clips/{name}"), "kind": "video", "duration": duration,
            "width": 1920, "height": 1080, "ratio": 1920 / 1080}


def image(name, width=1920, height=1080):
    return {"path": Path(f"/clips/{name}"), "kind": "image", "duration": None,
            "width": width, "height": height, "ratio": width / height}


def mixed_config(**overrides):
    return {**DEFAULT_CONFIG, "start": 0.0, "end": DURATION,
            "drop_time": 30.0, **overrides}


def build(clips, seed=42, **overrides):
    return build_edl(make_analysis(), clips, mixed_config(**overrides), seed=seed)


def test_images_are_used_only_on_short_segments():
    edl = build([video("a.mp4"), image("b.png")])
    used = [e for e in edl if e.get("kind") == "image"]
    assert used, "aucune image montée"
    assert all(e["duration"] <= IMAGE_MAX_DUR + 1e-9 for e in used)


def test_images_never_slow_down_or_speed_up():
    edl = build([video("a.mp4"), image("b.png")])
    assert all(e["speed"] == pytest.approx(1.0)
               for e in edl if e.get("kind") == "image")


def test_images_start_at_zero_and_get_kenburns():
    edl = build([video("a.mp4"), image("b.png")])
    for entry in edl:
        if entry.get("kind") == "image":
            assert entry["clip_in"] == pytest.approx(0.0)
            assert "kenburns" in entry["effects"]
            assert "zoom" not in entry["effects"], "kenburns fait déjà le zoom"
            assert entry["kenburns"]["zoom_dir"] in (1, -1)
            assert entry["kenburns"]["pan_dir"] in (1, -1)


def test_images_keep_a_minimum_gap_of_three_segments():
    """Sans garde-fou, un passage de strobo devient un diaporama."""
    edl = build([video("a.mp4"), image("b.png"), image("c.png"), image("d.png")])
    positions = [i for i, e in enumerate(edl) if e.get("kind") == "image"]
    assert positions, "aucune image montée"
    assert all(b - a >= 3 for a, b in zip(positions, positions[1:]))


def test_wide_image_falls_back_to_the_blurred_background_layout():
    edl = build([video("a.mp4"), image("wide.png", 1920, 1080)])
    used = [e for e in edl if e.get("kind") == "image"]
    assert used and all(e["layout"] == "blur" for e in used)


def test_portrait_image_is_cropped():
    edl = build([video("a.mp4"), image("tall.png", 1080, 1920)])
    used = [e for e in edl if e.get("kind") == "image"]
    assert used and all(e["layout"] == "crop" for e in used)


def test_image_selection_is_reproducible():
    clips = [video("a.mp4"), image("b.png"), image("c.png")]
    a = build(clips, seed=7)
    b = build(clips, seed=7)
    assert a == b


def test_video_only_catalog_is_unchanged():
    """Non-régression : sans image dans le catalogue, aucune clé nouvelle ne
    perturbe l'EDL et rien n'est marqué image."""
    edl = build([video("a.mp4"), video("b.mp4")])
    assert all(e["kind"] == "video" for e in edl)
    assert all("kenburns" not in e["effects"] for e in edl)


# --- Rendu ------------------------------------------------------------------

from beatsync import _segment_filters, _segment_input_args, kenburns_filter  # noqa: E402


def image_entry(**overrides):
    return {"timeline_start": 0.0, "duration": 0.5, "clip_path": Path("/clips/b.png"),
            "kind": "image", "clip_in": 0.0, "speed": 1.0,
            "effects": ["kenburns"], "kenburns": {"zoom_dir": 1, "pan_dir": 1},
            "focus_x": 0.5, "layout": "crop", "clip_w": 1920, "clip_h": 1080,
            **overrides}


def video_entry(**overrides):
    return {"timeline_start": 0.0, "duration": 0.5, "clip_path": Path("/clips/a.mp4"),
            "kind": "video", "clip_in": 12.0, "speed": 1.0, "effects": [],
            "focus_x": 0.5, "layout": "crop", "clip_w": 1920, "clip_h": 1080,
            **overrides}


def test_image_input_is_looped_without_seek():
    args = _segment_input_args(image_entry())
    assert args[:2] == ["-loop", "1"]
    assert "-ss" not in args
    assert args[-2:] == ["-i", "/clips/b.png"]


def test_video_input_seeks_before_the_input():
    args = _segment_input_args(video_entry())
    assert args[0] == "-ss" and args[1].startswith("12.0")
    assert "-loop" not in args


def test_video_input_accounts_for_speed():
    """Un segment accéléré consomme plus de source : duration x speed."""
    args = _segment_input_args(video_entry(duration=2.0, speed=1.4))
    assert float(args[3]) == pytest.approx(2.0 * 1.4 + 0.5)


def test_kenburns_zoom_direction_changes_the_expression():
    zoom_in = kenburns_filter(image_entry(kenburns={"zoom_dir": 1, "pan_dir": 1}),
                              DEFAULT_CONFIG)
    zoom_out = kenburns_filter(image_entry(kenburns={"zoom_dir": -1, "pan_dir": 1}),
                               DEFAULT_CONFIG)
    assert zoom_in.startswith("zoompan=") and zoom_out.startswith("zoompan=")
    assert zoom_in != zoom_out


def test_kenburns_pan_direction_changes_the_expression():
    left = kenburns_filter(image_entry(kenburns={"zoom_dir": 1, "pan_dir": -1}),
                           DEFAULT_CONFIG)
    right = kenburns_filter(image_entry(kenburns={"zoom_dir": 1, "pan_dir": 1}),
                            DEFAULT_CONFIG)
    assert left != right


def test_segment_filters_apply_kenburns_to_an_image():
    joined = " ".join(_segment_filters(image_entry(), DEFAULT_CONFIG))
    assert "zoompan=" in joined


def test_segment_filters_leave_videos_untouched():
    joined = " ".join(_segment_filters(video_entry(), DEFAULT_CONFIG))
    assert "zoompan=" not in joined
