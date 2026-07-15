"""Tests des punchlines incrustées : découpage en créneaux (pur), génération
(mockée : cache + dégradation), intégration EDL, et drawtext au rendu."""

import numpy as np
import pytest

import beatsync
from beatsync import (apply_subtitles, assign_caption_slots, generate_punchlines,
                      _segment_filters)

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
                        lambda pp, n, seed, model: (calls.append(1) or ["A", "B", "C"][:n]))
    cache = tmp_path / "subs"
    a = generate_punchlines("motivation gym", 3, seed=42, cache_dir=cache)
    b = generate_punchlines("motivation gym", 3, seed=42, cache_dir=cache)
    assert a == ["A", "B", "C"] and b == a
    assert len(calls) == 1  # 2e appel servi par le cache


def test_generate_seed_changes_cache_key(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(beatsync, "_call_llm",
                        lambda pp, n, seed, model: (calls.append(seed) or [f"s{seed}"] * n))
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
                        lambda pp, n, seed, model: [f"punch{i}" for i in range(n)])
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
