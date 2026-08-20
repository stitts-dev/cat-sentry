"""C5 service composition tests. No YOLO, no network, no real broker/ntfy --
`track_cats_fn`, `clock`, `store`, `notifier`, and `publisher` are all
injectable seams on `run_frame_loop`/`run_output_worker`/`run_service` for
exactly this reason. Real-stream/YOLO coverage is
tests/test_service_integration.py (marked `integration`).
"""

from __future__ import annotations

import logging
import queue
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
from helpers import det, fast_thresholds

from catsentry.config import FlagsConfig, RateLimitsConfig, load_config
from catsentry.policy import FirePolicy
from catsentry.replay import load_sequence
from catsentry.service import (
    SHUTDOWN,
    EventJob,
    FireJob,
    WallClock,
    _enqueue,
    run_frame_loop,
    run_output_worker,
    run_service,
)
from catsentry.tracer import Detection, SourceError
from catsentry.zones import ZoneMap

SAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "config.sample.yaml"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

FAST_ZONES = {"zone_a": [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]}
FRAME = np.zeros((2, 2, 3), dtype=np.uint8)


def _policy(*, deterrent_enabled: bool) -> FirePolicy:
    return FirePolicy(
        ZoneMap(FAST_ZONES),
        fast_thresholds(
            dwell_seconds=0.5,
            squat_seconds=0.5,
            escalate_seconds=1000.0,  # never escalate -- keep these scenarios sound-only
        ),
        RateLimitsConfig(max_fires_per_hour=10, cooldown_minutes=0.01),
        FlagsConfig(deterrent_enabled=deterrent_enabled),
        max_missing_frames=1,
    )


def _squat_frames() -> list[list[Detection]]:
    """Same shape as squat_escalation_sequence.json's detections, minus ts
    (the fake clock below supplies those) -- enough frames of a still,
    squat-shaped bbox to reach SUSPECT under `fast_thresholds`."""
    sequence = load_sequence(FIXTURES / "squat_escalation_sequence.json")
    return [
        [
            Detection(
                frame_idx=i,
                track_id=d["track_id"],
                confidence=d["confidence"],
                bbox=tuple(d["bbox"]),
            )
            for d in frame["detections"]
        ]
        for i, frame in enumerate(sequence)
    ]


def _fixed_clock(start: datetime, step_seconds: float = 1.0):
    """A deterministic `clock` callable: each call advances by
    `step_seconds`, starting at `start` on the first call."""
    state = {"n": -1}

    def _clock() -> datetime:
        state["n"] += 1
        return start + timedelta(seconds=state["n"] * step_seconds)

    return _clock


def _fake_track_cats(*call_results):
    """Builds a `track_cats_fn` stand-in. Each element of `call_results` is
    either a list of `(frame, detections)` pairs to yield on that call, or
    an Exception to raise (lazily, on first iteration -- like the real
    SourceError-from-inside-a-generator shape) on that call."""
    calls = {"n": 0}

    def fn(source, *, model_path=None, conf=None):
        idx = calls["n"]
        calls["n"] += 1
        result = call_results[idx]
        if isinstance(result, Exception):
            raise result
            yield  # pragma: no cover -- unreachable; keeps `fn` a generator function
        yield from result

    fn.calls = calls
    return fn


# -- _enqueue / drop-oldest ---------------------------------------------------


def test_enqueue_drops_oldest_on_overflow_and_logs_warning(caplog):
    q: queue.Queue = queue.Queue(maxsize=2)
    job1 = EventJob(event={"type": "zone_enter"})
    job2 = EventJob(event={"type": "zone_exit"})
    job3 = EventJob(event={"type": "squat_suspected"})

    with caplog.at_level(logging.WARNING):
        _enqueue(q, job1)
        _enqueue(q, job2)
        _enqueue(q, job3)  # queue full at job1, job2 -- drops job1

    assert q.qsize() == 2
    remaining = [q.get_nowait(), q.get_nowait()]
    assert remaining == [job2, job3]
    assert any("dropping oldest" in r.message for r in caplog.records)


def test_enqueue_does_not_block_or_raise_when_space_available():
    q: queue.Queue = queue.Queue(maxsize=5)
    _enqueue(q, FireJob(fire={"level": "sound"}))
    assert q.qsize() == 1


# -- run_output_worker: wiring order -----------------------------------------


def test_worker_runs_store_then_notify_with_stores_return_value_then_publish():
    calls: list[str] = []

    def _fake_save(event, frame=None):
        calls.append("store.save")
        return {**event, "snapshot_path": "events/2026-08-19/000000.jpg"}

    def _fake_notify(event):
        calls.append("notify")
        return True

    def _fake_publish_event(event):
        calls.append("publish_event")

    store = MagicMock()
    store.save.side_effect = _fake_save
    notifier = MagicMock()
    notifier.notify.side_effect = _fake_notify
    publisher = MagicMock()
    publisher.publish_event.side_effect = _fake_publish_event

    q: queue.Queue = queue.Queue()
    event = {"type": "squat_suspected", "ts": "2026-08-19T00:00:00Z"}
    q.put(EventJob(event=event, frame=FRAME))
    q.put(SHUTDOWN)

    run_output_worker(q, store, notifier, publisher)

    assert calls == ["store.save", "notify", "publish_event"]
    store.save.assert_called_once_with(event, FRAME)
    # notify/publish_event both see store.save's return value, not the raw event.
    notified_event = notifier.notify.call_args.args[0]
    published_event = publisher.publish_event.call_args.args[0]
    assert notified_event["snapshot_path"] == "events/2026-08-19/000000.jpg"
    assert published_event["snapshot_path"] == "events/2026-08-19/000000.jpg"


def test_worker_publishes_fire_jobs_without_touching_store_or_notify():
    store = MagicMock()
    notifier = MagicMock()
    publisher = MagicMock()

    q: queue.Queue = queue.Queue()
    fire = {"level": "sound", "ts": "2026-08-19T00:00:00Z"}
    q.put(FireJob(fire=fire))
    q.put(SHUTDOWN)

    run_output_worker(q, store, notifier, publisher)

    publisher.publish_fire.assert_called_once_with(fire)
    store.save.assert_not_called()
    notifier.notify.assert_not_called()


def test_worker_survives_a_failing_job_and_keeps_draining():
    store = MagicMock()
    store.save.side_effect = [RuntimeError("disk full"), {"type": "zone_enter", "ts": "x"}]
    notifier = MagicMock()
    publisher = MagicMock()

    q: queue.Queue = queue.Queue()
    q.put(EventJob(event={"type": "zone_enter", "ts": "bad"}))
    q.put(EventJob(event={"type": "zone_enter", "ts": "x"}))
    q.put(SHUTDOWN)

    run_output_worker(q, store, notifier, publisher)  # must not raise

    assert store.save.call_count == 2
    notifier.notify.assert_called_once()


# -- run_frame_loop: detection-only mode end to end --------------------------


def test_detection_only_mode_events_flow_but_zero_fire_jobs_enqueued():
    policy = _policy(deterrent_enabled=False)
    frames = _squat_frames()
    track_cats_fn = _fake_track_cats([(FRAME, dets) for dets in frames])
    q: queue.Queue = queue.Queue(maxsize=100)

    run_frame_loop(
        "unused",
        policy,
        q,
        clock=_fixed_clock(datetime(2026, 8, 19, tzinfo=UTC)),
        stop_event=threading.Event(),
        track_cats_fn=track_cats_fn,
    )

    jobs = []
    while not q.empty():
        jobs.append(q.get_nowait())

    event_types = [j.event["type"] for j in jobs if isinstance(j, EventJob)]
    assert "squat_suspected" in event_types  # detection isn't gated by the flag
    assert not any(isinstance(j, FireJob) for j in jobs)  # but nothing fired


def test_deterrent_enabled_mode_produces_a_fire_job():
    policy = _policy(deterrent_enabled=True)
    frames = _squat_frames()
    track_cats_fn = _fake_track_cats([(FRAME, dets) for dets in frames])
    q: queue.Queue = queue.Queue(maxsize=100)

    run_frame_loop(
        "unused",
        policy,
        q,
        clock=_fixed_clock(datetime(2026, 8, 19, tzinfo=UTC)),
        stop_event=threading.Event(),
        track_cats_fn=track_cats_fn,
    )

    jobs = []
    while not q.empty():
        jobs.append(q.get_nowait())
    assert any(isinstance(j, FireJob) for j in jobs)


# -- run_frame_loop: only squat_suspected/deterrent_fired carry a snapshot ---


def test_only_notify_worthy_events_carry_a_frame_for_snapshotting():
    policy = _policy(deterrent_enabled=False)
    frames = _squat_frames()
    track_cats_fn = _fake_track_cats([(FRAME, dets) for dets in frames])
    q: queue.Queue = queue.Queue(maxsize=100)

    run_frame_loop(
        "unused",
        policy,
        q,
        clock=_fixed_clock(datetime(2026, 8, 19, tzinfo=UTC)),
        stop_event=threading.Event(),
        track_cats_fn=track_cats_fn,
    )

    jobs = [q.get_nowait() for _ in range(q.qsize())]
    for job in jobs:
        if not isinstance(job, EventJob):
            continue
        if job.event["type"] in ("squat_suspected", "deterrent_fired"):
            assert job.frame is not None
        else:
            assert job.frame is None


# -- run_frame_loop: reconnect ------------------------------------------------


def test_source_error_calls_policy_reset_before_resuming():
    real_policy = _policy(deterrent_enabled=False)
    policy = MagicMock(wraps=real_policy)
    track_cats_fn = _fake_track_cats(
        SourceError("stream dropped"),
        [(FRAME, [det(1)])],
    )
    q: queue.Queue = queue.Queue(maxsize=100)

    run_frame_loop(
        "unused",
        policy,
        q,
        clock=_fixed_clock(datetime(2026, 8, 19, tzinfo=UTC)),
        stop_event=threading.Event(),
        track_cats_fn=track_cats_fn,
        min_backoff_s=0.01,
        max_backoff_s=0.01,
    )

    assert policy.reset.call_count == 1
    assert track_cats_fn.calls["n"] == 2  # first call raised, second call succeeded


def test_repeated_source_errors_back_off_and_eventually_stop_on_stop_event():
    policy = _policy(deterrent_enabled=False)
    # An effectively endless run of SourceErrors -- proves reconnect keeps
    # retrying rather than giving up, and that stop_event (not exhaustion)
    # is what ends it.
    track_cats_fn = _fake_track_cats(*(SourceError(f"drop {i}") for i in range(1000)))
    q: queue.Queue = queue.Queue(maxsize=10)
    stop_event = threading.Event()
    timer = threading.Timer(0.05, stop_event.set)
    timer.start()

    try:
        run_frame_loop(
            "unused",
            policy,
            q,
            clock=_fixed_clock(datetime(2026, 8, 19, tzinfo=UTC)),
            stop_event=stop_event,
            track_cats_fn=track_cats_fn,
            min_backoff_s=0.01,
            max_backoff_s=0.01,
        )
    finally:
        timer.cancel()

    assert track_cats_fn.calls["n"] >= 2  # retried at least once before the timer stopped it


def test_loop_mode_restarts_a_finite_source_and_resets_policy():
    real_policy = _policy(deterrent_enabled=False)
    policy = MagicMock(wraps=real_policy)
    track_cats_fn = _fake_track_cats(
        [(FRAME, [det(1)])],  # first pass: source ends normally
        [(FRAME, [det(1)])],  # loop restarts it
    )
    q: queue.Queue = queue.Queue(maxsize=100)
    stop_event = threading.Event()

    # Stop after the loop restart's frame is processed, else this would loop
    # forever (the fake source always "ends" after one frame).
    calls_before = {"count": 0}
    orig_update = real_policy.update

    def _update(ts, detections):
        calls_before["count"] += 1
        if calls_before["count"] >= 2:
            stop_event.set()
        return orig_update(ts, detections)

    policy.update.side_effect = _update

    run_frame_loop(
        "unused",
        policy,
        q,
        clock=_fixed_clock(datetime(2026, 8, 19, tzinfo=UTC)),
        stop_event=stop_event,
        track_cats_fn=track_cats_fn,
        loop=True,
    )

    assert track_cats_fn.calls["n"] == 2  # restarted once
    assert policy.reset.call_count == 1  # reset on the loop restart


def test_non_looping_finite_source_ends_the_frame_loop_without_error():
    policy = _policy(deterrent_enabled=False)
    track_cats_fn = _fake_track_cats([(FRAME, [det(1)])])
    q: queue.Queue = queue.Queue(maxsize=10)

    run_frame_loop(
        "unused",
        policy,
        q,
        clock=_fixed_clock(datetime(2026, 8, 19, tzinfo=UTC)),
        stop_event=threading.Event(),
        track_cats_fn=track_cats_fn,
        loop=False,
    )  # returns instead of hanging or raising


# -- WallClock -----------------------------------------------------------


def test_wall_clock_advances_with_monotonic_time_not_system_clock(monkeypatch):
    anchor_wall = datetime(2026, 8, 19, 0, 0, 0, tzinfo=UTC)
    fake_monotonic = {"t": 100.0}
    monkeypatch.setattr("catsentry.service.time.monotonic", lambda: fake_monotonic["t"])

    clock = WallClock(_anchor_wall=anchor_wall, _anchor_monotonic=100.0)
    assert clock.now() == anchor_wall

    fake_monotonic["t"] = 102.5
    assert clock.now() == anchor_wall + timedelta(seconds=2.5)


# -- run_service: full composition -------------------------------------------


def test_run_service_wires_everything_and_shuts_down_cleanly():
    cfg = load_config(SAMPLE_CONFIG)
    track_cats_fn = _fake_track_cats([(FRAME, [det(1, bbox=(0.15, 0.65, 0.10, 0.05))])])

    store = MagicMock()
    store.save.side_effect = lambda event, frame=None: event
    notifier = MagicMock()
    publisher = MagicMock()

    run_service(
        cfg,
        track_cats_fn=track_cats_fn,
        store=store,
        notifier=notifier,
        publisher=publisher,
        clock=_fixed_clock(datetime(2026, 8, 19, tzinfo=UTC)),
    )

    publisher.connect.assert_called_once()
    publisher.close.assert_called_once()
    store.close.assert_called_once()


def test_run_service_stops_promptly_when_stop_event_preset():
    cfg = load_config(SAMPLE_CONFIG)

    def _never_called(*args, **kwargs):  # pragma: no cover -- must not be reached
        raise AssertionError("track_cats_fn should not run once stop_event is already set")

    stop_event = threading.Event()
    stop_event.set()

    store = MagicMock()
    notifier = MagicMock()
    publisher = MagicMock()

    run_service(
        cfg,
        stop_event=stop_event,
        track_cats_fn=_never_called,
        store=store,
        notifier=notifier,
        publisher=publisher,
    )

    publisher.close.assert_called_once()
    store.close.assert_called_once()
