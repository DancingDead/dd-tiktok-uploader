"""Format de sortie : dérivation des dimensions et cadrage sensible au ratio."""

import pytest

from beatsync import DEFAULT_CONFIG, FORMATS, apply_format


def test_vertical_is_the_default():
    assert DEFAULT_CONFIG["format"] == "vertical"
    out = apply_format(dict(DEFAULT_CONFIG))
    assert (out["width"], out["height"]) == (1080, 1920)


def test_square_gives_square_dimensions():
    out = apply_format({**DEFAULT_CONFIG, "format": "carre"})
    assert (out["width"], out["height"]) == (1080, 1080)


def test_unknown_format_degrades_to_vertical():
    """Dégradation sûre, comme section et subtitles.mode."""
    out = apply_format({**DEFAULT_CONFIG, "format": "n'importe quoi"})
    assert (out["width"], out["height"]) == (1080, 1920)


def test_input_config_is_not_mutated():
    config = {**DEFAULT_CONFIG, "format": "carre"}
    apply_format(config)
    assert config["height"] == 1920


def test_nested_dicts_are_copied_not_shared():
    """Sans copie des dicts imbriqués, modifier la config de sortie polluerait
    celle de l'appelant — et donc la variante suivante du lot."""
    config = {**DEFAULT_CONFIG, "format": "carre", "effects": {"zoom": True}}
    out = apply_format(config)
    out["effects"]["zoom"] = False
    assert config["effects"]["zoom"] is True


def test_every_format_has_even_dimensions():
    """H.264 refuse les dimensions impaires."""
    for width, height in FORMATS.values():
        assert width % 2 == 0 and height % 2 == 0


# --- Cadrage sensible au format ---------------------------------------------

from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

from beatsync import frame_extract  # noqa: E402


def duel_clip(ratio=16 / 9):
    """Clip dont la fenêtre de scan est un duel franc (visages aux deux bords)."""
    n = 40
    return {"path": Path("/clips/a.mp4"), "kind": "video", "duration": 100.0,
            "width": 1920, "height": 1080, "ratio": ratio,
            "interest_x": np.full(n, 0.5), "dual": np.ones(n, dtype=bool),
            "scan_dt": 0.5}


def spread_clip(ratio=16 / 9):
    """Action sur toute la largeur : forte dispersion, pas de duel."""
    n = 40
    return {"path": Path("/clips/b.mp4"), "kind": "video", "duration": 100.0,
            "width": 1920, "height": 1080, "ratio": ratio,
            "interest_x": np.tile([0.1, 0.9], n // 2), "dual": np.zeros(n, dtype=bool),
            "scan_dt": 0.5}


def cfg(fmt):
    return apply_format({**DEFAULT_CONFIG, "format": fmt})


def test_duel_splits_in_vertical():
    _, layout = frame_extract(duel_clip(), 1.0, 2.0, cfg("vertical"))
    assert layout == "split"


def test_duel_is_cropped_in_square():
    """En 1:1 l'empilement donnerait deux bandes 2:1 ; le crop tient déjà les
    deux personnages."""
    _, layout = frame_extract(duel_clip(), 1.0, 2.0, cfg("carre"))
    assert layout == "crop"


def test_wide_source_gets_a_blurred_background_in_vertical():
    _, layout = frame_extract(spread_clip(16 / 9), 1.0, 2.0, cfg("vertical"))
    assert layout == "blur"


def test_sixteen_nine_is_cropped_in_square():
    """Seuil en carré : ratio >= 2.0 ; un 16:9 (1.78) passe donc en crop."""
    _, layout = frame_extract(spread_clip(16 / 9), 1.0, 2.0, cfg("carre"))
    assert layout == "crop"


def test_scope_source_still_blurs_in_square():
    _, layout = frame_extract(spread_clip(2.35), 1.0, 2.0, cfg("carre"))
    assert layout == "blur"


def test_focus_x_is_unchanged_by_the_format():
    fx_v, _ = frame_extract(spread_clip(), 1.0, 2.0, cfg("vertical"))
    fx_c, _ = frame_extract(spread_clip(), 1.0, 2.0, cfg("carre"))
    assert fx_v == pytest.approx(fx_c)


# --- Images : même règle que les vidéos -------------------------------------

from beatsync import build_edl  # noqa: E402


def image_clip(ratio):
    width = int(1080 * ratio)
    return {"path": Path("/clips/x.png"), "kind": "image", "duration": None,
            "width": width, "height": 1080, "ratio": ratio}


def test_image_layout_follows_the_same_rule_as_videos():
    """Effet de bord assumé du volet C : en vertical le seuil des images passe
    de 1.2 à 1.125 (= 2.0 x 0.5625), donc un 4:3 (1.33) gagne un fond flouté."""
    from tests.test_images import make_analysis, video

    clips = [video("a.mp4"), image_clip(4 / 3)]
    config = apply_format({**DEFAULT_CONFIG, "format": "vertical",
                           "start": 0.0, "end": 60.0, "drop_time": 30.0})
    edl = build_edl(make_analysis(), clips, config, seed=42)
    images = [e for e in edl if e.get("kind") == "image"]
    assert images and all(e["layout"] == "blur" for e in images)

    square = apply_format({**DEFAULT_CONFIG, "format": "carre",
                           "start": 0.0, "end": 60.0, "drop_time": 30.0})
    edl = build_edl(make_analysis(), clips, square, seed=42)
    images = [e for e in edl if e.get("kind") == "image"]
    assert images and all(e["layout"] == "crop" for e in images)
