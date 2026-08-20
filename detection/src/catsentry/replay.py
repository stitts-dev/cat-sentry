"""Replay harness: feed a canned JSON detection sequence through DwellEngine
and get back the events it would have emitted. No video, no YOLO, no wall
clock -- every frame's timestamp comes from the sequence file, which is what
makes this deterministic enough to golden-test.

Sequence format -- a JSON list of frames, oldest first:
[
  {"ts": "2026-08-19T00:00:00Z",
   "detections": [{"track_id": 1, "confidence": 0.9, "bbox": [0.1, 0.6, 0.05, 0.05]}]},
  ...
]

Usage: `catsentry-replay --config config.yaml sequence.json` prints one JSON
event per emitted zone_enter/zone_exit.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from catsentry.config import ConfigError, ThresholdsConfig, load_config
from catsentry.dwell import DEFAULT_MAX_MISSING_FRAMES, DwellEngine
from catsentry.tracer import Detection
from catsentry.zones import Polygon, ZoneMap


def load_sequence(path: str | Path) -> list[dict]:
    return json.loads(Path(path).read_text())


def _parse_frame(frame_idx: int, frame: dict) -> tuple[datetime, list[Detection]]:
    ts = datetime.fromisoformat(frame["ts"])
    detections = [
        Detection(
            frame_idx=frame_idx,
            track_id=d["track_id"],
            confidence=d["confidence"],
            bbox=tuple(d["bbox"]),
        )
        for d in frame["detections"]
    ]
    return ts, detections


def replay(
    zones: dict[str, Polygon],
    thresholds: ThresholdsConfig,
    sequence: list[dict],
    *,
    max_missing_frames: int = DEFAULT_MAX_MISSING_FRAMES,
) -> list[dict]:
    """Run a canned frame sequence through a fresh DwellEngine, in order,
    returning every zone_enter/zone_exit event emitted."""
    engine = DwellEngine(ZoneMap(zones), thresholds, max_missing_frames=max_missing_frames)
    events: list[dict] = []
    for frame_idx, frame in enumerate(sequence):
        ts, detections = _parse_frame(frame_idx, frame)
        events.extend(engine.update(ts, detections))
    return events


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="catsentry-replay",
        description="Replay a canned JSON detection sequence through the zone/dwell engine.",
    )
    parser.add_argument("sequence", type=Path, help="path to a sequence JSON file")
    parser.add_argument("--config", type=Path, required=True, help="path to config.yaml")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    for event in replay(cfg.zones, cfg.thresholds, load_sequence(args.sequence)):
        print(json.dumps(event))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
