"""Opt-in integration test: the composed service (catsentry.service.run_service)
against a real downloaded video fixture with real YOLO inference -- the only
thing about test_service.py's fakes this swaps out is `track_cats_fn`
(defaults to the real `catsentry.tracer.track_cats`); store/notifier/
publisher stay as no-op fakes so this needs no SQLite/ntfy network/mosquitto
broker, matching test_tracer.py's real-YOLO integration test. Skipped by
default (see pyproject.toml's `-m 'not integration'`); needs
`uv run python scripts/download_fixtures.py` first (network, once) and
downloads pretrained YOLO weights on first run (network, once). Run with:
    uv run pytest -m integration tests/test_service_integration.py
"""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

import pytest

from catsentry.config import load_config
from catsentry.service import run_service

SAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "config.sample.yaml"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_CLIP = FIXTURES_DIR / "cat_laser_pointer.webm"


class _NoopStore:
    def save(self, event: dict, frame=None) -> dict:
        return event

    def close(self) -> None:
        pass


class _NoopNotifier:
    def notify(self, event: dict) -> bool:
        return False


class _NoopPublisher:
    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def publish_event(self, event: dict) -> None:
        pass

    def publish_fire(self, fire: dict) -> None:
        pass


def _config_against_fixture():
    cfg = load_config(SAMPLE_CONFIG)
    return replace(cfg, source=replace(cfg.source, url=str(FIXTURE_CLIP)))


@pytest.mark.integration
@pytest.mark.skipif(not FIXTURE_CLIP.exists(), reason="run scripts/download_fixtures.py first")
def test_run_service_processes_a_real_video_file_end_to_end_and_stops_cleanly():
    """AC: 'One command + config runs the whole service ... against a video
    file'. Non-looping: the fixture clip is finite, so run_service must
    return (not hang or raise) once it's exhausted -- proving reconnect/
    shutdown plumbing works with the real tracer, not just fakes."""
    cfg = _config_against_fixture()

    run_service(
        cfg,
        store=_NoopStore(),
        notifier=_NoopNotifier(),
        publisher=_NoopPublisher(),
    )


@pytest.mark.integration
@pytest.mark.skipif(not FIXTURE_CLIP.exists(), reason="run scripts/download_fixtures.py first")
def test_run_service_loop_mode_restarts_the_clip_until_stopped():
    """AC: '... and against a video file (loop mode for testing)'. Lets the
    short clip restart a few times under real YOLO, then stops it via
    stop_event (the same mechanism serve.py's SIGINT/SIGTERM handler uses)
    -- proving --loop survives more than one source-exhausted restart
    without crashing."""
    cfg = _config_against_fixture()
    stop_event = threading.Event()
    timer = threading.Timer(20.0, stop_event.set)
    timer.start()
    try:
        run_service(
            cfg,
            loop=True,
            stop_event=stop_event,
            store=_NoopStore(),
            notifier=_NoopNotifier(),
            publisher=_NoopPublisher(),
        )
    finally:
        timer.cancel()
