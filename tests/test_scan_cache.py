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
