"""Détection des bandes noires sur les frames du scan. Logique pure."""

import numpy as np
import pytest

from beatsync import content_rect


def frames(n=10, h=360, w=640, fill=120):
    return np.full((n, h, w, 3), fill, dtype=np.uint8)


def test_a_clean_frame_has_no_crop():
    assert content_rect(frames()) is None


def test_letterbox_is_detected():
    """Bandes de 45 px en haut et en bas d'un cadre de 360 : contenu 270/360."""
    f = frames()
    f[:, :45] = 0
    f[:, -45:] = 0
    rect = content_rect(f)
    assert rect["y"] == pytest.approx(45 / 360)
    assert rect["h"] == pytest.approx(270 / 360)
    assert rect["x"] == pytest.approx(0.0)
    assert rect["w"] == pytest.approx(1.0)


def test_pillarbox_is_detected():
    f = frames()
    f[:, :, :80] = 0
    f[:, :, -80:] = 0
    rect = content_rect(f)
    assert rect["x"] == pytest.approx(80 / 640)
    assert rect["w"] == pytest.approx(480 / 640)
    assert rect["h"] == pytest.approx(1.0)


def test_both_axes_at_once():
    f = frames()
    f[:, :40] = 0
    f[:, -40:] = 0
    f[:, :, :50] = 0
    f[:, :, -50:] = 0
    rect = content_rect(f)
    assert rect["y"] == pytest.approx(40 / 360)
    assert rect["x"] == pytest.approx(50 / 640)


def test_an_asymmetric_bar_is_detected():
    """Rien n'oblige les bandes à être symétriques."""
    f = frames()
    f[:, :90] = 0
    rect = content_rect(f)
    assert rect["y"] == pytest.approx(90 / 360)
    assert rect["h"] == pytest.approx(270 / 360)


def test_a_dark_scene_is_not_cropped():
    """Cadre uniformément sombre : les bandes dépasseraient 30 %, on refuse
    plutôt que de mutiler un plan de nuit."""
    assert content_rect(frames(fill=4)) is None


def test_a_one_pixel_dark_line_is_ignored():
    """Bruit de compression sur le bord : sous le seuil des 1,5 %."""
    f = frames()
    f[:, :1] = 0
    assert content_rect(f) is None


def test_a_bright_subtitle_in_the_bar_does_not_hide_it():
    """Un sous-titre incrusté dans la bande n'apparaît que sur une poignée de
    frames : le 95e percentile l'ignore, là où un maximum se ferait avoir.

    40 frames et une seule porteuse du sous-titre (2,5 %) : c'est l'ordre de
    grandeur réel du scan, qui tourne à 2 fps sur des clips de plusieurs
    minutes. Avec 10 frames, une seule ferait 10 % et remonterait au-dessus du
    95e percentile — le test échouerait pour une raison qui n'existe pas en
    production."""
    f = frames(n=40)
    f[:, :45] = 0
    f[:, -45:] = 0
    f[0, -40:-20, 200:400] = 255
    rect = content_rect(f)
    assert rect is not None
    assert rect["h"] == pytest.approx(270 / 360)


def test_a_dark_line_in_the_middle_is_not_a_bar():
    """Seuls les segments continus depuis un bord comptent."""
    f = frames()
    f[:, 150:170] = 0
    assert content_rect(f) is None


def test_no_frames_yields_none():
    assert content_rect(np.zeros((0, 360, 640, 3), dtype=np.uint8)) is None


# --- Intégration au scan ----------------------------------------------------

from pathlib import Path  # noqa: E402

import beatsync  # noqa: E402
from beatsync import SCAN_CACHE_VERSION, _scan_payload, scan_clips  # noqa: E402


def letterboxed_clip(tmp_path):
    return {"path": tmp_path / "film.mp4", "kind": "video", "duration": 100.0,
            "width": 1920, "height": 1080, "ratio": 1920 / 1080}


def stub_scan(monkeypatch, rect):
    """Remplace le décodage réel par un scan qui pose un rectangle donné."""
    def fake(clip):
        clip["intervals"] = [{"start": 1.0, "end": 99.0, "motion": 0.5, "presence": 0.9}]
        clip["interest_x"] = np.full(200, 0.5)
        clip["dual"] = np.zeros(200, dtype=bool)
        clip["scan_dt"] = 0.5
        clip["crop"] = rect
        if rect is not None:
            clip["ratio"] = (rect["w"] * clip["width"]) / (rect["h"] * clip["height"])
    monkeypatch.setattr(beatsync, "_scan_one", fake)


def test_scan_stores_the_crop_and_fixes_the_ratio(tmp_path, monkeypatch):
    """Un film 2.35:1 letterboxé dans du 16:9 doit être vu comme du 2.35:1,
    sinon les règles de layout du format carré restent fausses."""
    rect = {"x": 0.0, "y": 0.118, "w": 1.0, "h": 0.764}
    stub_scan(monkeypatch, rect)
    clip = letterboxed_clip(tmp_path)
    scan_clips([clip])
    assert clip["crop"] == rect
    assert clip["ratio"] == pytest.approx(2.33, abs=0.05)


def test_payload_carries_the_crop_and_a_version():
    clip = {"intervals": [], "interest_x": np.array([0.5]), "dual": np.array([False]),
            "scan_dt": 0.5, "crop": {"x": 0.0, "y": 0.1, "w": 1.0, "h": 0.8}}
    payload = _scan_payload(clip)
    assert payload["crop"] == clip["crop"]
    assert payload["version"] == SCAN_CACHE_VERSION


# --- Rendu ------------------------------------------------------------------

from beatsync import DEFAULT_CONFIG, _segment_filters  # noqa: E402


def cropped_entry(**overrides):
    return {"timeline_start": 0.0, "duration": 1.0, "clip_path": Path("/clips/f.mp4"),
            "kind": "video", "clip_in": 5.0, "speed": 1.0, "effects": [],
            "layout": "crop", "focus_x": 0.5,
            "crop": {"x": 0, "y": 132, "w": 1920, "h": 816},
            "clip_w": 1920, "clip_h": 816, **overrides}


def test_crop_comes_first_in_the_filter_chain():
    args = _segment_filters(cropped_entry(), DEFAULT_CONFIG)
    # Le graphe est le dernier argument, quel que soit le drapeau (-vf ou
    # -filter_complex) : on travaille dessus, pas sur la liste aplatie.
    graph = args[1]
    assert "crop=1920:816:0:132" in graph
    assert graph.index("crop=1920:816:0:132") < graph.index("delogo="), \
        "le delogo compte en fractions du contenu : il doit venir après le rognage"


def test_an_entry_without_crop_is_unchanged():
    entry = cropped_entry(crop=None)
    joined = " ".join(_segment_filters(entry, DEFAULT_CONFIG))
    assert "crop=1920:816" not in joined


def test_the_crop_applies_to_every_layout():
    for layout in ("crop", "split", "blur"):
        joined = " ".join(_segment_filters(cropped_entry(layout=layout), DEFAULT_CONFIG))
        assert "crop=1920:816:0:132" in joined


def test_build_edl_puts_content_dimensions_on_the_entry(monkeypatch):
    """clip_w/clip_h décrivent le contenu : c'est ce qui recale le delogo."""
    from tests.test_free_windows import clip as make_clip, config, make_analysis

    from beatsync import build_edl

    c = make_clip("f.mp4")
    c["crop"] = {"x": 0.0, "y": 132 / 1080, "w": 1.0, "h": 816 / 1080}
    edl = build_edl(make_analysis(), [c], config(), seed=42)
    entry = next(e for e in edl if e["kind"] == "video")
    assert entry["clip_h"] == 816
    assert entry["clip_w"] == 1920
    assert entry["crop"] == {"x": 0, "y": 132, "w": 1920, "h": 816}
