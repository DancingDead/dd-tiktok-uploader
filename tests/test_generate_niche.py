"""Tests des helpers purs de generate_niche : plan de variantes, nom de fichier."""

from generate_niche import plan_variants, video_stem


def test_plan_variants_deterministic_and_distinct_seeds():
    tracks = ["tracks/a.mp3", "tracks/b.wav"]
    a = plan_variants(tracks, 3, base_seed=42)
    b = plan_variants(tracks, 3, base_seed=42)
    assert a == b                                   # reproductible à base_seed égal
    assert len(a) == 3
    seeds = [s for _, s in a]
    assert len(set(seeds)) == 3                      # seeds distinctes → variantes distinctes
    assert all(t in tracks for t, _ in a)            # morceaux tirés parmi ceux de la niche


def test_plan_variants_different_base_seed_differs():
    tracks = ["tracks/a.mp3", "tracks/b.wav", "tracks/c.mp3"]
    assert plan_variants(tracks, 3, 1) != plan_variants(tracks, 3, 2)


def test_plan_variants_empty_tracks():
    assert plan_variants([], 3, base_seed=1) == []


def test_video_stem_filesystem_safe():
    stem = video_stem("naruto-edits", "tracks/NLCK & HANNAH - Virus V4 (Radio Edit).wav",
                      seed=123, created_at="2026-07-09T12:00:00")
    assert "/" not in stem and " " not in stem
    assert stem.endswith("_s123")
    assert "naruto-edits" in stem
