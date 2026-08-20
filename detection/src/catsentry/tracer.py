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
from ultralytics import YOLO

CAT_CLASS_ID = 15  # COCO class id for "cat"
FALLBACK_FPS = 15.0  # contract default (10-15 fps) when a source reports none


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
    model_path: str = "yolov8n.pt",
    conf: float = 0.5,
    show: bool = False,
    save_path: Path | None = None,
) -> Iterator[list[Detection]]:
    """Yield the list of cat detections for each frame of `source`.

    Opens a display window per frame if `show`; writes an annotated mp4 to
    `save_path` if given. Caller owns printing/consuming the detections.
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
                for xyxy, track_id, box_conf in zip(
                    boxes.xyxy.tolist(), boxes.id.tolist(), boxes.conf.tolist(), strict=True
                ):
                    x1, y1, x2, y2 = xyxy
                    detections.append(
                        Detection(
                            frame_idx=frame_idx,
                            track_id=int(track_id),
                            confidence=round(float(box_conf), 4),
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

            yield detections
    finally:
        if writer is not None:
            writer.release()
        if show:
            cv2.destroyAllWindows()
