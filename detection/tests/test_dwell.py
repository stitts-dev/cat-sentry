from helpers import det, ts

from catsentry.config import ThresholdsConfig
from catsentry.dwell import DwellEngine
from catsentry.zones import ZoneMap

ZONE_A = [(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)]
ZONE_B = [(0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 1.0)]
ZONES = {"zone_a": ZONE_A, "zone_b": ZONE_B}

BBOX_A = (0.1, 0.4, 0.1, 0.1)  # bottom-center (0.15, 0.5) -> zone_a
BBOX_B = (0.6, 0.4, 0.1, 0.1)  # bottom-center (0.65, 0.5) -> zone_b
BBOX_NONE = (0.1, 1.2, 0.05, 0.05)  # bottom-center (0.125, 1.25) -> outside both


def make_engine(dwell_seconds: float = 2.0, max_missing_frames: int = 1) -> DwellEngine:
    thresholds = ThresholdsConfig(
        confidence=0.5,
        dwell_seconds=dwell_seconds,
        squat_seconds=3.0,
        squat_aspect_ratio=0.6,
        centroid_epsilon=0.02,
        escalate_seconds=5.0,
    )
    return DwellEngine(ZoneMap(ZONES), thresholds, max_missing_frames=max_missing_frames)


def test_candidate_evaporates_if_zone_left_before_dwell_threshold():
    engine = make_engine(dwell_seconds=2.0)
    events = []
    events += engine.update(ts(0), [det(1, BBOX_A)])
    events += engine.update(ts(1), [det(1, BBOX_A)])  # elapsed=1 < 2, still pending
    events += engine.update(ts(1.5), [det(1, BBOX_NONE)])  # leaves before confirming
    assert events == []


def test_zone_enter_fires_at_dwell_threshold_backdated_to_first_sighting():
    engine = make_engine(dwell_seconds=2.0)
    events = []
    events += engine.update(ts(0), [det(1, BBOX_A, confidence=0.81)])
    events += engine.update(ts(1), [det(1, BBOX_A, confidence=0.82)])
    assert events == []

    events += engine.update(ts(2), [det(1, BBOX_A, confidence=0.83)])
    assert len(events) == 1
    event = events[0]
    assert event["type"] == "zone_enter"
    assert event["zone"] == "zone_a"
    assert event["cat_id"] is None
    assert event["confidence"] == 0.81  # from the *first* sighting, not the confirming frame
    assert event["bbox"] == list(BBOX_A)
    assert event["ts"] == "2026-08-19T00:00:00Z"  # backdated to when the candidacy started
    assert event["snapshot_path"] is None


def test_no_duplicate_events_while_steady_in_confirmed_zone():
    engine = make_engine(dwell_seconds=1.0)
    engine.update(ts(0), [det(1, BBOX_A)])
    events = engine.update(ts(1), [det(1, BBOX_A)])  # confirms here
    assert len(events) == 1

    for i in range(2, 6):
        events = engine.update(ts(i), [det(1, BBOX_A)])
        assert events == []


def test_zone_exit_fires_immediately_using_last_confirmed_sighting():
    engine = make_engine(dwell_seconds=1.0)
    engine.update(ts(0), [det(1, BBOX_A, confidence=0.7)])
    engine.update(ts(1), [det(1, BBOX_A, confidence=0.71)])  # confirms
    events = engine.update(ts(2), [det(1, BBOX_A, confidence=0.72)])  # last live sighting in zone
    assert events == []

    events = engine.update(ts(3), [det(1, BBOX_NONE, confidence=0.5)])
    assert len(events) == 1
    event = events[0]
    assert event["type"] == "zone_exit"
    assert event["zone"] == "zone_a"
    assert event["confidence"] == 0.72  # last live sighting *inside* the zone, not this frame's
    assert event["bbox"] == list(BBOX_A)
    assert event["ts"] == "2026-08-19T00:00:03Z"  # exit ts is "now", never backdated


def test_switching_candidate_zone_before_confirmation_restarts_timer():
    engine = make_engine(dwell_seconds=2.0)
    engine.update(ts(0), [det(1, BBOX_A)])  # candidate zone_a since t0
    events = engine.update(ts(1), [det(1, BBOX_B)])  # switches to zone_b before zone_a confirms
    assert events == []  # zone_a was never confirmed, so no exit either

    events = engine.update(ts(2), [det(1, BBOX_B)])  # 1s into zone_b candidacy -> not enough
    assert events == []

    events = engine.update(ts(3), [det(1, BBOX_B)])  # 2s into zone_b candidacy -> confirms
    assert len(events) == 1
    assert events[0]["zone"] == "zone_b"
    assert events[0]["ts"] == "2026-08-19T00:00:01Z"  # backdated to when zone_b candidacy started


def test_track_isolation_transitions_do_not_leak_between_tracks():
    engine = make_engine(dwell_seconds=1.0, max_missing_frames=5)
    engine.update(ts(0), [det(1, BBOX_A), det(2, BBOX_B)])
    events = engine.update(ts(1), [det(1, BBOX_A), det(2, BBOX_B)])
    assert [e["zone"] for e in events] == ["zone_a", "zone_b"]  # both confirm, in input order

    # Only track 1 leaves; track 2 stays confirmed and silent.
    events = engine.update(ts(2), [det(1, BBOX_NONE), det(2, BBOX_B)])
    assert len(events) == 1
    assert events[0]["type"] == "zone_exit"
    assert events[0]["zone"] == "zone_a"


def test_disappear_within_tolerance_preserves_confirmed_state():
    engine = make_engine(dwell_seconds=1.0, max_missing_frames=2)
    engine.update(ts(0), [det(1, BBOX_A)])
    engine.update(ts(1), [det(1, BBOX_A)])  # confirms, entered_at = ts(0)

    events = engine.update(ts(2), [])  # missing frame 1 of 2 tolerated
    assert events == []

    events = engine.update(ts(3), [det(1, BBOX_A)])  # reappears within tolerance
    assert events == []  # steady -- no fresh enter, no exit
    assert engine.dwell_seconds(1, ts(3)) == 3.0  # entered_at unchanged across the gap


def test_disappear_beyond_tolerance_resets_and_reentry_is_a_fresh_visit():
    engine = make_engine(dwell_seconds=1.0, max_missing_frames=1)
    engine.update(ts(0), [det(1, BBOX_A, confidence=0.6)])
    engine.update(ts(1), [det(1, BBOX_A, confidence=0.61)])  # confirms

    events = engine.update(ts(2), [])  # missing frame 1 (tolerated, 1 <= 1)
    assert events == []

    events = engine.update(ts(3), [])  # missing frame 2 -> exceeds tolerance -> reset
    assert len(events) == 1
    assert events[0]["type"] == "zone_exit"
    assert events[0]["confidence"] == 0.61  # last known sighting before it vanished

    assert engine.dwell_seconds(1, ts(3)) is None  # state fully reset

    # Reappearing needs a fresh full dwell period -- one frame isn't enough.
    events = engine.update(ts(4), [det(1, BBOX_A, confidence=0.9)])
    assert events == []
    events = engine.update(ts(5), [det(1, BBOX_A, confidence=0.9)])
    assert len(events) == 1
    assert events[0]["type"] == "zone_enter"


def test_dwell_seconds_helper_is_none_until_confirmed():
    engine = make_engine(dwell_seconds=5.0)
    assert engine.dwell_seconds(1, ts(0)) is None  # unknown track

    engine.update(ts(0), [det(1, BBOX_A)])
    assert engine.dwell_seconds(1, ts(1)) is None  # still just a candidate, not confirmed


def test_reset_clears_all_track_state():
    engine = make_engine(dwell_seconds=1.0)
    engine.update(ts(0), [det(1, BBOX_A)])
    engine.update(ts(1), [det(1, BBOX_A)])  # confirms
    assert engine.confirmed_zone(1) == "zone_a"

    engine.reset()

    assert engine.confirmed_zone(1) is None
    assert engine.dwell_seconds(1, ts(1)) is None
    # A sighting right after reset needs a fresh full dwell period, exactly
    # like a track's first-ever contact -- proving no stale state survived.
    events = engine.update(ts(1), [det(1, BBOX_A)])
    assert events == []
