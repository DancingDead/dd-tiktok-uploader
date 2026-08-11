"""Tests des punchlines incrustées : découpage en créneaux (pur), génération
(mockée : cache + dégradation), intégration EDL, et drawtext au rendu."""

import numpy as np
import pytest

import beatsync
from beatsync import (apply_subtitles, assign_caption_slots, generate_punchlines,
                      resolve_caption_font, _segment_filters)

DEFAULT = beatsync.DEFAULT_CONFIG


def make_edl(starts):
    """EDL minimal : une entrée par timeline_start donné."""
    edl = []
    for i, s in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else s + 0.4
        edl.append({"timeline_start": s, "duration": nxt - s})
    return edl


# --- Découpage en créneaux (logique pure) -----------------------------------


def test_slots_group_segments_by_min_duration():
    # Coupes toutes les 0.4 s ; min_dur 1.4 s → un nouveau créneau seulement
    # quand ≥ 1.4 s se sont écoulées depuis le début du créneau courant.
    edl = make_edl([0.0, 0.4, 0.8, 1.2, 1.6, 2.0, 2.4, 3.2])
    n = assign_caption_slots(edl, min_dur=1.4)
    slots = [e["caption_slot"] for e in edl]
    assert slots == [0, 0, 0, 0, 1, 1, 1, 2]
    assert n == 3


def test_each_slot_spans_at_least_min_dur_except_last():
    edl = make_edl([0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0])
    assign_caption_slots(edl, min_dur=1.0)
    # Un créneau s'affiche de son début jusqu'au début du suivant : cet écart
    # (hors dernier créneau) doit valoir ≥ min_dur.
    from itertools import groupby
    starts = [g[0]["timeline_start"] for _, g in
              ((k, list(v)) for k, v in groupby(edl, key=lambda e: e["caption_slot"]))]
    for a, b in zip(starts, starts[1:]):
        assert b - a >= 1.0 - 1e-9


def test_min_dur_zero_gives_one_slot_per_cut():
    edl = make_edl([0.0, 0.4, 0.8])
    n = assign_caption_slots(edl, min_dur=0.0)
    assert [e["caption_slot"] for e in edl] == [0, 1, 2]
    assert n == 3


# --- Génération (mockée) : cache + dégradation ------------------------------


def test_generate_caches_and_reuses(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(beatsync, "_call_llm",
                        lambda pp, n, seed, model, style="court": (calls.append(1) or ["A", "B", "C"][:n]))
    cache = tmp_path / "subs"
    a = generate_punchlines("motivation gym", 3, seed=42, cache_dir=cache)
    b = generate_punchlines("motivation gym", 3, seed=42, cache_dir=cache)
    assert a == ["A", "B", "C"] and b == a
    assert len(calls) == 1  # 2e appel servi par le cache


def test_generate_seed_changes_cache_key(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(beatsync, "_call_llm",
                        lambda pp, n, seed, model, style="court": (calls.append(seed) or [f"s{seed}"] * n))
    cache = tmp_path / "subs"
    generate_punchlines("x", 2, seed=1, cache_dir=cache)
    generate_punchlines("x", 2, seed=2, cache_dir=cache)
    assert calls == [1, 2]  # seed différent = génération distincte


def test_generate_degrades_to_empty_on_failure(tmp_path, monkeypatch):
    def boom(*a):
        raise RuntimeError("pas de clé API")
    monkeypatch.setattr(beatsync, "_call_llm", boom)
    assert generate_punchlines("x", 3, seed=1, cache_dir=tmp_path) == []


def test_generate_noop_without_preprompt_or_count():
    assert generate_punchlines("", 3, seed=1) == []
    assert generate_punchlines("  ", 3, seed=1) == []
    assert generate_punchlines("x", 0, seed=1) == []


# --- Backends LLM : LM Studio (compatible OpenAI) + dispatch/fallback --------


def test_call_lmstudio_parses_openai_response(monkeypatch):
    import io
    import json

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        payload = {"choices": [{"message": {"content":
                   json.dumps({"punchlines": ["monte le son", "encore plus fort"]})}}]}
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("LMSTUDIO_MODEL", "qwen2.5-7b")

    out = beatsync._call_lmstudio("motivation gym", 2, seed=7, model="claude-opus-4-8")
    assert out == ["monte le son", "encore plus fort"]
    assert captured["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert captured["body"]["model"] == "qwen2.5-7b"
    assert captured["body"]["seed"] == 7  # seed transmis pour reproductibilité


def test_call_llm_defaults_to_lmstudio(monkeypatch):
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    monkeypatch.delenv("LLM_FALLBACK", raising=False)
    monkeypatch.setattr(beatsync, "_call_lmstudio", lambda *a: ["local"])
    monkeypatch.setattr(beatsync, "_call_anthropic", lambda *a: ["cloud"])
    assert beatsync._call_llm("x", 1, 1, "m") == ["local"]


def test_call_llm_falls_back_to_anthropic(monkeypatch):
    def boom(*a):
        raise RuntimeError("LM Studio éteint")
    monkeypatch.setenv("LLM_BACKEND", "lmstudio")
    monkeypatch.setenv("LLM_FALLBACK", "anthropic")
    monkeypatch.setattr(beatsync, "_call_lmstudio", boom)
    monkeypatch.setattr(beatsync, "_call_anthropic", lambda *a: ["cloud"])
    assert beatsync._call_llm("x", 1, 1, "m") == ["cloud"]


def test_call_llm_no_fallback_reraises(monkeypatch):
    def boom(*a):
        raise RuntimeError("down")
    monkeypatch.setenv("LLM_BACKEND", "lmstudio")
    monkeypatch.delenv("LLM_FALLBACK", raising=False)
    monkeypatch.setattr(beatsync, "_call_lmstudio", boom)
    with pytest.raises(RuntimeError):
        beatsync._call_llm("x", 1, 1, "m")


# --- Intégration EDL --------------------------------------------------------


def test_apply_subtitles_assigns_caption_per_slot(monkeypatch):
    monkeypatch.setattr(beatsync, "_call_llm",
                        lambda pp, n, seed, model, style="court": [f"punch{i}" for i in range(n)])
    # starts 0/0.4/0.8 → créneau 0 ; 1.6 (≥1.4 après 0) → créneau 1 ; 2.4 (0.8
    # après 1.6, < 1.4) → reste créneau 1.
    edl = make_edl([0.0, 0.4, 0.8, 1.6, 2.4])
    config = dict(DEFAULT, subtitles={"enabled": True, "preprompt": "gym", "min_dur": 1.4})
    apply_subtitles(edl, config, seed=42)
    caps = [e["caption"] for e in edl]
    assert caps == ["punch0", "punch0", "punch0", "punch1", "punch1"]


def test_apply_subtitles_disabled_is_noop():
    edl = make_edl([0.0, 0.4])
    config = dict(DEFAULT, subtitles={"enabled": False, "preprompt": "gym"})
    apply_subtitles(edl, config, seed=42)
    assert all("caption" not in e for e in edl)


def test_apply_subtitles_no_text_when_generation_empty(monkeypatch):
    monkeypatch.setattr(beatsync, "_call_llm", lambda *a: [])  # pas de clé -> []
    edl = make_edl([0.0, 0.4, 1.6])
    config = dict(DEFAULT, subtitles={"enabled": True, "preprompt": "gym", "min_dur": 1.4})
    apply_subtitles(edl, config, seed=42)
    assert all(e["caption"] == "" for e in edl)  # créneaux assignés mais texte vide


# --- Rendu : drawtext -------------------------------------------------------


def test_segment_filters_draws_caption_when_present():
    entry = {"timeline_start": 0, "duration": 1.0, "effects": [], "layout": "crop",
             "focus_x": 0.5, "speed": 1.0, "caption": "NO PAIN NO GAIN"}
    args = _segment_filters(entry, dict(DEFAULT))
    vf = " ".join(args)
    assert "drawtext" in vf and "NO PAIN NO GAIN" in vf


def test_segment_filters_no_drawtext_without_caption():
    entry = {"timeline_start": 0, "duration": 1.0, "effects": [], "layout": "crop",
             "focus_x": 0.5, "speed": 1.0}
    assert "drawtext" not in " ".join(_segment_filters(entry, dict(DEFAULT)))


def test_caption_special_chars_escaped():
    entry = {"timeline_start": 0, "duration": 1.0, "effects": [], "layout": "crop",
             "focus_x": 0.5, "speed": 1.0, "caption": "t'es prêt: vas-y"}
    vf = " ".join(_segment_filters(entry, dict(DEFAULT)))
    # l'apostrophe et les deux-points ne doivent pas casser le filtre (échappés)
    assert "drawtext" in vf
    assert "\\:" in vf or "\\\\:" in vf


def caption_entry():
    return {"timeline_start": 0.0, "duration": 1.0, "clip_path": "/clips/a.mp4",
            "clip_in": 0.0, "speed": 1.0, "effects": [], "layout": "crop",
            "focus_x": 0.5, "clip_w": 1920, "clip_h": 1080, "caption": "TEST"}


def test_caption_placement_comes_from_the_config():
    config = {**DEFAULT, "subtitles": {**DEFAULT["subtitles"],
                                       "x": 0.25, "y": 0.10, "size": 96}}
    joined = " ".join(_segment_filters(caption_entry(), config))
    assert "fontsize=96" in joined
    assert "x=w*0.2500-text_w/2" in joined
    assert "y=h*0.1000-text_h/2" in joined


def test_caption_defaults_keep_the_historical_placement():
    joined = " ".join(_segment_filters(caption_entry(), DEFAULT))
    assert "fontsize=64" in joined
    assert "x=w*0.5000-text_w/2" in joined


def test_generated_punchlines_use_the_same_placement_path():
    """Un seul chemin de code : le mode LLM hérite du réglage de placement."""
    config = {**DEFAULT, "subtitles": {**DEFAULT["subtitles"], "mode": "llm", "size": 40}}
    joined = " ".join(_segment_filters(caption_entry(), config))
    assert "fontsize=40" in joined


def test_no_caption_means_no_drawtext():
    entry = caption_entry()
    del entry["caption"]
    assert "drawtext" not in " ".join(_segment_filters(entry, DEFAULT))


def test_caption_placement_degrades_to_defaults_on_none_values():
    """`None` (ex. champ de formulaire vidé) ne doit pas faire planter le
    rendu : dégradation sur le défaut, comme generate_punchlines -> []."""
    config = {**DEFAULT, "subtitles": {**DEFAULT["subtitles"],
                                       "x": None, "y": None, "size": None}}
    joined = " ".join(_segment_filters(caption_entry(), config))
    assert "fontsize=64" in joined
    assert "x=w*0.5000-text_w/2" in joined
    assert "y=h*0.7400-text_h/2" in joined


def test_caption_placement_degrades_to_defaults_on_unparsable_strings():
    config = {**DEFAULT, "subtitles": {**DEFAULT["subtitles"],
                                       "x": "gauche", "y": "haut", "size": "96.5"}}
    joined = " ".join(_segment_filters(caption_entry(), config))
    assert "fontsize=64" in joined
    assert "x=w*0.5000-text_w/2" in joined
    assert "y=h*0.7400-text_h/2" in joined


# --- Polices embarquées ------------------------------------------------------


def test_resolve_caption_font_known_names():
    for name, filename in beatsync._FONT_FILES.items():
        path = resolve_caption_font(name)
        assert path is not None and path.endswith(filename)


def test_resolve_caption_font_unknown_name_falls_back_to_impact():
    assert resolve_caption_font("gothique") == resolve_caption_font("impact")


def test_resolve_caption_font_missing_file_falls_back_to_system(monkeypatch, tmp_path):
    monkeypatch.setattr(beatsync, "FONTS_DIR", tmp_path)  # dossier vide
    assert resolve_caption_font("douce") == beatsync._caption_font()


def test_default_config_has_font_key():
    assert DEFAULT["subtitles"]["font"] == "impact"


def test_merge_settings_accepts_subtitles_font():
    from beatsync import merge_settings
    merged = merge_settings(dict(DEFAULT), {"subtitles": {"font": "douce"}})
    assert merged["subtitles"]["font"] == "douce"
    assert merged["subtitles"]["enabled"] is False  # le reste est préservé


def test_segment_filters_uses_configured_font():
    entry = {"timeline_start": 0, "duration": 1.0, "effects": [], "layout": "crop",
             "focus_x": 0.5, "speed": 1.0, "caption": "CHILL"}
    config = dict(DEFAULT, subtitles={**DEFAULT["subtitles"], "font": "douce"})
    vf = " ".join(_segment_filters(entry, config))
    assert "Baloo2-Bold.ttf" in vf


# --- Mode texte fixe --------------------------------------------------------


def fixed_config(**subs):
    return {**DEFAULT, "subtitles": {**DEFAULT["subtitles"],
                                     "enabled": True, "mode": "fixe", **subs}}


def test_fixed_mode_puts_the_same_caption_on_every_segment():
    edl = make_edl([0.0, 0.4, 0.8, 1.2, 1.6])
    out = apply_subtitles(edl, fixed_config(text="LIEN EN BIO"), seed=1)
    assert [e["caption"] for e in out] == ["LIEN EN BIO"] * 5


def test_fixed_mode_never_calls_the_llm(monkeypatch):
    """L'usine ne doit pas dépendre du LLM quand le texte est écrit à la main.
    On compte les appels plutôt que de lever : generate_punchlines rattrape
    Exception (dégradation en []), une AssertionError y serait avalée."""
    calls = []
    monkeypatch.setattr(beatsync, "_call_llm",
                        lambda pp, n, seed, model, style="court": (calls.append(1) or ["X"] * n))
    edl = make_edl([0.0, 0.4, 0.8])
    out = apply_subtitles(edl, fixed_config(text="DANCING DEAD"), seed=1)
    assert calls == []
    assert all(e["caption"] == "DANCING DEAD" for e in out)


def test_fixed_mode_disabled_leaves_the_edl_alone():
    edl = make_edl([0.0, 0.4])
    config = {**DEFAULT, "subtitles": {**DEFAULT["subtitles"],
                                       "enabled": False, "mode": "fixe", "text": "X"}}
    out = apply_subtitles(edl, config, seed=1)
    assert all("caption" not in e for e in out)


# --- Mode « une punchline générée » -----------------------------------------


def unique_config(**subs):
    return {**DEFAULT, "subtitles": {**DEFAULT["subtitles"], "enabled": True,
                                     "mode": "llm_unique",
                                     "preprompt": "motivation gym", **subs}}


def test_unique_mode_puts_the_same_generated_caption_everywhere(monkeypatch):
    monkeypatch.setattr(beatsync, "_call_llm",
                        lambda pp, n, seed, model, style="court": ["ON LACHE RIEN"])
    edl = make_edl([0.0, 0.4, 0.8, 1.2, 1.6])
    out = apply_subtitles(edl, unique_config(), seed=1)
    assert [e["caption"] for e in out] == ["ON LACHE RIEN"] * 5


def test_unique_mode_asks_the_llm_for_exactly_one_punchline(monkeypatch):
    """Une seule punchline demandée, un seul appel — et surtout pas un appel
    par créneau : c'est ce qui distingue ce mode du mode « llm »."""
    calls = []
    monkeypatch.setattr(beatsync, "_call_llm",
                        lambda pp, n, seed, model, style="court": (calls.append(n) or ["X"]))
    edl = make_edl([0.0, 0.4, 1.8, 3.4, 5.0])
    apply_subtitles(edl, unique_config(), seed=1)
    assert calls == [1]


def test_unique_mode_gives_a_different_punchline_per_seed(monkeypatch):
    """La promesse du mode : un lot de N variantes donne N punchlines. Chaque
    variante a sa seed, et la seed atteint le prompt. Utilise un EDL sur plusieurs
    créneaux (4, avec min_dur=1.4) pour assurer que le chemin llm produit 4
    punchlines différentes — ce mode n'en demande qu'une : discriminant."""
    monkeypatch.setattr(beatsync, "_call_llm",
                        lambda pp, n, seed, model, style="court": [f"PUNCH {seed}-{i}" for i in range(n)])
    # EDL de 4.8 s sur 4 segments → 4 créneaux avec min_dur=1.4
    edl_a = make_edl([0.0, 1.6, 3.2, 4.8])
    edl_b = make_edl([0.0, 1.6, 3.2, 4.8])
    out_a = apply_subtitles(edl_a, unique_config(), seed=11)
    out_b = apply_subtitles(edl_b, unique_config(), seed=22)
    # Mode llm_unique : tous les segments de même vidéo ont la même caption
    captions_a = [e["caption"] for e in out_a]
    captions_b = [e["caption"] for e in out_b]
    assert len(set(captions_a)) == 1, f"Attendu 1 caption unique, got {len(set(captions_a))}"
    assert len(set(captions_b)) == 1, f"Attendu 1 caption unique, got {len(set(captions_b))}"
    # Mais les seeds diffèrent → punchlines différentes entre vidéos
    assert captions_a[0] != captions_b[0]


def test_unique_mode_degrades_to_no_text_when_the_llm_fails(monkeypatch):
    """L'usine ne bloque jamais sur le LLM : la vidéo sort sans texte. Ce test
    garde spécifiquement le code `text = lines[0] if lines else ""`, mutation
    clé du mode llm_unique."""
    def boom(pp, n, seed, model):
        raise RuntimeError("LM Studio éteint")

    monkeypatch.setattr(beatsync, "_call_llm", boom)
    edl = make_edl([0.0, 0.4, 0.8])
    out = apply_subtitles(edl, unique_config(), seed=1)
    assert [e["caption"] for e in out] == ["", "", ""]


def test_unique_mode_does_not_build_caption_slots(monkeypatch):
    """Pas de créneaux dans ce mode : le texte ne change jamais, `min_dur` n'a
    rien à découper."""
    monkeypatch.setattr(beatsync, "_call_llm", lambda pp, n, seed, model, style="court": ["X"])
    monkeypatch.setattr(beatsync, "assign_caption_slots",
                        lambda edl, min_dur: pytest.fail("créneaux calculés à tort"))
    edl = make_edl([0.0, 0.4, 0.8])
    out = apply_subtitles(edl, unique_config(), seed=1)
    assert all("caption_slot" not in e for e in out)


def test_unique_mode_disabled_leaves_the_edl_alone():
    edl = make_edl([0.0, 0.4])
    out = apply_subtitles(edl, unique_config(enabled=False), seed=1)
    assert all("caption" not in e for e in out)


def test_unknown_mode_degrades_to_llm(monkeypatch):
    """Dégradation sûre : une valeur inattendue ne fait pas planter la génération."""
    monkeypatch.setattr(beatsync, "_call_llm", lambda *a, **k: ["A", "B", "C", "D"])
    edl = make_edl([0.0, 0.4, 1.8, 3.4])
    config = {**DEFAULT, "subtitles": {**DEFAULT["subtitles"],
                                       "enabled": True, "mode": "n'importe quoi",
                                       "preprompt": "test"}}
    out = apply_subtitles(edl, config, seed=1)
    assert all(e["caption"] for e in out)


# --- Échappement drawtext ---------------------------------------------------


def test_drawtext_escape_handles_newlines():
    """Un retour à la ligne réel casserait le parseur de filtergraph ; drawtext
    attend la séquence à deux caractères."""
    assert beatsync._drawtext_escape("HAUT\nBAS") == "HAUT\\nBAS"


def test_drawtext_escape_handles_quotes_and_specials():
    escaped = beatsync._drawtext_escape("c'est 100% : oui")
    for ch in ("'", "%", ":"):
        assert f"\\{ch}" in escaped


# --- Accroche longue du mode « une punchline générée » ----------------------


def test_long_style_uses_a_different_system_prompt():
    """Les deux styles ne doivent pas partager la même consigne : le mode
    défilant impose 2 à 6 mots, l'accroche unique a le temps d'être lue."""
    court = beatsync._PUNCHLINE_SYSTEMS["court"]
    long_ = beatsync._PUNCHLINE_SYSTEMS["long"]
    assert court != long_
    assert "2 à 6 mots" in court
    # L'accroche longue est en DEUX lignes qui s'opposent : c'est sa forme, pas
    # seulement sa longueur.
    assert "\\n" in long_ or "ligne" in long_


def test_unique_mode_asks_for_the_long_style(monkeypatch):
    """Le mode « une punchline générée » doit demander l'accroche longue —
    sinon la fonctionnalité se réduit à une punchline courte figée."""
    vus = []
    monkeypatch.setattr(beatsync, "_call_llm",
                        lambda pp, n, seed, model, style="court":
                        (vus.append(style) or ["X"]))
    apply_subtitles(make_edl([0.0, 0.4, 0.8]), unique_config(), seed=1)
    assert vus == ["long"]


def test_scrolling_mode_keeps_the_short_style(monkeypatch):
    """Aucune régression sur les niches existantes : à la coupe, un texte long
    serait illisible."""
    vus = []
    monkeypatch.setattr(beatsync, "_call_llm",
                        lambda pp, n, seed, model, style="court":
                        (vus.append(style) or ["A", "B", "C", "D"]))
    config = {**DEFAULT, "subtitles": {**DEFAULT["subtitles"], "enabled": True,
                                       "mode": "llm", "preprompt": "test"}}
    apply_subtitles(make_edl([0.0, 1.6, 3.2, 4.8]), config, seed=1)
    assert vus == ["court"]


def test_cache_does_not_mix_the_two_styles(tmp_path, monkeypatch):
    """Même préprompt, même seed, même count=1 : sans le style dans la clé, le
    mode long recevrait l'accroche COURTE déjà en cache."""
    monkeypatch.setattr(beatsync, "_call_llm",
                        lambda pp, n, seed, model, style="court":
                        [f"texte {style}"])
    court = generate_punchlines("gym", 1, 7, tmp_path, style="court")
    long_ = generate_punchlines("gym", 1, 7, tmp_path, style="long")
    assert court == ["texte court"]
    assert long_ == ["texte long"]


def test_existing_short_caches_stay_valid(tmp_path, monkeypatch):
    """Le style « court » ne doit RIEN ajouter à la clé. On écrit ici un fichier
    de cache à l'ANCIENNE clé (celle d'avant l'ajout du style) et on vérifie
    qu'il est encore lu : sinon tous les caches déjà sur disque seraient
    invalidés et une même seed ne rendrait plus la même punchline qu'avant —
    l'invariant de reproductibilité du projet. Comparer deux appels « court »
    entre eux ne prouverait rien, les deux clés bougeant ensemble."""
    import hashlib
    import json

    ancienne_cle = hashlib.md5(
        f"{beatsync._llm_backend()}|m|gym|1|7".encode()).hexdigest()
    (tmp_path / f"{ancienne_cle}.json").write_text(
        json.dumps({"punchlines": ["DEJA EN CACHE"]}), encoding="utf-8")

    def jamais(*a, **k):
        raise AssertionError("le LLM ne doit pas être appelé : le cache existe")

    monkeypatch.setattr(beatsync, "_call_llm", jamais)
    assert generate_punchlines("gym", 1, 7, tmp_path, model="m") == ["DEJA EN CACHE"]
