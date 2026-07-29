"""Cache de scan : round-trip pur + invalidation mtime (scan réel stubé)."""

import numpy as np
import pytest

import beatsync
from beatsync import _apply_scan_payload, _scan_payload, scan_clips


def make_scanned_clip(path):
    return {
        "path": path, "duration": 10.0, "width": 1920, "height": 1080,
        "ratio": 16 / 9,
        "intervals": [{"start": 1.0, "end": 9.0, "motion": 0.1, "presence": 0.8}],
        "interest_x": np.array([0.4, 0.6]),
        "dual": np.array([False, True]),
        "scan_dt": 0.5,
    }


def test_payload_roundtrip(tmp_path):
    clip = make_scanned_clip(tmp_path / "a.mp4")
    payload = _scan_payload(clip)
    restored = {"path": clip["path"], "duration": 10.0, "width": 1920,
                "height": 1080, "ratio": 16 / 9}
    _apply_scan_payload(restored, payload)
    assert restored["intervals"] == clip["intervals"]
    assert np.allclose(restored["interest_x"], clip["interest_x"])
    assert restored["dual"].dtype == bool and list(restored["dual"]) == [False, True]
    assert restored["scan_dt"] == 0.5


def test_cache_hit_and_mtime_invalidation(tmp_path, monkeypatch):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"fake")
    calls = []

    def fake_scan_one(clip):
        calls.append(clip["path"])
        clip.update({k: v for k, v in make_scanned_clip(video).items()
                     if k != "path"})

    monkeypatch.setattr(beatsync, "_scan_one", fake_scan_one)
    cache = tmp_path / "cache"

    clip = {"path": video, "duration": 10.0, "width": 1920, "height": 1080,
            "ratio": 16 / 9}
    scan_clips([dict(clip)], cache_dir=cache)
    scan_clips([dict(clip)], cache_dir=cache)
    assert len(calls) == 1                      # 2e appel servi par le cache

    video.write_bytes(b"fake modifie")          # mtime change
    import os
    os.utime(video, (video.stat().st_atime, video.stat().st_mtime + 10))
    scan_clips([dict(clip)], cache_dir=cache)
    assert len(calls) == 2                      # invalidé, re-scanné


def test_corrupt_cache_treated_as_miss(tmp_path, monkeypatch):
    """Cache tronqué/corrompu (process tué en pleine écriture) : cache miss,
    re-scan sans lever, et le cache est réécrit en JSON valide."""
    import hashlib
    import json

    video = tmp_path / "a.mp4"
    video.write_bytes(b"fake")
    calls = []

    def fake_scan_one(clip):
        calls.append(clip["path"])
        clip.update({k: v for k, v in make_scanned_clip(video).items()
                     if k != "path"})

    monkeypatch.setattr(beatsync, "_scan_one", fake_scan_one)
    cache = tmp_path / "cache"
    cache.mkdir()
    digest = hashlib.md5(str(video).encode()).hexdigest()
    cache_path = cache / f"{digest}.json"
    cache_path.write_text("{pas du json")

    clip = {"path": video, "duration": 10.0, "width": 1920, "height": 1080,
            "ratio": 16 / 9}
    scan_clips([dict(clip)], cache_dir=cache)
    assert len(calls) == 1                      # corrompu => re-scanné, sans lever

    cached = json.loads(cache_path.read_text())  # cache remplacé par du JSON valide
    assert cached["mtime"] == video.stat().st_mtime
    assert cached["scan_dt"] == 0.5

    scan_clips([dict(clip)], cache_dir=cache)
    assert len(calls) == 1                      # et le nouveau cache sert bien


def test_no_cache_dir_means_always_scan(tmp_path, monkeypatch):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"fake")
    calls = []
    monkeypatch.setattr(beatsync, "_scan_one",
                        lambda clip: calls.append(1) or clip.update(
                            {k: v for k, v in make_scanned_clip(video).items()
                             if k != "path"}))
    clip = {"path": video, "duration": 10.0, "width": 1920, "height": 1080,
            "ratio": 16 / 9}
    scan_clips([dict(clip)])
    scan_clips([dict(clip)])
    assert len(calls) == 2


def test_a_cache_entry_without_version_is_a_miss(tmp_path, monkeypatch):
    """Les caches écrits avant la détection des bandes n'ont pas de `crop` :
    il faut re-scanner, pas les lire à moitié."""
    import json

    from beatsync import SCAN_CACHE_VERSION

    path = tmp_path / "a.mp4"
    path.write_bytes(b"x")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    digest = beatsync.hashlib.md5(str(path).encode()).hexdigest()
    (cache_dir / f"{digest}.json").write_text(json.dumps({
        "mtime": path.stat().st_mtime,
        "intervals": [{"start": 0.0, "end": 5.0, "motion": 0.5, "presence": 1.0}],
        "interest_x": [0.5], "dual": [False], "scan_dt": 0.5,
    }))

    scanned = []
    monkeypatch.setattr(beatsync, "_scan_one",
                        lambda clip: (scanned.append(clip["path"].name),
                                      clip.update(intervals=[], interest_x=np.array([0.5]),
                                                  dual=np.array([False]), scan_dt=0.5,
                                                  crop=None)))
    clip = {"path": path, "kind": "video", "duration": 10.0,
            "width": 1920, "height": 1080, "ratio": 16 / 9}
    beatsync.scan_clips([clip], cache_dir=cache_dir)
    assert scanned == ["a.mp4"], "l'entrée sans version aurait dû être ignorée"
    assert SCAN_CACHE_VERSION >= 2
