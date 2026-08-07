import pytest

import clipper


def w(text, start, end):
    return {"word": text, "start": start, "end": end}


WORDS = [w("Le", 0.0, 0.3), w("hardstyle", 0.3, 0.9), w("c'est", 0.9, 1.2),
         w("violent.", 1.2, 1.8), w("Et", 65.0, 65.2), w("j'assume.", 65.2, 66.0)]


def test_digest_horodate_chaque_phrase():
    out = clipper.transcript_digest(WORDS)
    assert "[00:00]" in out and "[01:05]" in out
    assert "hardstyle" in out


def test_digest_tronque_les_transcripts_trop_longs():
    """Un modèle local a une fenêtre finie : mieux vaut tronquer proprement que
    se faire couper la réponse au milieu d'un JSON."""
    long_words = [w("mot.", i * 0.5, i * 0.5 + 0.4) for i in range(20_000)]
    out = clipper.transcript_digest(long_words, max_chars=1_000)
    assert len(out) <= 1_100


def test_moment_text_extrait_la_fenetre():
    assert clipper.moment_text(WORDS, 0.0, 2.0) == "Le hardstyle c'est violent."


def test_propose_convertit_la_reponse_du_llm(monkeypatch):
    monkeypatch.setattr(clipper, "_call_json", lambda *a, **k: {
        "moments": [{"start": 0, "end": 40, "title": "Le hardstyle"}]})
    assert clipper.propose_moments(WORDS, 3, seed=7) == [
        {"start": 0.0, "end": 40.0, "title": "Le hardstyle"}]


def test_propose_ignore_les_entrees_malformees(monkeypatch):
    """Un modèle local rend parfois un objet incomplet : on jette l'entrée, on
    ne fait pas échouer la source."""
    monkeypatch.setattr(clipper, "_call_json", lambda *a, **k: {"moments": [
        {"start": 0, "end": 40, "title": "bon"},
        {"start": "?", "end": 40, "title": "cassé"},
        {"end": 40, "title": "sans début"},
        {"start": 50, "end": 20, "title": "à l'envers"},
    ]})
    assert [m["title"] for m in clipper.propose_moments(WORDS, 3, 7)] == ["bon"]


def test_propose_degrade_en_liste_vide_si_le_llm_echoue(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("LM Studio éteint")
    monkeypatch.setattr(clipper, "_call_json", boom)
    assert clipper.propose_moments(WORDS, 3, 7) == []


def test_score_borne_les_notes_dans_0_100(monkeypatch):
    monkeypatch.setattr(clipper, "_call_json", lambda *a, **k: {
        "hook": 150, "flow": -20, "value": 60, "why": "ça claque"})
    assert clipper.score_moment("texte", "titre", 7) == {
        "hook": 100, "flow": 0, "value": 60, "why": "ça claque"}


def test_score_degrade_a_zero_si_le_llm_echoue(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("timeout")
    monkeypatch.setattr(clipper, "_call_json", boom)
    assert clipper.score_moment("texte", "titre", 7) == {
        "hook": 0, "flow": 0, "value": 0, "why": ""}


def test_score_tronque_une_justification_verbeuse(monkeypatch):
    monkeypatch.setattr(clipper, "_call_json", lambda *a, **k: {
        "hook": 50, "flow": 50, "value": 50, "why": "x" * 2000})
    assert len(clipper.score_moment("t", "t", 7)["why"]) <= clipper.WHY_MAX


@pytest.mark.parametrize("reponse", [None, [], ["pas un objet"], 42])
def test_propose_degrade_si_la_racine_json_n_est_pas_un_objet(monkeypatch, reponse):
    """`strict: True` n'est pas honoré par tous les modèles locaux : la racine
    peut ne pas être l'objet demandé. L'usine dégrade, elle ne tombe pas."""
    monkeypatch.setattr(clipper, "_call_json", lambda *a, **k: reponse)
    assert clipper.propose_moments(WORDS, 3, 7) == []


@pytest.mark.parametrize("reponse", [None, [], ["pas un objet"], 42])
def test_score_degrade_si_la_racine_json_n_est_pas_un_objet(monkeypatch, reponse):
    monkeypatch.setattr(clipper, "_call_json", lambda *a, **k: reponse)
    assert clipper.score_moment("texte", "titre", 7) == {
        "hook": 0, "flow": 0, "value": 0, "why": ""}
