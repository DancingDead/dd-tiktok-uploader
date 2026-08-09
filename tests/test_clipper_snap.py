import pytest

from clipper import sentences, snap_to_speech


def w(text, start, end):
    return {"word": text, "start": start, "end": end}


# Deux phrases : « Le hardstyle c'est violent. » puis « Et j'assume totalement. »
# La coupure se fait sur le point ET sur le silence de 1 s qui suit.
PHRASES = [
    w("Le", 0.0, 0.3), w("hardstyle", 0.3, 0.9), w("c'est", 0.9, 1.2),
    w("violent.", 1.2, 1.8),
    w("Et", 2.8, 3.0), w("j'assume", 3.0, 3.6), w("totalement.", 3.6, 4.2),
]


def test_sentences_coupe_sur_la_ponctuation_et_le_silence():
    assert sentences(PHRASES) == [(0, 3), (4, 6)]


def test_sentences_coupe_sur_un_long_silence_sans_ponctuation():
    words = [w("euh", 0.0, 0.4), w("donc", 0.4, 0.9),
             w("bref", 3.0, 3.4)]     # 2,1 s de silence : nouvelle phrase
    assert sentences(words) == [(0, 1), (2, 2)]


def test_sentences_liste_vide():
    assert sentences([]) == []


def test_snap_cale_sur_les_frontieres_de_phrase():
    """Le LLM a proposé 0,7 → 3,3 : au milieu d'un mot des deux côtés."""
    start, end = snap_to_speech(0.7, 3.3, PHRASES, min_dur=0.5, max_dur=10.0)
    assert start == pytest.approx(0.0 - 0.0)   # 1re phrase, pas de silence avant
    assert end == pytest.approx(4.2 + 0.15)    # dernière phrase + respiration


def test_snap_ajoute_la_respiration_sans_mordre_sur_la_parole():
    """PAD vaut 0,15 s, mais la moitié du silence disponible seulement : entre
    les deux phrases il y a 1 s de blanc, donc 0,15 s est accordé en entier."""
    start, _ = snap_to_speech(2.9, 4.2, PHRASES, min_dur=0.5, max_dur=10.0)
    assert start == pytest.approx(2.8 - 0.15)


def test_snap_tronque_ce_qui_depasse_max_dur():
    """Deux phrases = 4,2 s ; avec max_dur à 2,5 s seule la première tient."""
    start, end = snap_to_speech(0.0, 4.2, PHRASES, min_dur=0.5, max_dur=2.5)
    assert end < 2.5


def test_snap_etend_ce_qui_est_trop_court():
    """Le candidat ne couvre que la 1re phrase (1,8 s) ; min_dur exige 3 s,
    donc la phrase suivante est absorbée."""
    _, end = snap_to_speech(0.0, 1.8, PHRASES, min_dur=3.0, max_dur=10.0)
    assert end == pytest.approx(4.2 + 0.15)


def test_snap_rejette_ce_qui_ne_peut_pas_tenir_dans_les_bornes():
    """Rien à étendre au-delà de la 2e phrase, et min_dur est inatteignable."""
    assert snap_to_speech(2.8, 4.2, PHRASES, min_dur=30.0, max_dur=60.0) is None


def test_snap_rejette_une_phrase_unique_trop_longue():
    words = [w("aaa", 0.0, 30.0), w("bbb.", 30.0, 90.0)]
    assert snap_to_speech(0.0, 90.0, words, min_dur=15.0, max_dur=60.0) is None


def test_snap_sans_mots():
    assert snap_to_speech(0.0, 10.0, [], min_dur=1.0, max_dur=60.0) is None


def test_snap_bornes_hors_du_transcript():
    """Un LLM peut halluciner un timestamp après la fin : on retombe sur la
    dernière phrase plutôt que de planter."""
    result = snap_to_speech(3000.0, 4000.0, PHRASES, min_dur=0.5, max_dur=10.0)
    assert result is not None
    assert result[1] <= 4.2 + 0.15
