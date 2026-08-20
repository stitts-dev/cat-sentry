"""C5: the composed service -- wires every existing building block (tracer,
zones, dwell, policy, store, notify, outputs) into the running loop
described by docs/design.md. Composition only; no new detection/zone/squat/
rate-limit logic lives here.

Threading model (docs/design.md, Track 1 components, the paragraph right
after the component list): MqttPublisher is already non-blocking, but
EventStore.save and NtfyNotifier.notify are synchronous by design -- so the
frame loop never calls them inline. Instead the frame loop only enqueues
onto a bounded queue; a single worker thread drains it, running
EventStore.save -> NtfyNotifier.notify (fed store.save's return value, so
notify sees the snapshot_path store.save just filled in) -> the matching
MqttPublisher.publish_event/publish_fire call. This keeps the frame loop
itself doing nothing but YOLO/zone/policy work plus a non-blocking queue put.

Timestamps: DwellEngine/FirePolicy never read the wall clock themselves
(see dwell.py/policy.py docstrings) -- every `update()` call needs a ts
argument. The frame loop gets that ts from a `WallClock` anchored once, at
construction, to `time.monotonic()`: real elapsed time between frames comes
from the monotonic clock (immune to NTP steps/DST/manual clock changes
mid-run), translated back to a wall-clock datetime for the contract's ISO
timestamps. Reconnects don't re-anchor it -- `time.monotonic()` keeps
advancing through a disconnect/backoff, so ts naturally jumps forward by
however long the source was actually down; `policy.reset()` (called before
resuming) is what stops that legitimate jump from being misread as an
implausible dwell/squat duration for tracks that predate the reconnect.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np

from catsentry.config import Config
from catsentry.notify import NOTIFY_EVENT_TYPES, NtfyNotifier
from catsentry.outputs import MqttPublisher
from catsentry.policy import FirePolicy
from catsentry.store import EventStore
from catsentry.tracer import (
    DEFAULT_CONF,
    DEFAULT_MODEL,
    Detection,
    SourceError,
    parse_source,
    track_cats,
)
from catsentry.zones import ZoneMap

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_MAXSIZE = 100
RECONNECT_MIN_BACKOFF_S = 1.0
RECONNECT_MAX_BACKOFF_S = 30.0
WORKER_JOIN_TIMEOUT_S = 10.0

# Shutdown marker pushed onto the output queue: tells the worker thread to
# stop after it has drained everything queued ahead of it (the "flush"
# clean-shutdown needs), rather than being torn down mid-job.
SHUTDOWN = object()


@dataclass
class WallClock:
    """Wall-clock timestamps anchored to `time.monotonic()` at construction
    -- see module docstring. `now()` is the callable the frame loop's
    `clock` parameter expects; tests inject their own deterministic
    `clock` callable instead of this one."""

    _anchor_wall: datetime = field(default_factory=lambda: datetime.now(UTC))
    _anchor_monotonic: float = field(default_factory=time.monotonic)

    def now(self) -> datetime:
        elapsed = time.monotonic() - self._anchor_monotonic
        return self._anchor_wall + timedelta(seconds=elapsed)


@dataclass(frozen=True, eq=False)
class EventJob:
    """A catsentry/event dict for the output worker to store/notify/publish.
    `frame` is the raw BGR frame to snapshot alongside it, or None to skip
    the snapshot (see `run_frame_loop`'s NOTIFY_EVENT_TYPES gate).

    eq=False: a numpy array field breaks dataclass equality (`arr == arr`
    is elementwise, not a bool) -- comparing two jobs isn't meaningful
    anyway, so identity equality (the default from `object`) is correct.
    """

    event: dict
    frame: np.ndarray | None = None


@dataclass(frozen=True)
class FireJob:
    """A catsentry/deterrent/fire dict for the output worker to publish."""

    fire: dict


OutputJob = EventJob | FireJob


def _job_kind(job: OutputJob) -> str:
    if isinstance(job, EventJob):
        return f"event:{job.event.get('type')}"
    return f"fire:{job.fire.get('level')}"


def _enqueue(output_queue: queue.Queue, job: OutputJob) -> None:
    """Non-blocking enqueue for the frame loop. On overflow, drops the
    *oldest* queued job rather than the new one (so the service degrades
    toward current state instead of stalling on stale data) and logs a
    warning -- a chronically full queue means the output worker is falling
    behind and that's worth knowing about.

    # ponytail: single producer (frame loop) + single consumer (worker
    # thread), so get_nowait()+put_nowait() not being one atomic op only
    # matters if it races the worker for which item counts as "oldest" --
    # it can never block the frame loop, raise, or drop more than the one
    # job being enqueued right now, which is all drop-oldest needs.
    """
    try:
        output_queue.put_nowait(job)
        return
    except queue.Full:
        pass

    try:
        dropped = output_queue.get_nowait()
        logger.warning(
            "output queue full (max=%d); dropping oldest %s",
            output_queue.maxsize,
            _job_kind(dropped),
        )
    except queue.Empty:
        pass

    try:
        output_queue.put_nowait(job)
    except queue.Full:
        logger.warning(
            "output queue still full after dropping oldest; discarding %s", _job_kind(job)
        )


def run_output_worker(
    output_queue: queue.Queue,
    store: EventStore,
    notifier: NtfyNotifier,
    publisher: MqttPublisher,
) -> None:
    """Drains `output_queue` until it sees `SHUTDOWN`, running each job's
    synchronous work off the frame loop's thread -- see module docstring
    for the wiring order. Any exception from a single job (store/notify/
    publish) is logged and swallowed so one bad job can't wedge the worker
    or take detection down with it; meant to run as the target of a
    dedicated thread (see `run_service`)."""
    while True:
        job = output_queue.get()
        try:
            if job is SHUTDOWN:
                break
            if isinstance(job, EventJob):
                stored = store.save(job.event, job.frame)
                notifier.notify(stored)
                publisher.publish_event(stored)
            else:
                publisher.publish_fire(job.fire)
        except Exception:
            logger.exception("output worker failed on %s", _job_kind(job))
        finally:
            output_queue.task_done()


def run_frame_loop(
    source: str | int,
    policy: FirePolicy,
    output_queue: queue.Queue,
    *,
    clock: Callable[[], datetime],
    stop_event: threading.Event,
    track_cats_fn: Callable[..., Iterator[tuple[np.ndarray, list[Detection]]]] = track_cats,
    model_path: str = DEFAULT_MODEL,
    conf: float = DEFAULT_CONF,
    loop: bool = False,
    min_backoff_s: float = RECONNECT_MIN_BACKOFF_S,
    max_backoff_s: float = RECONNECT_MAX_BACKOFF_S,
) -> None:
    """The composed per-frame pipeline: `track_cats_fn(source)` yields
    `(frame, detections)` -> `policy.update(clock(), detections)` -> events
    get enqueued (with a frame attached only for the event types
    NtfyNotifier actually alerts on -- squat_suspected/deterrent_fired,
    reusing NOTIFY_EVENT_TYPES rather than re-deriving that list) and fires
    get enqueued unconditionally. `clock()` is called once per yielded
    frame; policy/dwell never see a timestamp any other way.

    Reconnect: a `SourceError` from `track_cats_fn` (source unreachable or
    died mid-stream) is caught, logged, and retried with exponential
    backoff (capped at `max_backoff_s`) -- `policy.reset()` runs first so
    the wall-clock jump across the outage never flows into dwell/squat
    arithmetic (see module docstring). Runs until `stop_event` is set, or,
    for a source that ends on its own (e.g. a finite video file) without
    `loop=True`, until it's exhausted.
    """
    backoff = min_backoff_s
    while not stop_event.is_set():
        try:
            for frame, detections in track_cats_fn(source, model_path=model_path, conf=conf):
                if stop_event.is_set():
                    return
                backoff = min_backoff_s
                ts = clock()
                events, fires = policy.update(ts, detections)

                for event in events:
                    logger.info(
                        "event type=%s zone=%s confidence=%s ts=%s",
                        event["type"],
                        event.get("zone"),
                        event.get("confidence"),
                        event["ts"],
                    )
                    snapshot_frame = frame if event["type"] in NOTIFY_EVENT_TYPES else None
                    _enqueue(output_queue, EventJob(event=event, frame=snapshot_frame))

                for fire in fires:
                    logger.info(
                        "fire level=%s duration_ms=%s ts=%s reason=%s",
                        fire["level"],
                        fire["duration_ms"],
                        fire["ts"],
                        fire["reason"],
                    )
                    _enqueue(output_queue, FireJob(fire=fire))

            # Generator exhausted on its own (no SourceError, no stop_event
            # break above) -- a finite source, e.g. a video file, ended. Skip
            # the reset+restart if shutdown was requested on that last frame
            # -- the outer while's stop_event check would just undo it.
            if not loop or stop_event.is_set():
                return
            logger.info("source exhausted source=%s; restarting (--loop)", source)
            policy.reset()
        except SourceError as exc:
            logger.warning("source error source=%s backoff=%.1fs error=%s", source, backoff, exc)
            policy.reset()
            if stop_event.wait(backoff):
                return
            backoff = min(backoff * 2, max_backoff_s)


def run_service(
    config: Config,
    *,
    loop: bool = False,
    stop_event: threading.Event | None = None,
    queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
    track_cats_fn: Callable[..., Iterator[tuple[np.ndarray, list[Detection]]]] = track_cats,
    clock: Callable[[], datetime] | None = None,
    policy: FirePolicy | None = None,
    store: EventStore | None = None,
    notifier: NtfyNotifier | None = None,
    publisher: MqttPublisher | None = None,
) -> None:
    """Top-level composition: builds the C2-C4 pipeline (ZoneMap+FirePolicy,
    EventStore, NtfyNotifier, MqttPublisher) from `config` unless a caller
    supplies its own (tests inject fakes here instead of touching a real
    broker, filesystem, or network), starts the single output worker
    thread, and runs `run_frame_loop` against `config.source.url` until
    `stop_event` is set (serve.py wires Ctrl+C/SIGTERM to it) or a finite
    non-looping source ends.

    Shutdown is always clean, on any exit path out of the frame loop: push
    `SHUTDOWN` so the worker finishes draining what's already queued, join
    it, then close the publisher and the store.
    """
    stop_event = stop_event if stop_event is not None else threading.Event()
    clock = clock if clock is not None else WallClock().now
    policy = (
        policy
        if policy is not None
        else FirePolicy(ZoneMap(config.zones), config.thresholds, config.rate_limits, config.flags)
    )
    store = (
        store
        if store is not None
        else EventStore(config.store.db_path, config.store.events_dir)
    )
    notifier = notifier if notifier is not None else NtfyNotifier(config.ntfy)
    publisher = publisher if publisher is not None else MqttPublisher(config.broker)

    publisher.connect()

    output_queue: queue.Queue = queue.Queue(maxsize=queue_maxsize)
    worker = threading.Thread(
        target=run_output_worker,
        args=(output_queue, store, notifier, publisher),
        name="catsentry-output",
        daemon=True,
    )
    worker.start()
    logger.info("output worker started queue_max=%d", queue_maxsize)

    try:
        run_frame_loop(
            parse_source(config.source.url),
            policy,
            output_queue,
            clock=clock,
            stop_event=stop_event,
            track_cats_fn=track_cats_fn,
            conf=config.thresholds.confidence,
            loop=loop,
        )
    finally:
        logger.info("flushing output queue and shutting down")
        output_queue.put(SHUTDOWN)
        worker.join(timeout=WORKER_JOIN_TIMEOUT_S)
        if worker.is_alive():
            logger.warning("output worker did not stop within %.0fs", WORKER_JOIN_TIMEOUT_S)
        publisher.close()
        store.close()
