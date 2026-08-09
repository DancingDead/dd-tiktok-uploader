import pytest

from clipper import moment_score, rank_moments


def m(start, end, hook=50, flow=50, value=50, title="t"):
    return {"start": start, "end": end, "title": title,
            "hook": hook, "flow": flow, "value": value, "why": ""}


def test_score_pondere_le_hook_le_plus_fort():
    assert moment_score(m(0, 30, hook=100, flow=0, value=0)) == pytest.approx(40.0)
    assert moment_score(m(0, 30, hook=0, flow=100, value=0)) == pytest.approx(30.0)
    assert moment_score(m(0, 30, hook=0, flow=0, value=100)) == pytest.approx(30.0)


def test_score_tolere_une_note_absente_ou_nulle():
    """Un échec LLM laisse le moment sans notes : score 0, jamais d'exception."""
    assert moment_score({"start": 0, "end": 30}) == pytest.approx(0.0)
    assert moment_score(m(0, 30, hook=None)) == pytest.approx(30.0)


def test_rank_trie_par_score_decroissant():
    moments = [m(0, 30, hook=10, title="faible"),
               m(100, 130, hook=90, title="fort")]
    assert [x["title"] for x in rank_moments(moments, 5)] == ["fort", "faible"]


def test_rank_ecarte_les_chevauchements_majoritaires():
    """Le second recouvre 80 % du premier : c'est le même moment, on garde le
    mieux noté."""
    moments = [m(0, 30, hook=90, title="garde"),
               m(6, 36, hook=20, title="doublon")]
    assert [x["title"] for x in rank_moments(moments, 5)] == ["garde"]


def test_rank_garde_les_chevauchements_mineurs():
    """20 % de recouvrement : deux moments distincts qui se touchent."""
    moments = [m(0, 30, hook=90, title="a"), m(24, 54, hook=80, title="b")]
    assert [x["title"] for x in rank_moments(moments, 5)] == ["a", "b"]


def test_rank_limite_au_compte_demande():
    moments = [m(i * 100, i * 100 + 30, hook=i * 10) for i in range(1, 8)]
    assert len(rank_moments(moments, 3)) == 3


def test_rank_deterministe_sur_les_ex_aequo():
    """Deux moments au même score : le plus tôt d'abord, toujours."""
    moments = [m(200, 230, title="tard"), m(10, 40, title="tot")]
    assert [x["title"] for x in rank_moments(moments, 5)] == ["tot", "tard"]


def test_rank_liste_vide():
    assert rank_moments([], 5) == []


def test_rank_ne_mute_pas_l_entree():
    moments = [m(0, 30)]
    rank_moments(moments, 5)
    assert "score" not in moments[0]
