from pathlib import Path

import pytest

from trim_clip import TRIM_CRF, TRIM_PRESET, coerce_bounds, ffmpeg_trim_args


def test_bornes_nominales():
    assert coerce_bounds(3.0, 20.0, 30.0) == (3.0, 20.0)


def test_les_bornes_acceptent_des_chaines():
    """L'interface envoie du JSON, les nombres peuvent arriver en texte."""
    assert coerce_bounds("3", "20.5", "30") == (3.0, 20.5)


def test_une_fin_qui_depasse_a_peine_est_ramenee_a_la_duree():
    """La derniere frame d'une video tombe rarement pile sur la duree annoncee :
    poser la borne de fin sur la fin du lecteur ne doit pas etre un echec."""
    assert coerce_bounds(0.0, 30.04, 30.0) == (0.0, 30.0)


def test_un_debut_legerement_negatif_est_ramene_a_zero():
    assert coerce_bounds(-0.01, 10.0, 30.0) == (0.0, 10.0)


def test_bornes_inversees():
    with pytest.raises(ValueError):
        coerce_bounds(20.0, 3.0, 30.0)


def test_bornes_egales():
    with pytest.raises(ValueError):
        coerce_bounds(10.0, 10.0, 30.0)


def test_duree_resultante_trop_courte():
    """Sous MIN_TRIMMED_DUR le clip n'a plus de matiere exploitable."""
    with pytest.raises(ValueError):
        coerce_bounds(10.0, 10.5, 30.0)


def test_un_debut_franchement_negatif_est_refuse():
    """Ramener -5 a 0 masquerait une erreur de saisie au lieu de la signaler."""
    with pytest.raises(ValueError):
        coerce_bounds(-5.0, 10.0, 30.0)


def test_une_fin_franchement_au_dela_de_la_duree_est_refusee():
    with pytest.raises(ValueError):
        coerce_bounds(0.0, 45.0, 30.0)


def test_valeurs_non_numeriques():
    with pytest.raises(ValueError):
        coerce_bounds("<script>", 10.0, 30.0)
    with pytest.raises(ValueError):
        coerce_bounds(0.0, None, 30.0)


def test_les_booleens_sont_refuses():
    """float(True) vaut 1.0 : accepter un booleen ferait passer une faute de
    frappe pour une borne valide."""
    with pytest.raises(ValueError):
        coerce_bounds(True, 10.0, 30.0)


def test_valeurs_non_finies():
    with pytest.raises(ValueError):
        coerce_bounds(float("nan"), 10.0, 30.0)
    with pytest.raises(ValueError):
        coerce_bounds(0.0, float("inf"), 30.0)


def test_le_seek_est_avant_l_entree():
    """-ss apres -i ferait decoder toute la video avant la coupe."""
    args = ffmpeg_trim_args(Path("clips/a.mp4"), Path("clips/.trim/a.mp4"), 3.0, 20.0)
    assert args.index("-ss") < args.index("-i")
    assert args.index("-to") < args.index("-i")


def test_les_valeurs_d_encodage_sont_celles_de_la_spec():
    args = ffmpeg_trim_args(Path("a.mp4"), Path("b.mp4"), 0.0, 10.0)
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-crf") + 1] == str(TRIM_CRF)
    assert args[args.index("-preset") + 1] == TRIM_PRESET
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert args[args.index("-c:a") + 1] == "copy"


def test_les_flags_bitexact_sont_presents():
    """Invariant du projet : sans eux, l'encodeur date le fichier et deux
    encodages identiques different octet a octet."""
    args = ffmpeg_trim_args(Path("a.mp4"), Path("b.mp4"), 0.0, 10.0)
    assert "+bitexact" in args


def test_la_source_et_la_cible_sont_aux_bons_endroits():
    args = ffmpeg_trim_args(Path("clips/a.mp4"), Path("clips/.trim/a.mp4"), 1.0, 5.0)
    assert args[args.index("-i") + 1] == "clips/a.mp4"
    assert args[-1] == "clips/.trim/a.mp4"
