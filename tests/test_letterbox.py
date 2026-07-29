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
