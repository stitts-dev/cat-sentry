"""Tests for C3's deterrent policy: squat heuristic -> WARNED/ESCALATED,
and the safety rails that gate every fire. Two styles, matching the issue's
acceptance criteria:

- Golden replay fixtures (tests/fixtures/*.json) driven through
  `replay_policy`, mirroring test_replay.py's C2 golden test but exercising
  the full pipeline to fire decisions.
- Direct FirePolicy unit tests (matching test_dwell.py's style) for cases
  that are awkward to express as a canned JSON sequence: a second track
  colliding with the global cooldown, and the hourly-cap property test.
"""

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from catsentry.config import FlagsConfig, RateLimitsConfig, ThresholdsConfig, load_config
from catsentry.policy import EVENT_DETERRENT_FIRED, EVENT_SQUAT_SUSPECTED, FirePolicy, TrackState
from catsentry.replay import load_sequence, replay_policy
from catsentry.tracer import Detection
from catsentry.zones import PROTECTED_ZONE, ZoneMap, bbox_bottom_center, point_in_polygon

SAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "config.sample.yaml"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

BASE_TS = datetime(2026, 8, 19, 0, 0, 0, tzinfo=UTC)


def ts(seconds: float) -> datetime:
    return BASE_TS + timedelta(seconds=seconds)


def det(
    track_id: int, bbox: tuple[float, float, float, float], confidence: float = 0.9
) -> Detection:
    return Detection(frame_idx=0, track_id=track_id, confidence=confidence, bbox=bbox)


def _event_types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


# -- golden replay fixtures --------------------------------------------------


def test_squat_escalation_golden_sound_then_air_then_cooldown_blocks_refire():
    cfg = load_config(SAMPLE_CONFIG)  # dwell=2, squat=3, escalate=5, cooldown=15min, cap=4
    flags = replace(cfg.flags, deterrent_enabled=True)
    sequence = load_sequence(FIXTURES / "squat_escalation_sequence.json")

    result = replay_policy(cfg.zones, cfg.thresholds, cfg.rate_limits, flags, sequence)

    assert len(result.fires) == 2  # exactly sound + air -- the t=11 tail produces no third fire
    sound, air = result.fires
    fixture_bbox = (0.15, 0.65, 0.10, 0.05)
    expected_target = list(bbox_bottom_center(fixture_bbox))

    assert sound["level"] == "sound"
    assert sound["ts"] == "2026-08-19T00:00:05Z"  # squat_since(2) + squat_seconds(3)
    assert sound["target"] == expected_target
    assert sound["duration_ms"] == 1500
    assert "dwell=5.0s" in sound["reason"] and "zone=floor_left" in sound["reason"]

    assert air["level"] == "air"
    assert air["ts"] == "2026-08-19T00:00:10Z"  # warned_at(5) + escalate_seconds(5)
    assert air["target"] == expected_target
    assert air["duration_ms"] == 800
    assert "dwell=10.0s" in air["reason"] and "zone=floor_left" in air["reason"]

    types = _event_types(result.events)
    assert types.count(EVENT_SQUAT_SUSPECTED) == 1
    assert types.count(EVENT_DETERRENT_FIRED) == 2
    squat_event = next(e for e in result.events if e["type"] == EVENT_SQUAT_SUSPECTED)
    assert squat_event["ts"] == "2026-08-19T00:00:02Z"  # backdated to when squat began
    assert squat_event["zone"] == "floor_left"


def test_detection_only_mode_emits_zero_fires_but_events_still_flow():
    cfg = load_config(SAMPLE_CONFIG)
    assert cfg.flags.deterrent_enabled is False  # the shipped default -- detection-only
    sequence = load_sequence(FIXTURES / "squat_escalation_sequence.json")

    result = replay_policy(cfg.zones, cfg.thresholds, cfg.rate_limits, cfg.flags, sequence)

    assert result.fires == []
    types = _event_types(result.events)
    assert EVENT_SQUAT_SUSPECTED in types  # detection isn't gated by the deterrent flag
    assert EVENT_DETERRENT_FIRED not in types  # nothing actually fired


def test_wobbly_cat_never_reaches_suspect():
    cfg = load_config(SAMPLE_CONFIG)
    flags = replace(cfg.flags, deterrent_enabled=True)
    sequence = load_sequence(FIXTURES / "wobbly_cat_sequence.json")

    result = replay_policy(cfg.zones, cfg.thresholds, cfg.rate_limits, flags, sequence)

    assert result.fires == []
    assert EVENT_SQUAT_SUSPECTED not in _event_types(result.events)


def test_protected_zone_squat_never_fires():
    cfg = load_config(SAMPLE_CONFIG)
    flags = replace(cfg.flags, deterrent_enabled=True)  # isolate the zone rail specifically
    sequence = load_sequence(FIXTURES / "protected_zone_squat_sequence.json")

    result = replay_policy(cfg.zones, cfg.thresholds, cfg.rate_limits, flags, sequence)

    assert result.fires == []  # hard invariant even though the squat is genuinely detected
    types = _event_types(result.events)
    assert EVENT_SQUAT_SUSPECTED in types  # detection still happens in the boxes zone
    assert EVENT_DETERRENT_FIRED not in types


# -- direct FirePolicy unit tests --------------------------------------------

FAST_ZONES = {"zone_a": [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]}
SQUAT_BBOX = (0.1, 0.1, 0.1, 0.05)  # ratio 0.5, stationary centroid


def _fast_thresholds(**overrides) -> ThresholdsConfig:
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


def test_global_cooldown_blocks_a_different_tracks_sound_fire():
    thresholds = _fast_thresholds()
    rate_limits = RateLimitsConfig(max_fires_per_hour=10, cooldown_minutes=5.0)
    flags = FlagsConfig(deterrent_enabled=True)
    policy = FirePolicy(ZoneMap(FAST_ZONES), thresholds, rate_limits, flags, max_missing_frames=1)

    all_events: list[dict] = []
    all_fires: list[dict] = []

    def step(t: float, detections: list[Detection]) -> None:
        events, fires = policy.update(ts(t), detections)
        all_events.extend(events)
        all_fires.extend(fires)

    # Track 1 escalates fully: sound at t=2, air at t=3.
    step(0, [det(1, SQUAT_BBOX)])
    step(1, [det(1, SQUAT_BBOX)])
    step(2, [det(1, SQUAT_BBOX)])
    step(3, [det(1, SQUAT_BBOX)])
    assert len(all_fires) == 2
    assert [f["level"] for f in all_fires] == ["sound", "air"]
    assert policy.track_state(1) == TrackState.COOLDOWN

    # Track 2 starts its own episode moments later, well inside the 5-minute
    # global cooldown track 1's air fire (t=3) just started.
    step(4, [det(2, SQUAT_BBOX)])
    step(5, [det(2, SQUAT_BBOX)])
    step(6, [det(2, SQUAT_BBOX)])  # would confirm SUSPECT and attempt sound here

    assert len(all_fires) == 2  # track 2's sound was blocked -- no new fire
    squat_events_track2 = [
        e
        for e in all_events
        if e["type"] == EVENT_SQUAT_SUSPECTED and e["bbox"] == list(SQUAT_BBOX)
    ]
    assert len(squat_events_track2) == 2  # detection still fired for both tracks
    assert EVENT_DETERRENT_FIRED in _event_types(all_events)


def test_hourly_cap_enforced_across_many_independent_episodes():
    """Property-style: however many separate squat episodes occur inside a
    rolling hour, the number of fires never exceeds max_fires_per_hour."""
    thresholds = _fast_thresholds(escalate_seconds=1000.0)  # never escalate -- sound-only episodes
    max_fires_per_hour = 3
    rate_limits = RateLimitsConfig(max_fires_per_hour=max_fires_per_hour, cooldown_minutes=0.01)
    flags = FlagsConfig(deterrent_enabled=True)
    policy = FirePolicy(ZoneMap(FAST_ZONES), thresholds, rate_limits, flags, max_missing_frames=1)

    rng = random.Random(1234)
    total_fires = 0
    for episode in range(20):
        track_id = episode + 1  # fresh track per episode -- isolates each attempt
        base = episode * 10.0 + rng.uniform(0, 2)
        _, fires = policy.update(ts(base), [det(track_id, SQUAT_BBOX)])
        total_fires += len(fires)
        _, fires = policy.update(ts(base + 1), [det(track_id, SQUAT_BBOX)])  # confirms zone
        total_fires += len(fires)
        _, fires = policy.update(ts(base + 2), [det(track_id, SQUAT_BBOX)])  # confirms squat, fires
        total_fires += len(fires)

        assert total_fires <= max_fires_per_hour

    assert total_fires == max_fires_per_hour  # the cap actually got hit, not just never violated


def test_rail_violation_reasons_are_specific():
    thresholds = _fast_thresholds()
    rate_limits = RateLimitsConfig(max_fires_per_hour=1, cooldown_minutes=5.0)

    disabled = FirePolicy(
        ZoneMap(FAST_ZONES), thresholds, rate_limits, FlagsConfig(deterrent_enabled=False)
    )
    assert disabled._rail_violation("zone_a", ts(0), check_cooldown=True) == (
        "deterrent disabled (detection-only mode)"
    )

    enabled = FirePolicy(
        ZoneMap(FAST_ZONES), thresholds, rate_limits, FlagsConfig(deterrent_enabled=True)
    )
    violation = enabled._rail_violation(PROTECTED_ZONE, ts(0), check_cooldown=True)
    assert violation is not None and "protected" in violation


def test_protected_zone_point_in_polygon_sanity_matches_sample_config():
    # Cheap cross-check that the protected-zone fixture's bbox really does
    # land inside "boxes" per config.sample.yaml -- if this ever drifts the
    # protected-zone golden test above would silently stop testing what it
    # claims to.
    cfg = load_config(SAMPLE_CONFIG)
    fixture_bbox = (0.40, 0.65, 0.10, 0.05)
    point = bbox_bottom_center(fixture_bbox)
    assert point_in_polygon(point, cfg.zones[PROTECTED_ZONE])
