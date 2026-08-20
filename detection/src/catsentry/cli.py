"""catsentry CLI: video file / webcam index / stream URL -> cat detections."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from catsentry.config import ConfigError, load_config
from catsentry.tracer import DEFAULT_CONF, DEFAULT_MODEL, SourceError, parse_source, track_cats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="catsentry",
        description="Detect and track cats in a video source using YOLO + ByteTrack.",
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="video file path, webcam index (e.g. 0), or stream URL. "
        "Falls back to config.source.url if --config is given and this is omitted.",
    )
    parser.add_argument(
        "--config", type=Path, help="path to config.yaml (parsed and validated in full)"
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"ultralytics YOLO weights (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=DEFAULT_CONF,
        help=f"detection confidence threshold (default: {DEFAULT_CONF})",
    )
    parser.add_argument("--show", action="store_true", help="render annotated playback window")
    parser.add_argument("--save", type=Path, help="write annotated video to this mp4 path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    source = args.source
    if args.config:
        try:
            cfg = load_config(args.config)
        except ConfigError as exc:
            print(f"config error: {exc}", file=sys.stderr)
            return 1
        source = source or cfg.source.url

    if not source:
        print(
            "error: provide a source (video path / webcam index / stream URL) or --config",
            file=sys.stderr,
        )
        return 2

    frame_count = 0
    detection_count = 0
    try:
        for _frame, detections in track_cats(
            parse_source(source),
            model_path=args.model,
            conf=args.conf,
            show=args.show,
            save_path=args.save,
        ):
            frame_count += 1
            for d in detections:
                detection_count += 1
                print(
                    f"frame={d.frame_idx} track_id={d.track_id} "
                    f"conf={d.confidence:.2f} bbox={list(d.bbox)}"
                )
    except SourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"done: {frame_count} frames, {detection_count} cat detections", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
