"""Replay harness: feed a canned JSON detection sequence through the
detection pipeline and get back what it would have emitted/fired. No video,
no YOLO, no wall clock -- every frame's timestamp comes from the sequence
file, which is what makes this deterministic enough to golden-test.

Sequence format -- a JSON list of frames, oldest first:
[
  {"ts": "2026-08-19T00:00:00Z",
   "detections": [{"track_id": 1, "confidence": 0.9, "bbox": [0.1, 0.6, 0.05, 0.05]}]},
  ...
]

Two entry points:
- `replay` (C2) drives DwellEngine only -- zone_enter/zone_exit events.
- `replay_policy` (C3) drives the full pipeline through FirePolicy --
  zone_enter/zone_exit/squat_suspected/deterrent_fired events, plus any
  catsentry/deterrent/fire commands the sequence produced.

Usage: `catsentry-replay --config config.yaml sequence.json` prints one JSON
line per emitted event, then one per fire command.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from catsentry.config import (
    ConfigError,
    FlagsConfig,
    RateLimitsConfig,
    ThresholdsConfig,
    load_config,
)
from catsentry.dwell import DEFAULT_MAX_MISSING_FRAMES, DwellEngine
from catsentry.policy import FirePolicy
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


@dataclass(frozen=True)
class PolicyReplayResult:
    events: list[dict]
    fires: list[dict]


def replay_policy(
    zones: dict[str, Polygon],
    thresholds: ThresholdsConfig,
    rate_limits: RateLimitsConfig,
    flags: FlagsConfig,
    sequence: list[dict],
    *,
    max_missing_frames: int = DEFAULT_MAX_MISSING_FRAMES,
) -> PolicyReplayResult:
    """Run a canned frame sequence through a fresh FirePolicy, in order,
    returning every event (zone_enter/zone_exit/squat_suspected/
    deterrent_fired) and every catsentry/deterrent/fire command it
    produced -- the C3 "full pipeline to fire decisions" harness."""
    policy = FirePolicy(
        ZoneMap(zones), thresholds, rate_limits, flags, max_missing_frames=max_missing_frames
    )
    events: list[dict] = []
    fires: list[dict] = []
    for frame_idx, frame in enumerate(sequence):
        ts, detections = _parse_frame(frame_idx, frame)
        frame_events, frame_fires = policy.update(ts, detections)
        events.extend(frame_events)
        fires.extend(frame_fires)
    return PolicyReplayResult(events=events, fires=fires)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="catsentry-replay",
        description="Replay a canned JSON detection sequence through the full detection pipeline.",
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

    result = replay_policy(
        cfg.zones, cfg.thresholds, cfg.rate_limits, cfg.flags, load_sequence(args.sequence)
    )
    for event in result.events:
        print(json.dumps(event))
    for fire in result.fires:
        print(json.dumps(fire))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
