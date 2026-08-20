"""catsentry-serve CLI glue tests. `run_service` itself is exercised in
test_service.py/test_service_integration.py; here we only check that
serve.py parses args, surfaces a config error correctly, and hands the
right things (config, --loop, a stop_event) to run_service -- monkeypatched
out so this never touches a real source/broker/ntfy."""

from __future__ import annotations

import signal
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from catsentry.config import load_config
from catsentry.serve import build_parser, main

SAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "config.sample.yaml"


@pytest.fixture(autouse=True)
def _restore_signal_handlers():
    """main() installs real SIGINT/SIGTERM handlers on this process --
    restore whatever was there before so a test that calls main() doesn't
    leak a handler (closed over a throwaway stop_event) into later tests."""
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM) if hasattr(signal, "SIGTERM") else None
    yield
    signal.signal(signal.SIGINT, original_sigint)
    if original_sigterm is not None:
        signal.signal(signal.SIGTERM, original_sigterm)


def test_config_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_parses_config_loop_and_verbose_flags():
    args = build_parser().parse_args(
        ["--config", "config.sample.yaml", "--loop", "--verbose"]
    )
    assert args.config == Path("config.sample.yaml")
    assert args.loop is True
    assert args.verbose is True


def test_defaults_loop_and_verbose_to_false():
    args = build_parser().parse_args(["--config", "config.sample.yaml"])
    assert args.loop is False
    assert args.verbose is False


def test_main_returns_1_and_prints_error_for_invalid_config(tmp_path, capsys, monkeypatch):
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("not: [valid: yaml")
    run_service = MagicMock()
    monkeypatch.setattr("catsentry.serve.run_service", run_service)

    exit_code = main(["--config", str(bad_config)])

    assert exit_code == 1
    assert "config error" in capsys.readouterr().err
    run_service.assert_not_called()


def test_main_calls_run_service_with_loaded_config_and_loop_flag(monkeypatch):
    run_service = MagicMock()
    monkeypatch.setattr("catsentry.serve.run_service", run_service)

    exit_code = main(["--config", str(SAMPLE_CONFIG), "--loop"])

    assert exit_code == 0
    run_service.assert_called_once()
    call = run_service.call_args
    assert call.args[0] == load_config(SAMPLE_CONFIG)
    assert call.kwargs["loop"] is True
    assert isinstance(call.kwargs["stop_event"], threading.Event)


def test_sigint_sets_the_stop_event_run_service_receives(monkeypatch):
    captured_stop_event = {}

    def _fake_run_service(cfg, *, loop, stop_event):
        captured_stop_event["event"] = stop_event

    monkeypatch.setattr("catsentry.serve.run_service", _fake_run_service)

    main(["--config", str(SAMPLE_CONFIG)])

    # main() installed a SIGINT handler that sets the same stop_event it
    # passed to run_service -- simulate Ctrl+C by invoking it directly
    # rather than actually signalling the test process.
    handler = signal.getsignal(signal.SIGINT)
    handler(signal.SIGINT, None)

    assert captured_stop_event["event"].is_set()
