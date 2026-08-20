"""Deterrent policy: extends C2's IN_ZONE state machine past IN_ZONE --
SUSPECT (squat heuristic) -> WARNED (sound fired) -> ESCALATED (air fired)
-> COOLDOWN -- plus the hard safety rails every fire command must clear.

Pure logic -- no cv2/ultralytics/mqtt. Composes a DwellEngine (owns
IDLE<->IN_ZONE and zone membership) rather than duplicating it, per
dwell.py's own note that this is where C3 lands. Deterministic: every
timestamp is the caller's frame ts (see replay.py), never the wall clock.

Squat heuristic (docs/design.md component 5): a track that's IN_ZONE with a
bbox height/width ratio below `thresholds.squat_aspect_ratio` and a centroid
that hasn't drifted more than `thresholds.centroid_epsilon` from where the
squat candidacy started, sustained for `thresholds.squat_seconds`, is
SUSPECT. Same debounce shape as DwellEngine's zone candidacy: any frame that
breaks either condition restarts the candidacy rather than merely pausing
it, which is what makes a wobbly (moving) cat never reach SUSPECT.

# altitude: every safety rail (protected zone, hourly cap, global cooldown,
# detection-only flag) lives in `_rail_violation` -- the single choke point
# every fire command passes through -- rather than scattered across the
# state machine below it. Nothing else in this module is allowed to append
# to a `fires` list directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from catsentry.config import FlagsConfig, RateLimitsConfig, ThresholdsConfig
from catsentry.dwell import DEFAULT_MAX_MISSING_FRAMES, DwellEngine, build_event, format_ts
from catsentry.tracer import Detection
from catsentry.zones import PROTECTED_ZONE, ZoneMap, bbox_bottom_center

EVENT_SQUAT_SUSPECTED = "squat_suspected"
EVENT_DETERRENT_FIRED = "deterrent_fired"

LEVEL_SOUND = "sound"
LEVEL_AIR = "air"

# Fire command durations (ms). Not config -- no threshold in
# docs/design.md/config.sample.yaml covers these, and no acceptance
# criteria calls for them to be tunable yet; hardware retuning (C3's other
# thresholds) can promote these to config later if needed.
# ponytail: hardcoded rather than added to ThresholdsConfig -- keep the
# config surface to what the issue actually asked to be tunable.
SOUND_DURATION_MS = 1500
AIR_DURATION_MS = 800

_SECONDS_PER_HOUR = 3600.0


class TrackState(StrEnum):
    """Per-track state past IN_ZONE. SUSPECT and ESCALATED are momentary --
    confirmed and acted on in the same `update()` call -- so they're never
    the state a track is left holding between frames; only IN_ZONE, WARNED,
    and COOLDOWN persist."""

    IN_ZONE = "IN_ZONE"
    WARNED = "WARNED"
    COOLDOWN = "COOLDOWN"


@dataclass
class _PolicyTrackState:
    state: TrackState = TrackState.IN_ZONE
    squat_since: datetime | None = None  # when the current squat candidacy began
    squat_anchor: tuple[float, float] | None = None  # centroid at candidacy start
    squat_start_detection: Detection | None = None  # for backdated squat_suspected events
    warned_at: datetime | None = None  # ts the sound command was attempted

    def clear_squat(self) -> None:
        """Drop any squat candidacy -- the shape/stillness condition broke."""
        self.squat_since = None
        self.squat_anchor = None
        self.squat_start_detection = None

    def start_squat(self, ts: datetime, centroid: tuple[float, float], det: Detection) -> None:
        """Begin a fresh squat candidacy anchored at this frame."""
        self.squat_since = ts
        self.squat_anchor = centroid
        self.squat_start_detection = det


def _aspect_ratio(bbox: tuple[float, float, float, float]) -> float:
    _, _, w, h = bbox
    return h / w if w > 0 else math.inf


def _centroid(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    x, y, w, h = bbox
    return (x + w / 2, y + h / 2)


def _displacement(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _fire_command(
    *,
    ts: datetime,
    level: str,
    bbox: tuple[float, float, float, float],
    duration_ms: int,
    reason: str,
) -> dict:
    """Shape a catsentry/deterrent/fire payload exactly per
    docs/contract-catsentry-v1.md: ts/level/target/duration_ms/reason,
    target = bbox bottom-center (where the deterrent should aim)."""
    return {
        "ts": format_ts(ts),
        "level": level,
        "target": list(bbox_bottom_center(bbox)),
        "duration_ms": duration_ms,
        "reason": reason,
    }


class FirePolicy:
    """Runs the full per-track pipeline past IN_ZONE and decides what, if
    anything, gets fired. Call `update` once per frame, in ts order; it
    returns `(events, fires)` for that frame -- `events` is C2's
    zone_enter/zone_exit stream plus squat_suspected/deterrent_fired,
    `fires` is zero or more catsentry/deterrent/fire payloads.
    """

    def __init__(
        self,
        zones: ZoneMap,
        thresholds: ThresholdsConfig,
        rate_limits: RateLimitsConfig,
        flags: FlagsConfig,
        *,
        max_missing_frames: int = DEFAULT_MAX_MISSING_FRAMES,
    ) -> None:
        self._dwell = DwellEngine(zones, thresholds, max_missing_frames=max_missing_frames)
        self._thresholds = thresholds
        self._rate_limits = rate_limits
        self._flags = flags
        self._tracks: dict[int, _PolicyTrackState] = {}
        self._fire_log: list[datetime] = []  # trailing-hour fire history, for the hourly cap
        self._last_fire_at: datetime | None = None  # global cooldown anchor

    def dwell_seconds(self, track_id: int, now: datetime) -> float | None:
        return self._dwell.dwell_seconds(track_id, now)

    def track_state(self, track_id: int) -> TrackState | None:
        state = self._tracks.get(track_id)
        return state.state if state is not None else None

    def reset(self) -> None:
        """Drop all per-track policy state and the dwell engine's, for a
        stream reconnect -- see DwellEngine.reset. The global rate-limit
        history (`_fire_log`/`_last_fire_at`) is deliberately kept: the
        safety rails must survive a reconnect, not reset with it."""
        self._tracks.clear()
        self._dwell.reset()

    def update(self, ts: datetime, detections: list[Detection]) -> tuple[list[dict], list[dict]]:
        events = self._dwell.update(ts, detections)
        fires: list[dict] = []

        # Drop policy memory for any track no longer confirmed IN_ZONE
        # (left the zone, or vanished) -- a fresh visit starts fresh.
        for track_id in list(self._tracks):
            if self._dwell.confirmed_zone(track_id) is None:
                del self._tracks[track_id]

        for det in detections:
            if det.track_id is None:
                continue
            zone = self._dwell.confirmed_zone(det.track_id)
            if zone is None:
                continue  # not IN_ZONE (yet) -- squat heuristic doesn't apply
            events.extend(self._advance(det, zone, ts, fires))

        return events, fires

    # -- rails: the one place every fire command is gated ------------------

    def _rail_violation(self, zone: str, ts: datetime, *, check_cooldown: bool) -> str | None:
        """None if a fire is allowed right now; otherwise the reason it's
        blocked. Checked fresh for every fire attempt -- order is cheapest
        (and most decisive) check first.

        `check_cooldown` is False for an ESCALATED (air) fire that's
        continuing an episode whose SUSPECT/WARNED sound just cleared this
        same rail moments ago: the cooldown protects against back-to-back
        *episodes*, not against the sound->air escalation inside one --
        with typical config (escalate_seconds ~5s, cooldown_minutes ~15) the
        air fire would otherwise always be blocked by the cooldown its own
        sound just started. The hourly cap has no such exemption -- it's an
        unconditional ceiling on actuator activations, sound or air alike.
        """
        if not self._flags.deterrent_enabled:
            return "deterrent disabled (detection-only mode)"
        if zone == PROTECTED_ZONE:
            return f"zone '{zone}' is protected -- deterrent never targets litterboxes"
        if check_cooldown and self._last_fire_at is not None:
            elapsed_min = (ts - self._last_fire_at).total_seconds() / 60.0
            if elapsed_min < self._rate_limits.cooldown_minutes:
                return (
                    f"global cooldown active "
                    f"({elapsed_min:.1f}/{self._rate_limits.cooldown_minutes}min)"
                )
        recent = [t for t in self._fire_log if (ts - t).total_seconds() <= _SECONDS_PER_HOUR]
        self._fire_log = recent
        if len(recent) >= self._rate_limits.max_fires_per_hour:
            return f"hourly cap reached ({len(recent)}/{self._rate_limits.max_fires_per_hour})"
        return None

    def _try_fire(
        self,
        *,
        level: str,
        ts: datetime,
        zone: str,
        bbox: tuple[float, float, float, float],
        duration_ms: int,
        reason: str,
        fires: list[dict],
        check_cooldown: bool,
    ) -> bool:
        if self._rail_violation(zone, ts, check_cooldown=check_cooldown) is not None:
            return False
        fires.append(
            _fire_command(ts=ts, level=level, bbox=bbox, duration_ms=duration_ms, reason=reason)
        )
        self._fire_log.append(ts)
        self._last_fire_at = ts
        return True

    # -- per-track state machine past IN_ZONE -------------------------------

    def _advance(
        self, det: Detection, zone: str, ts: datetime, fires: list[dict]
    ) -> list[dict]:
        pstate = self._tracks.setdefault(det.track_id, _PolicyTrackState())
        events: list[dict] = []

        ratio = _aspect_ratio(det.bbox)
        is_squat_shape = ratio < self._thresholds.squat_aspect_ratio
        centroid = _centroid(det.bbox)
        holding_still = (
            pstate.squat_anchor is not None
            and _displacement(centroid, pstate.squat_anchor) < self._thresholds.centroid_epsilon
        )

        if pstate.state == TrackState.IN_ZONE:
            if not (is_squat_shape and holding_still):
                pstate.clear_squat()
                if is_squat_shape:
                    pstate.start_squat(ts, centroid, det)
                return events

            elapsed = (ts - pstate.squat_since).total_seconds()
            if elapsed < self._thresholds.squat_seconds:
                return events

            # SUSPECT confirmed -- backdated to squat_since, matching
            # dwell.py's zone_enter backdating convention.
            events.append(
                build_event(
                    EVENT_SQUAT_SUSPECTED,
                    zone=zone,
                    detection=pstate.squat_start_detection,
                    ts=pstate.squat_since,
                )
            )
            dwell = self._dwell.dwell_seconds(det.track_id, ts) or 0.0
            fired = self._try_fire(
                level=LEVEL_SOUND,
                ts=ts,
                zone=zone,
                bbox=det.bbox,
                duration_ms=SOUND_DURATION_MS,
                reason=f"squat_suspected dwell={dwell:.1f}s zone={zone}",
                fires=fires,
                check_cooldown=True,  # starting a new episode -- subject to the cooldown
            )
            pstate.state = TrackState.WARNED
            pstate.warned_at = ts
            if fired:
                events.append(build_event(EVENT_DETERRENT_FIRED, zone=zone, detection=det, ts=ts))

        elif pstate.state == TrackState.WARNED:
            if not (is_squat_shape and holding_still):
                # Squat broke before escalating -- episode over, go back to
                # plain watching. A fresh squat candidacy re-earns SUSPECT;
                # the global cooldown rail (not this state) is what stops a
                # too-soon re-fire.
                pstate.state = TrackState.IN_ZONE
                pstate.clear_squat()
                if is_squat_shape:
                    pstate.start_squat(ts, centroid, det)
                return events

            elapsed = (ts - pstate.warned_at).total_seconds()
            if elapsed < self._thresholds.escalate_seconds:
                return events

            dwell = self._dwell.dwell_seconds(det.track_id, ts) or 0.0
            fired = self._try_fire(
                level=LEVEL_AIR,
                ts=ts,
                zone=zone,
                bbox=det.bbox,
                duration_ms=AIR_DURATION_MS,
                reason=f"squat_persisted dwell={dwell:.1f}s zone={zone} escalated_after_sound",
                fires=fires,
                check_cooldown=False,  # continuing this episode -- exempt, see _rail_violation
            )
            pstate.state = TrackState.COOLDOWN
            if fired:
                events.append(build_event(EVENT_DETERRENT_FIRED, zone=zone, detection=det, ts=ts))

        elif pstate.state == TrackState.COOLDOWN:
            # This track already escalated. Once the global cooldown clears,
            # let it re-arm for a fresh squat episode instead of staying
            # stuck forever; until then it's simply not re-evaluated.
            cooled_down = self._last_fire_at is None or (
                (ts - self._last_fire_at).total_seconds() / 60.0
                >= self._rate_limits.cooldown_minutes
            )
            if cooled_down:
                pstate.state = TrackState.IN_ZONE
                pstate.clear_squat()
                if is_squat_shape:
                    pstate.start_squat(ts, centroid, det)

        return events
