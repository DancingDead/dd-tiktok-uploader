import io
import json
import urllib.error
import urllib.request

import pytest

import beatsync
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


def test_call_json_bascule_sur_le_backend_de_repli(monkeypatch):
    """CLAUDE.md promet que LLM_BACKEND pilote les deux sous-systèmes de la même
    façon : sans repli, un LM Studio éteint fait échouer le clipper alors que
    beatsync serait passé sur Anthropic."""
    monkeypatch.setenv("LLM_BACKEND", "lmstudio")
    monkeypatch.setenv("LLM_FALLBACK", "anthropic")

    def eteint(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(clipper, "_json_lmstudio", eteint)
    monkeypatch.setattr(clipper, "_json_anthropic", lambda *a, **k: {"ok": True})

    assert clipper._call_json("s", "u", {}, 7, "n") == {"ok": True}


def test_call_json_remonte_l_erreur_sans_repli(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "lmstudio")
    monkeypatch.delenv("LLM_FALLBACK", raising=False)

    def eteint(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(clipper, "_json_lmstudio", eteint)
    with pytest.raises(OSError):
        clipper._call_json("s", "u", {}, 7, "n")


# --- Correction 1 : l'erreur réelle du serveur doit remonter --------------------


class _FakeResponse(io.BytesIO):
    """Réponse d'urlopen : un contexte qui rend des octets."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_lmstudio_remonte_le_message_d_erreur_d_un_200(monkeypatch):
    """LM Studio répond 200 avec {"error": ...} quand le chemin de l'endpoint est
    mauvais : sans garde, data["choices"] lève un KeyError nu et le message du
    serveur — le seul qui explique quoi que ce soit — est perdu."""
    body = json.dumps({"error": "Unexpected endpoint or method."}).encode()
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(body))
    with pytest.raises(Exception) as excinfo:
        clipper._json_lmstudio("s", "u", {}, 7, "n")
    assert "Unexpected endpoint or method." in str(excinfo.value)


def test_lmstudio_remonte_le_corps_d_une_erreur_http(monkeypatch):
    """Le dépassement de contexte sort en HTTP 400 avec l'explication dans le
    corps : c'est le corps qu'il faut montrer, pas le code seul."""
    message = ("Trying to keep the first 4646 tokens when context the overflows. "
               "However, the model is loaded with context length of only 4096 tokens.")
    body = json.dumps({"error": message}).encode()

    def boom(*a, **k):
        raise urllib.error.HTTPError("http://x/v1/chat/completions", 400,
                                     "Bad Request", {}, io.BytesIO(body))
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(Exception) as excinfo:
        clipper._json_lmstudio("s", "u", {}, 7, "n")
    assert "400" in str(excinfo.value)
    assert "context length of only 4096 tokens" in str(excinfo.value)


def test_lmstudio_signale_une_reponse_sans_choices(monkeypatch):
    body = json.dumps({"objet": "inattendu"}).encode()
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(body))
    with pytest.raises(Exception) as excinfo:
        clipper._json_lmstudio("s", "u", {}, 7, "n")
    assert "inexploitable" in str(excinfo.value)
    assert "inattendu" in str(excinfo.value)


def test_clipper_defaults_match_beatsync():
    """`beatsync.DEFAULT_CONFIG["clipper"]` duplique clipper.DEFAULTS en littéral
    (pour ne pas importer clipper depuis beatsync et inverser la dépendance) :
    ce test garde les deux alignés sans les coupler par un import croisé."""
    assert beatsync.DEFAULT_CONFIG["clipper"] == clipper.DEFAULTS
