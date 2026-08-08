import io
import json
import types
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


def test_propose_journalise_la_cause_avant_de_degrader(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("contexte dépassé : 4646 > 4096")
    monkeypatch.setattr(clipper, "_call_json", boom)
    lignes = []
    assert clipper.propose_moments(WORDS, 3, 7, log=lignes.append) == []
    assert any("4646 > 4096" in ligne for ligne in lignes)


def test_score_journalise_la_cause_avant_de_degrader(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("LM Studio éteint")
    monkeypatch.setattr(clipper, "_call_json", boom)
    lignes = []
    assert clipper.score_moment("texte", "titre", 7, log=lignes.append)["hook"] == 0
    assert any("LM Studio éteint" in ligne for ligne in lignes)


# --- Correction 2 : découpage du transcript en fenêtres --------------------------


def test_windows_rend_une_seule_fenetre_si_tout_tient():
    assert clipper.transcript_windows(WORDS, 10_000) == [WORDS]


def test_windows_ne_coupe_jamais_une_phrase():
    fenetres = clipper.transcript_windows(WORDS, 30)
    assert len(fenetres) > 1
    # Chaque fenêtre commence en début de phrase : ici, « Le … violent. » puis
    # « Et j'assume. ».
    assert [f[0]["word"] for f in fenetres] == ["Le", "Et"]


def test_windows_ne_perd_aucun_mot():
    mots = [w("mot.", i * 0.5, i * 0.5 + 0.4) for i in range(200)]
    fenetres = clipper.transcript_windows(mots, 200)
    assert len(fenetres) > 1
    assert [m for f in fenetres for m in f] == mots


def test_windows_isole_une_phrase_geante():
    """Une phrase plus longue que max_chars forme sa propre fenêtre : la jeter
    perdrait purement et simplement ce morceau de transcript."""
    geante = [w(f"mot{i}", i * 0.1, i * 0.1 + 0.05) for i in range(100)]
    geante[-1]["word"] = "fin."
    suite = [w("Court.", 100.0, 100.4)]
    fenetres = clipper.transcript_windows(geante + suite, 50)
    assert fenetres[0] == geante
    assert fenetres[-1] == suite


def test_digest_d_une_fenetre_phrase_geante_n_est_pas_vide():
    """La fenêtre que `transcript_windows` isole exprès doit partir au modèle
    avec son contenu : `transcript_digest` la rendait vide (break sur la
    première ligne trop longue) et le prompt disait « Transcription : (rien) »."""
    geante = [w(f"mot{i}", i * 0.1, i * 0.1 + 0.05) for i in range(800)]
    geante[-1]["word"] = "fin."
    fenetres = clipper.transcript_windows(geante, 6000)
    assert fenetres == [geante]
    digest = clipper.transcript_digest(fenetres[0], 6000)
    assert digest
    assert "mot0" in digest and "fin." in digest


def test_propose_envoie_le_texte_d_une_phrase_geante(monkeypatch):
    """Bout en bout : aucune fenêtre ne part avec un prompt sans transcription."""
    prompts = []

    def faux(system, user, schema, seed, name):
        prompts.append(user)
        return {"moments": []}
    monkeypatch.setattr(clipper, "_call_json", faux)
    geante = [w(f"mot{i}", i * 0.1, i * 0.1 + 0.05) for i in range(800)]
    geante[-1]["word"] = "fin."
    clipper.propose_moments(geante, 4, 7, digest_chars=1500)
    assert prompts and all("mot0" in prompt for prompt in prompts)


def test_propose_ignore_une_liste_de_moments_qui_n_en_est_pas_une(monkeypatch):
    """`{"moments": 5}` ou `null` : un TypeError qui s'échappe ferait tomber la
    source entière, alors qu'une seule fenêtre est en cause."""
    monkeypatch.setattr(clipper, "_call_json", lambda *a, **k: {"moments": 5})
    lignes = []
    assert clipper.propose_moments(WORDS, 3, 7, log=lignes.append) == []
    assert any("liste" in ligne for ligne in lignes)


def test_propose_rappelle_les_bornes_de_duree_reglees(monkeypatch):
    """Les bornes sont réglables : un prompt qui annonce « 15 à 60 secondes » à
    quelqu'un réglé sur 20–45 s ment au modèle."""
    vus = []

    def faux(system, user, schema, seed, name):
        vus.append((system, user))
        return {"moments": []}
    monkeypatch.setattr(clipper, "_call_json", faux)
    clipper.propose_moments(WORDS, 2, 7, min_dur=20.0, max_dur=45.0)
    system, user = vus[0]
    assert "20 à 45 secondes" in system
    assert "20 et 45 secondes" in user


def test_json_illisible_montre_ce_qui_a_ete_recu(monkeypatch):
    """Un modèle local qui n'honore pas `strict: True` rend de la prose : la
    JSONDecodeError nue n'en montrait pas un caractère."""
    body = json.dumps({"choices": [{"message": {
        "content": "Bien sûr ! Voici les moments…"}}]}).encode()
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(body))
    with pytest.raises(Exception) as excinfo:
        clipper._json_lmstudio("s", "u", {}, 7, "n")
    assert "Bien sûr" in str(excinfo.value)


def test_corps_http_non_json_remonte_avec_son_extrait(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse(b"<html>502 Bad Gateway</html>"))
    with pytest.raises(Exception) as excinfo:
        clipper._json_lmstudio("s", "u", {}, 7, "n")
    assert "502 Bad Gateway" in str(excinfo.value)


# --- Correction 3 : `eval=frame` n'existe plus sur ffmpeg 8 ----------------------


@pytest.mark.parametrize("aide, attendu", [
    # ffmpeg 7 : l'option existe.
    ("crop AVOptions:\n   x <string> ..FV....... set x\n"
     "   eval <int> ..FV....... specify when to evaluate expressions\n", True),
    # ffmpeg 8 : elle a disparu, `x` porte le drapeau T (runtime-tunable).
    ("crop AVOptions:\n   x <string> ..FV.....T. set the x crop area expression\n"
     "   keep_aspect <boolean> ..FV....... keep aspect ratio\n", False),
])
def test_sonde_eval_lit_l_aide_du_filtre(monkeypatch, aide, attendu):
    monkeypatch.setattr(clipper, "_CROP_EVAL_SUPPORTED", None)
    monkeypatch.setattr(clipper.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout=aide, stderr=""))
    assert clipper.crop_supports_eval() is attendu


def test_sonde_eval_suppose_absente_si_la_sonde_echoue(monkeypatch):
    """ffmpeg absent ou sortie inattendue : on suppose l'option absente, le
    comportement des versions récentes, qui ne casse rien sur elles."""
    monkeypatch.setattr(clipper, "_CROP_EVAL_SUPPORTED", None)

    def boom(*a, **k):
        raise FileNotFoundError("ffmpeg")
    monkeypatch.setattr(clipper.subprocess, "run", boom)
    assert clipper.crop_supports_eval() is False


def test_sonde_eval_mise_en_cache(monkeypatch):
    """Un sous-processus par segment rendu serait payé pour rien."""
    monkeypatch.setattr(clipper, "_CROP_EVAL_SUPPORTED", None)
    appels = []

    def compte(*a, **k):
        appels.append(1)
        return types.SimpleNamespace(stdout="   eval <int> ..FV.....\n", stderr="")
    monkeypatch.setattr(clipper.subprocess, "run", compte)
    clipper.crop_supports_eval()
    clipper.crop_supports_eval()
    assert len(appels) == 1


def test_windows_liste_vide():
    assert clipper.transcript_windows([], 1000) == []


def test_propose_interroge_chaque_fenetre_et_concatene(monkeypatch):
    appels = []

    def faux(system, user, schema, seed, name):
        appels.append(seed)
        return {"moments": [{"start": len(appels), "end": len(appels) + 30,
                             "title": f"fenêtre {len(appels)}"}]}
    monkeypatch.setattr(clipper, "_call_json", faux)
    mots = [w("mot.", i * 0.5, i * 0.5 + 0.4) for i in range(200)]
    fenetres = clipper.transcript_windows(mots, 200)
    out = clipper.propose_moments(mots, 6, 7, digest_chars=200)
    assert len(appels) == len(fenetres) > 1
    # Seed décalée par fenêtre : déterministe, mais distincte d'une à l'autre.
    assert appels == [7 + i for i in range(len(fenetres))]
    assert len(out) == len(fenetres)


def test_propose_ne_perd_pas_les_autres_fenetres_si_une_echoue(monkeypatch):
    etat = {"n": 0}

    def faux(system, user, schema, seed, name):
        etat["n"] += 1
        if etat["n"] == 1:
            raise RuntimeError("contexte dépassé")
        return {"moments": [{"start": 0, "end": 30, "title": f"ok {etat['n']}"}]}
    monkeypatch.setattr(clipper, "_call_json", faux)
    mots = [w("mot.", i * 0.5, i * 0.5 + 0.4) for i in range(200)]
    fenetres = clipper.transcript_windows(mots, 200)
    lignes = []
    out = clipper.propose_moments(mots, 6, 7, digest_chars=200, log=lignes.append)
    assert len(out) == len(fenetres) - 1
    assert any("contexte dépassé" in ligne for ligne in lignes)


def test_clipper_defaults_match_beatsync():
    """`beatsync.DEFAULT_CONFIG["clipper"]` duplique clipper.DEFAULTS en littéral
    (pour ne pas importer clipper depuis beatsync et inverser la dépendance) :
    ce test garde les deux alignés sans les coupler par un import croisé."""
    assert beatsync.DEFAULT_CONFIG["clipper"] == clipper.DEFAULTS
