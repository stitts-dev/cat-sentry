"""SQLite event store: mirrors the catsentry/event contract payload as rows,
writing an optional JPEG snapshot alongside each one it's given a frame for.

# ponytail: cv2 is only imported inside _write_snapshot, and only reached
# when a caller actually passes a frame -- tests that exercise pure event
# storage (the common case) never import cv2, matching the cv2-stays-out-
# of-pure-logic boundary the rest of the pipeline already keeps (see
# zones.py/dwell.py docstrings; tracer.py is the one module allowed to own
# cv2 for capture/tracking, this is the other for snapshot writing).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    cat_id TEXT,
    zone TEXT,
    confidence REAL,
    bbox_x REAL,
    bbox_y REAL,
    bbox_w REAL,
    bbox_h REAL,
    snapshot_path TEXT
)
"""

JPEG_QUALITY = 85


def _write_snapshot(frame: np.ndarray, events_dir: Path, ts: datetime) -> Path:
    """JPEG to events/YYYY-MM-DD/HHMMSS.jpg under `events_dir`, matching the
    contract's snapshot_path convention. The only place in this module that
    touches cv2 (see module docstring)."""
    import cv2

    day_dir = events_dir / ts.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"{ts.strftime('%H%M%S')}.jpg"
    cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return path


class EventStore:
    """One SQLite file + one events/ snapshot tree.

    `save` mirrors an event dict (shaped per docs/contract-catsentry-v1.md)
    into a row. If `frame` is given, a JPEG is written first and
    `snapshot_path` is filled in on both the row and the returned dict, so
    callers publishing the event onward (MQTT, ntfy) see the same
    snapshot_path the store just wrote.
    """

    def __init__(self, db_path: str | Path, events_dir: str | Path = "events") -> None:
        self._events_dir = Path(events_dir)
        # check_same_thread=False: the service constructs the store on the
        # main thread but only its output worker thread ever calls save()/
        # close() -- single-writer access, no locking needed. Without this,
        # sqlite3 raises ProgrammingError on the first cross-thread save.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def save(self, event: dict, frame: np.ndarray | None = None) -> dict:
        event = dict(event)
        if frame is not None:
            ts = datetime.fromisoformat(event["ts"])
            snapshot_path = _write_snapshot(frame, self._events_dir, ts)
            event["snapshot_path"] = snapshot_path.as_posix()

        bbox = event.get("bbox") or (None, None, None, None)
        self._conn.execute(
            "INSERT INTO events "
            "(ts, type, cat_id, zone, confidence, bbox_x, bbox_y, bbox_w, bbox_h, snapshot_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event["ts"],
                event["type"],
                event.get("cat_id"),
                event.get("zone"),
                event.get("confidence"),
                *bbox,
                event.get("snapshot_path"),
            ),
        )
        self._conn.commit()
        return event

    def all_events(self) -> list[sqlite3.Row]:
        """Every stored row, oldest first -- test/inspection helper."""
        self._conn.row_factory = sqlite3.Row
        return list(self._conn.execute("SELECT * FROM events ORDER BY id"))
