"""Câblage de `render_clip` : qui est appelé, avec quoi, et ce qui sort dans le
filtre.

Le rendu réel demande ffmpeg et une vraie vidéo, donc il n'est pas testable en
CI — mais le CÂBLAGE l'est, et c'est lui qui a cassé deux fois pendant le
chantier du recadrage sur le locuteur : trois signatures ont bougé en cours de
route (`crop_segments` a gagné `fps`, `crop_expr` est passée d'une trajectoire à
des segments, `link_tracks` a gagné une borne d'ancienneté). Elles concordent
aujourd'hui, rien ne le garantissait demain : `test_clip_source.py` mocke
`render_clip` en entier et `analyze_framing` n'avait aucun test.

On monkeypatche les quatre points d'I/O (dimensions, analyse, sonde ffmpeg,
sous-processus) et on inspecte la chaîne de filtres construite.
"""

import subprocess

import pytest

import clipper
import speaker


MOTS = [{"word": "salut", "start": 0.0, "end": 0.4},
        {"word": "toi", "start": 0.5, "end": 0.9}]


@pytest.fixture
def ffmpeg(monkeypatch):
    """Capture les arguments du seul appel ffmpeg, sans rien lancer."""
    appels = []

    class Faux:
        returncode = 0
        stderr = ""

    def faux_run(args, **kwargs):
        appels.append(list(args))
        return Faux()

    monkeypatch.setattr(subprocess, "run", faux_run)
    monkeypatch.setattr(clipper, "probe_size", lambda path: (1920, 1080))
    # La sonde est mémorisée au niveau du module : on la remplace, sinon le
    # résultat dépendrait de la ffmpeg de la machine qui lance les tests.
    monkeypatch.setattr(clipper, "crop_supports_eval", lambda: True)
    return appels


def filtre(appels):
    """La chaîne passée à -vf de l'unique appel ffmpeg."""
    assert len(appels) == 1
    args = appels[0]
    return args[args.index("-vf") + 1]


def test_speaker_cuts_appelle_analyze_framing_avec_les_bons_arguments(
        ffmpeg, monkeypatch, tmp_path):
    """Le chemin nominal : `analyze_framing` reçoit la fenêtre, les dimensions
    sondées et le `min_shot` des réglages ; l'expression qu'elle induit se
    retrouve telle quelle dans le `crop=`."""
    vus = {}

    def faux_analyze(video_path, start, end, src_w, src_h, *, min_shot, log=None):
        vus.update(path=video_path, start=start, end=end, src_w=src_w,
                   src_h=src_h, min_shot=min_shot, log=log)
        # Deux segments distincts : l'expression compilée porte un palier.
        return [{"start": 0.0, "end": 1.0, "x_start": 100, "x_end": 100},
                {"start": 1.0, "end": 2.0, "x_start": 800, "x_end": 800}]

    monkeypatch.setattr(speaker, "analyze_framing", faux_analyze)
    journal = []

    def note(ligne):
        journal.append(ligne)

    clipper.render_clip(tmp_path / "source.mp4", 3.0, 5.0,
                        tmp_path / "out.mp4", words=MOTS,
                        config={"speaker_cuts": True, "min_shot": 0.8},
                        log=note)

    assert (vus["start"], vus["end"]) == (3.0, 5.0)
    assert (vus["src_w"], vus["src_h"]) == (1920, 1080)
    assert vus["min_shot"] == 0.8
    assert vus["log"] is note       # le repli doit rester journalisable

    attendu = speaker.crop_expr(faux_analyze(
        None, 0, 0, 1920, 1080, min_shot=0.8), 606, 1920)
    vf = filtre(ffmpeg)
    assert f"x='{attendu}'" in vf
    assert vf.startswith("crop=606:1080:")   # 9:16 pair, tiré de crop_size


def test_sans_speaker_cuts_c_est_le_repli_qui_est_emprunte(
        ffmpeg, monkeypatch, tmp_path):
    """`speaker_cuts: False` → track_faces → smooth_track → track_to_segments,
    et `analyze_framing` n'est PAS appelée. C'est la porte de sortie du
    réglage : si elle se refermait en silence, le repli ne servirait plus à
    rien sur le contenu où la détection se comporte mal."""
    ordre = []

    def interdit(*args, **kwargs):
        raise AssertionError("analyze_framing ne doit pas être appelée")

    monkeypatch.setattr(speaker, "analyze_framing", interdit)

    def faux_track_faces(video_path, start, end):
        ordre.append("track_faces")
        return [400.0, 500.0, 600.0]

    vraie_smooth = speaker.smooth_track

    def faux_smooth(centers, default, dead_zone):
        ordre.append("smooth_track")
        # La zone morte se compte en pixels SOURCE, rapportée au crop.
        assert dead_zone == pytest.approx(speaker.DEAD_ZONE * 606)
        assert default == pytest.approx(1920 / 2)
        return vraie_smooth(centers, default, dead_zone)

    vraie_segments = speaker.track_to_segments

    def faux_segments(centers, sample_fps, crop_w, src_w):
        ordre.append("track_to_segments")
        assert sample_fps == clipper.SAMPLE_FPS
        return vraie_segments(centers, sample_fps, crop_w, src_w)

    monkeypatch.setattr(clipper, "track_faces", faux_track_faces)
    monkeypatch.setattr(speaker, "smooth_track", faux_smooth)
    monkeypatch.setattr(speaker, "track_to_segments", faux_segments)

    clipper.render_clip(tmp_path / "source.mp4", 0.0, 1.5,
                        tmp_path / "out.mp4", words=MOTS,
                        config={"speaker_cuts": False})

    assert ordre == ["track_faces", "smooth_track", "track_to_segments"]
    assert "crop=606:1080:" in filtre(ffmpeg)


def test_eval_frame_seulement_si_l_expression_en_a_besoin_et_ffmpeg_la_connait(
        ffmpeg, monkeypatch, tmp_path):
    """`:eval=frame` n'apparaît que si les DEUX conditions tiennent.

    C'est le piège qui figerait le cadre en silence : sans l'option sur une
    ffmpeg ≤ 7, une expression dépendant de `t` n'est évaluée qu'une fois ; avec
    l'option sur une ffmpeg ≥ 8, tous les rendus échouent (« Option not
    found »)."""
    mobile = [{"start": 0.0, "end": 1.0, "x_start": 100, "x_end": 100},
              {"start": 1.0, "end": 2.0, "x_start": 800, "x_end": 800}]
    fige = [{"start": 0.0, "end": 2.0, "x_start": 300, "x_end": 300}]

    for segments, supporte, attendu in ((mobile, True, True),
                                        (mobile, False, False),
                                        (fige, True, False),
                                        (fige, False, False)):
        ffmpeg.clear()
        monkeypatch.setattr(
            speaker, "analyze_framing",
            lambda *a, segments=segments, **k: segments)
        monkeypatch.setattr(clipper, "crop_supports_eval",
                            lambda supporte=supporte: supporte)
        clipper.render_clip(tmp_path / "source.mp4", 0.0, 2.0,
                            tmp_path / "out.mp4", words=MOTS, config={})
        vf = filtre(ffmpeg)
        assert (":eval=frame" in vf) is attendu, (segments, supporte, vf)


def test_le_fichier_de_sous_titres_est_efface_apres_le_rendu(ffmpeg, monkeypatch,
                                                             tmp_path):
    """Le .ass est un intermédiaire : le laisser traîner à côté du mp4 le
    ferait servir par l'endpoint de lecture des clips."""
    monkeypatch.setattr(speaker, "analyze_framing", lambda *a, **k: [])
    out = tmp_path / "clip.mp4"
    clipper.render_clip(tmp_path / "source.mp4", 0.0, 2.0, out,
                        words=MOTS, config={})
    assert not out.with_suffix(".ass").exists()
    assert "subtitles=" in filtre(ffmpeg)
