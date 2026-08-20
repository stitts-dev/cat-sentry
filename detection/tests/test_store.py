"""EventStore tests. Every test but the snapshot ones calls save() with no
frame, so they never touch cv2 -- only test_save_with_frame_* below builds a
real image array (see store.py's module docstring for why that isolation
matters)."""

from __future__ import annotations

from pathlib import Path

from catsentry.store import EventStore

EVENT = {
    "ts": "2026-08-19T21:04:00Z",
    "type": "zone_enter",
    "cat_id": None,
    "zone": "floor_left",
    "confidence": 0.87,
    "bbox": [0.41, 0.62, 0.11, 0.09],
    "snapshot_path": None,
}


def make_store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "events.db", events_dir=tmp_path / "events")


def test_save_without_frame_inserts_row_and_returns_event_unchanged(tmp_path):
    store = make_store(tmp_path)

    saved = store.save(EVENT)

    assert saved == EVENT
    rows = store.all_events()
    assert len(rows) == 1
    row = rows[0]
    assert row["ts"] == EVENT["ts"]
    assert row["type"] == "zone_enter"
    assert row["cat_id"] is None
    assert row["zone"] == "floor_left"
    assert row["confidence"] == 0.87
    assert (row["bbox_x"], row["bbox_y"], row["bbox_w"], row["bbox_h"]) == (0.41, 0.62, 0.11, 0.09)
    assert row["snapshot_path"] is None


def test_save_without_bbox_stores_null_bbox_columns(tmp_path):
    store = make_store(tmp_path)
    event = {"ts": "2026-08-19T21:04:00Z", "type": "all_clear"}

    store.save(event)

    row = store.all_events()[0]
    assert (row["bbox_x"], row["bbox_y"], row["bbox_w"], row["bbox_h"]) == (None, None, None, None)
    assert row["zone"] is None
    assert row["confidence"] is None


def test_multiple_saves_preserve_insertion_order(tmp_path):
    store = make_store(tmp_path)
    store.save({**EVENT, "type": "zone_enter"})
    store.save({**EVENT, "type": "zone_exit"})

    rows = store.all_events()
    assert [r["type"] for r in rows] == ["zone_enter", "zone_exit"]


def test_save_with_frame_writes_jpeg_snapshot_under_date_directory(tmp_path):
    import numpy as np

    store = make_store(tmp_path)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    saved = store.save(EVENT, frame=frame)

    expected_path = tmp_path / "events" / "2026-08-19" / "210400.jpg"
    assert expected_path.exists()
    assert expected_path.read_bytes()[:2] == b"\xff\xd8"  # JPEG magic bytes
    assert saved["snapshot_path"] == expected_path.as_posix()


def test_save_with_frame_records_snapshot_path_in_row(tmp_path):
    import numpy as np

    store = make_store(tmp_path)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)

    store.save(EVENT, frame=frame)

    expected_path = tmp_path / "events" / "2026-08-19" / "210400.jpg"
    row = store.all_events()[0]
    assert row["snapshot_path"] == expected_path.as_posix()


def test_save_does_not_mutate_caller_event_dict(tmp_path):
    import numpy as np

    store = make_store(tmp_path)
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    original = dict(EVENT)

    store.save(EVENT, frame=frame)

    assert EVENT == original  # caller's dict untouched; save() returned a copy
