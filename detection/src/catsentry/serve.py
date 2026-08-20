"""catsentry-serve: `catsentry-serve --config config.yaml` runs the full
composed service (ingest -> zones -> dwell -> squat -> policy -> store/
notify/MQTT, see catsentry.service) against config.yaml's source until
Ctrl+C/SIGTERM. Thin by design: this module owns only argument parsing,
logging setup, and turning a signal into the stop_event catsentry.service
already knows how to shut down cleanly on.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from catsentry.config import ConfigError, load_config
from catsentry.service import run_service

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="catsentry-serve",
        description="Run the full Cat Sentry detection service against config.yaml.",
    )
    parser.add_argument("--config", type=Path, required=True, help="path to config.yaml")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="restart a finite source (e.g. a video file) from the beginning when it ends "
        "instead of stopping the service -- for soak-testing against a short clip",
    )
    parser.add_argument("--verbose", action="store_true", help="DEBUG logging (default: INFO)")
    return parser


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _handle(signum: int, _frame: object) -> None:
        logger.info("received signal %s, shutting down", signal.Signals(signum).name)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)

    logger.info(
        "starting catsentry-serve source=%s deterrent_enabled=%s ntfy_topic=%s loop=%s",
        cfg.source.url,
        cfg.flags.deterrent_enabled,
        cfg.ntfy.topic,
        args.loop,
    )
    run_service(cfg, loop=args.loop, stop_event=stop_event)
    logger.info("catsentry-serve stopped cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
