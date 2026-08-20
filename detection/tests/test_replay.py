"""Golden replay test: docs/contract-catsentry-v1.md event shape end to end,
loading real zones/thresholds from config.sample.yaml and a canned JSON
detection sequence covering debounced entry, a same-frame zone-to-zone
transition, disappearance-triggered exit, and disappear/reappear as a fresh
visit -- see tests/fixtures/replay_sequence.json for the frame-by-frame
scenario.

Driven through `replay_policy` (detection-only, flags.deterrent_enabled=False)
rather than the old DwellEngine-only `replay` -- config.sample.yaml's
shipped bboxes are square (aspect ratio 1.0, never squat-shaped), so
FirePolicy never opens a squat candidacy here and the zone_enter/zone_exit
stream is identical to what bare DwellEngine would have produced; `fires`
stays empty either way since deterrent_enabled is off.
"""

from pathlib import Path

from catsentry.config import load_config
from catsentry.replay import load_sequence, replay_policy

SAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "config.sample.yaml"
SEQUENCE = Path(__file__).resolve().parent / "fixtures" / "replay_sequence.json"

FLOOR_LEFT_BBOX = [0.15, 0.6, 0.1, 0.1]
BOXES_BBOX = [0.425, 0.6, 0.1, 0.1]
FLOOR_RIGHT_BBOX = [0.65, 0.6, 0.1, 0.1]

EXPECTED_EVENTS = [
    {
        "ts": "2026-08-19T00:00:00Z",
        "type": "zone_enter",
        "cat_id": None,
        "zone": "floor_left",
        "confidence": 0.90,
        "bbox": FLOOR_LEFT_BBOX,
        "snapshot_path": None,
    },
    {
        "ts": "2026-08-19T00:00:03Z",
        "type": "zone_exit",
        "cat_id": None,
        "zone": "floor_left",
        "confidence": 0.92,
        "bbox": FLOOR_LEFT_BBOX,
        "snapshot_path": None,
    },
    {
        "ts": "2026-08-19T00:00:01Z",
        "type": "zone_enter",
        "cat_id": None,
        "zone": "boxes",
        "confidence": 0.80,
        "bbox": BOXES_BBOX,
        "snapshot_path": None,
    },
    {
        "ts": "2026-08-19T00:00:05Z",
        "type": "zone_exit",
        "cat_id": None,
        "zone": "boxes",
        "confidence": 0.82,
        "bbox": BOXES_BBOX,
        "snapshot_path": None,
    },
    {
        "ts": "2026-08-19T00:00:03Z",
        "type": "zone_enter",
        "cat_id": None,
        "zone": "boxes",
        "confidence": 0.93,
        "bbox": BOXES_BBOX,
        "snapshot_path": None,
    },
    {
        "ts": "2026-08-19T00:00:06Z",
        "type": "zone_enter",
        "cat_id": None,
        "zone": "floor_right",
        "confidence": 0.83,
        "bbox": FLOOR_RIGHT_BBOX,
        "snapshot_path": None,
    },
]


def test_golden_replay_produces_exact_expected_event_stream():
    cfg = load_config(SAMPLE_CONFIG)  # dwell_seconds=2.0, real zone polygons
    assert cfg.flags.deterrent_enabled is False  # detection-only -- the shipped default
    sequence = load_sequence(SEQUENCE)

    result = replay_policy(
        cfg.zones, cfg.thresholds, cfg.rate_limits, cfg.flags, sequence, max_missing_frames=1
    )

    assert result.events == EXPECTED_EVENTS
    assert result.fires == []
