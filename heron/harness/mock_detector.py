"""MockDetector for harness self-testing.

Returns deterministic scores keyed off the commit SHA so runs are
bit-for-bit reproducible. Implements the full DetectionResult contract.
"""

from __future__ import annotations

import hashlib


DETECTOR_VERSION = "mock-0.0.1"


def mock_detector(enriched_commit: dict, context: dict) -> dict:
    """Return a deterministic pseudo-random DetectionResult."""
    sha = enriched_commit.get("commit_info", {}).get("commit_id", "")
    # Deterministic float from SHA
    digest = hashlib.sha256(sha.encode("utf-8")).digest()
    confidence = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF

    return {
        "confidence": round(confidence, 6),
        "evidence": [
            {
                "tag": "mock_signal",
                "weight": round(confidence, 6),
                "fields": {"source": "mock_detector", "sha": sha[:12]},
                "prose": f"Mock signal with deterministic score {confidence:.4f}",
            }
        ],
        "signals_evaluated": ["mock"],
        "detector_version": DETECTOR_VERSION,
    }
