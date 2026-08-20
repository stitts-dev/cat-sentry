"""Video source -> pretrained YOLO + ByteTrack -> per-frame cat detections.

# ponytail: single pretrained COCO model (cat = class 15), no fine-tuning and
# no zone/state-machine/deterrent logic yet -- this is just the tracer (C1).
# Those consume this module's output in later issues (C2+).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

CAT_CLASS_ID = 15  # COCO class id for "cat"
FALLBACK_FPS = 15.0  # contract default (10-15 fps) when a source reports none
DEFAULT_MODEL = "yolov8n.pt"  # single source of truth -- cli.py imports these
DEFAULT_CONF = 0.5


class SourceError(RuntimeError):
    """A video source could not be opened or died mid-stream.

    Wraps the exception families cv2/ultralytics actually raise for a bad
    source (missing file, unreachable stream, busy webcam) so callers -- the
    CLI now, the C5 service later -- catch one domain type instead of
    re-deriving that list.
    """


@dataclass(frozen=True)
class Detection:
    frame_idx: int
    track_id: int | None
    confidence: float
    bbox: tuple[float, float, float, float]  # x, y, w, h normalized [0,1], top-left origin


def parse_source(raw: str) -> str | int:
    """cv2.VideoCapture wants an int for a webcam index, a str for a file
    path or stream URL."""
    try:
        return int(raw)
    except ValueError:
        return raw


def _probe_fps(source: str | int) -> float:
    cap = cv2.VideoCapture(source)
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return fps if fps and fps > 1 else FALLBACK_FPS


def track_cats(
    source: str | int,
    *,
    model_path: str = DEFAULT_MODEL,
    conf: float = DEFAULT_CONF,
    show: bool = False,
    save_path: Path | None = None,
) -> Iterator[tuple[np.ndarray, list[Detection]]]:
    """Yield `(frame, detections)` for each frame of `source`. `frame` is the
    raw BGR frame (`result.orig_img`) so callers -- C5's EventStore -- can
    hand it straight to a snapshot write; `detections` is that frame's cat
    detections.

    Opens a display window per frame if `show`; writes an annotated mp4 to
    `save_path` if given. Caller owns printing/consuming the detections.
    Raises SourceError if the source can't be opened or dies mid-stream.
    """
    model = YOLO(model_path)
    writer: cv2.VideoWriter | None = None
    # Probe fps before model.track() opens its own capture on `source` --
    # opening two captures on the same webcam/stream at once is asking for
    # trouble, so grab this first and reuse it when the writer is created.
    save_fps = _probe_fps(source) if save_path is not None else FALLBACK_FPS
    try:
        results = model.track(
            source=source,
            classes=[CAT_CLASS_ID],
            conf=conf,
            persist=True,
            stream=True,
            verbose=False,
        )
        for frame_idx, result in enumerate(results):
            h, w = result.orig_shape
            detections: list[Detection] = []
            boxes = result.boxes
            if boxes is not None and boxes.id is not None:
                # One .tolist() = one GPU->CPU sync per frame; when tracking,
                # each row is [x1, y1, x2, y2, track_id, conf, cls].
                for x1, y1, x2, y2, track_id, box_conf, _cls in boxes.data.tolist():
                    detections.append(
                        Detection(
                            frame_idx=frame_idx,
                            track_id=int(track_id),
                            confidence=round(box_conf, 4),
                            bbox=(
                                round(x1 / w, 4),
                                round(y1 / h, 4),
                                round((x2 - x1) / w, 4),
                                round((y2 - y1) / h, 4),
                            ),
                        )
                    )

            if show or save_path:
                annotated = result.plot()
                if save_path is not None:
                    if writer is None:
                        writer = cv2.VideoWriter(
                            str(save_path),
                            cv2.VideoWriter_fourcc(*"mp4v"),
                            save_fps,
                            (annotated.shape[1], annotated.shape[0]),
                        )
                    writer.write(annotated)
                if show:
                    cv2.imshow("cat-sentry", annotated)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break

            yield result.orig_img, detections
    except (OSError, ConnectionError, RuntimeError) as exc:
        raise SourceError(f"video source {source!r} failed: {exc}") from exc
    finally:
        if writer is not None:
            writer.release()
        if show:
            cv2.destroyAllWindows()
