"""Shared scaffolding for the detection test suite -- a fixed base
timestamp, a `Detection` builder, and fast `ThresholdsConfig` defaults, all
near-verbatim copy-pasted across test_dwell.py/test_policy.py/test_service.py
before this file existed. Plain module-level helpers, not pytest fixtures --
nothing here needs request-scoping, so a plain `from helpers import ...`
(tests/ has no __init__.py, so pytest's default import mode puts it on
sys.path) is the least-magic way to share them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from catsentry.config import ThresholdsConfig
from catsentry.tracer import Detection

BASE_TS = datetime(2026, 8, 19, 0, 0, 0, tzinfo=UTC)


def ts(seconds: float) -> datetime:
    return BASE_TS + timedelta(seconds=seconds)


def det(
    track_id: int,
    bbox: tuple[float, float, float, float] = (0.1, 0.1, 0.1, 0.05),
    confidence: float = 0.9,
) -> Detection:
    return Detection(frame_idx=0, track_id=track_id, confidence=confidence, bbox=bbox)


def fast_thresholds(**overrides) -> ThresholdsConfig:
    """`ThresholdsConfig` with fast, test-friendly defaults -- pass overrides
    for scenario-specific values, e.g. `fast_thresholds(escalate_seconds=1000.0)`
    to keep a scenario sound-only."""
    base = dict(
        confidence=0.5,
        dwell_seconds=1.0,
        squat_seconds=1.0,
        squat_aspect_ratio=0.6,
        centroid_epsilon=0.02,
        escalate_seconds=1.0,
    )
    base.update(overrides)
    return ThresholdsConfig(**base)
