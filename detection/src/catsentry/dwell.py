"""Per-track zone dwell state machine: IDLE -> IN_ZONE (dwell timer) ->
exit/reset, emitting zone_enter/zone_exit events shaped per
docs/contract-catsentry-v1.md's catsentry/event payload.

Pure logic -- consumes catsentry.tracer.Detection as its only per-frame
input, catsentry.zones.ZoneMap for "where is it", and
catsentry.config.ThresholdsConfig for "how long is a real visit", plus a
caller-supplied frame timestamp. Never reads the wall clock, so replaying a
canned detection sequence (see replay.py) is fully deterministic.

Confirmation is debounced: a bbox landing in a zone starts a *candidate*
that only becomes a confirmed IN_ZONE (and fires zone_enter) once it has
held that same zone for >= thresholds.dwell_seconds. A candidate that moves
zones or disappears before confirming just evaporates -- no event, since
nothing was ever announced. Once confirmed, leaving the zone (by moving
elsewhere or by the track disappearing for too long) fires zone_exit
immediately; there's no debounce on the way out.

# ponytail: this only builds IDLE <-> IN_ZONE. SUSPECT/WARNED/ESCALATED/
# COOLDOWN (squat heuristic, sound/air firing, cooldown) are C3 (#4) -- it
# extends DwellEngine/_TrackState here rather than replacing them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from catsentry.config import ThresholdsConfig
from catsentry.tracer import Detection
from catsentry.zones import ZoneMap

# Consecutive no-detection frames tolerated before a track's state resets.
# Not config-driven yet (ThresholdsConfig has no field for it) -- at the
# contract's 10-15 fps this absorbs a couple of missed/occluded frames
# without mistaking a still-present cat for a fresh visit.
DEFAULT_MAX_MISSING_FRAMES = 5

EVENT_ZONE_ENTER = "zone_enter"
EVENT_ZONE_EXIT = "zone_exit"


@dataclass
class _TrackState:
    confirmed_zone: str | None = None
    entered_at: datetime | None = None
    candidate_zone: str | None = None
    candidate_since: datetime | None = None
    candidate_detection: Detection | None = None
    last_detection: Detection | None = None
    missing_frames: int = 0


def _format_ts(ts: datetime) -> str:
    """ISO 8601 UTC, contract format: '2026-08-19T21:04:00Z' (no
    microseconds, 'Z' rather than '+00:00')."""
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(event_type: str, *, zone: str, detection: Detection, ts: datetime) -> dict:
    return {
        "ts": _format_ts(ts),
        "type": event_type,
        "cat_id": None,  # per-cat ID is phase 2 (contract v1)
        "zone": zone,
        "confidence": detection.confidence,
        "bbox": list(detection.bbox),
        "snapshot_path": None,  # filled in by the event store (C4), not this layer
    }


class DwellEngine:
    """Tracks every cat's zone occupancy across a session.

    Call `update` once per frame with that frame's detections and
    timestamp; it returns the zone_enter/zone_exit events that frame
    produced, in emission order.
    """

    def __init__(
        self,
        zones: ZoneMap,
        thresholds: ThresholdsConfig,
        *,
        max_missing_frames: int = DEFAULT_MAX_MISSING_FRAMES,
    ) -> None:
        self._zones = zones
        self._dwell_seconds = thresholds.dwell_seconds
        self._max_missing_frames = max_missing_frames
        self._tracks: dict[int, _TrackState] = {}

    def dwell_seconds(self, track_id: int, now: datetime) -> float | None:
        """Seconds `track_id` has continuously held its confirmed zone, or
        None if it isn't confirmed in one right now. Exposed so C3's squat
        eligibility check can extend this engine instead of re-deriving it."""
        state = self._tracks.get(track_id)
        if state is None or state.entered_at is None:
            return None
        return (now - state.entered_at).total_seconds()

    def update(self, ts: datetime, detections: list[Detection]) -> list[dict]:
        events: list[dict] = []
        seen_ids = {d.track_id for d in detections if d.track_id is not None}

        # Disappearance first: any previously-tracked ID absent this frame.
        for track_id in list(self._tracks):
            if track_id in seen_ids:
                continue
            state = self._tracks[track_id]
            state.missing_frames += 1
            if state.missing_frames > self._max_missing_frames:
                if state.confirmed_zone is not None and state.last_detection is not None:
                    events.append(
                        _event(
                            EVENT_ZONE_EXIT,
                            zone=state.confirmed_zone,
                            detection=state.last_detection,
                            ts=ts,
                        )
                    )
                del self._tracks[track_id]

        for det in detections:
            if det.track_id is None:
                continue
            events.extend(self._advance(det, ts))

        return events

    def _advance(self, det: Detection, ts: datetime) -> list[dict]:
        state = self._tracks.setdefault(det.track_id, _TrackState())
        state.missing_frames = 0
        raw_zone = self._zones.locate(det.bbox)
        events: list[dict] = []

        if state.confirmed_zone is not None and raw_zone != state.confirmed_zone:
            events.append(
                _event(
                    EVENT_ZONE_EXIT,
                    zone=state.confirmed_zone,
                    detection=state.last_detection,
                    ts=ts,
                )
            )
            state.confirmed_zone = None
            state.entered_at = None
            state.candidate_zone = None
            state.candidate_since = None
            state.candidate_detection = None

        if state.confirmed_zone is None:
            if raw_zone is None:
                state.candidate_zone = None
                state.candidate_since = None
                state.candidate_detection = None
            else:
                if state.candidate_zone != raw_zone:
                    state.candidate_zone = raw_zone
                    state.candidate_since = ts
                    state.candidate_detection = det
                elapsed = (ts - state.candidate_since).total_seconds()
                if elapsed >= self._dwell_seconds:
                    state.confirmed_zone = raw_zone
                    state.entered_at = state.candidate_since
                    events.append(
                        _event(
                            EVENT_ZONE_ENTER,
                            zone=raw_zone,
                            detection=state.candidate_detection,
                            ts=state.candidate_since,
                        )
                    )

        state.last_detection = det
        return events
