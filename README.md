# cat-sentry

AI litterbox sentry. A camera + YOLO watches the basement floor next to the
litterboxes, detects a cat settling in to pee where it shouldn't, and deters it
mildly — a sound chirp first, an air puff if it persists. Every event is logged
per-cat (outside-box peeing is often medical; the log is vet-relevant data).

**Built and validated in a digital twin before buying any hardware.** The
detection service develops against a Unity simulation
([worldsim](https://github.com/stitts-dev/worldsim)) that streams a real MJPEG
camera feed and executes real MQTT deterrent commands. Swapping sim for hardware
changes one config line and one actuator implementation.

```
worldsim / real camera --MJPEG--> detection service --MQTT--> virtual turret / ESP32
   (Unity, basement)              (YOLO + zones +              (chirp, air puff)
                                   dwell/squat state machine)
```

## Status

Pre-hardware. Spec'd, building detection service against the sim. $0 spent.

## Docs

- [Design spec](docs/design.md)
- [Interface contract (catsentry-v1)](docs/contract-catsentry-v1.md) — frozen; canonical copy lives in the homelab toolbelt

## Stack

Python 3.11+, ultralytics YOLO, OpenCV, paho-mqtt, SQLite, ntfy. Prototype runs
on an RTX 4090; phase 2 ports a quantized model to a Raspberry Pi 5 edge box.

## Detection service

The Python service lives in [`detection/`](detection/), `uv`-managed.

```
cd detection
uv sync
uv run python scripts/download_fixtures.py   # grabs 2 short CC cat clips for local testing
uv run catsentry tests/fixtures/cat_laser_pointer.webm --show
uv run catsentry tests/fixtures/cat_laser_pointer.webm --save out.mp4
uv run catsentry 0                            # webcam index
uv run catsentry http://localhost:8089/stream # worldsim / RTSP stream URL
uv run catsentry --config config.sample.yaml  # source.url from config

uv run ruff check .
uv run pytest                # fast tests only (config validation etc.)
uv run pytest -m integration # + real YOLO inference on the downloaded fixtures
```

That `catsentry` command is just the C1 tracer CLI (detect+track, print
detections). The full service -- ingest -> zones -> dwell/squat state
machine -> deterrent policy -> SQLite store + ntfy alerts + MQTT -- runs as
`catsentry-serve`:

```
cp config.sample.yaml config.local.yaml   # edit source.url, zones, thresholds, ntfy topic
uv run catsentry-serve --config config.local.yaml            # runs until Ctrl+C/SIGTERM
uv run catsentry-serve --config config.local.yaml --verbose  # DEBUG logging
uv run catsentry-serve --config config.local.yaml --loop     # restart a finite source (a
                                                               # video file) when it ends,
                                                               # for soak-testing a short clip
```

`config.local.yaml`'s `source.url` can be a video file, webcam index, or
stream URL (MJPEG worldsim endpoint or RTSP camera) -- same swap rule as the
contract. `flags.deterrent_enabled: false` (the shipped default) is
detection-only mode: `catsentry/event`s and SQLite/ntfy/MQTT logging all
still happen, but zero `catsentry/deterrent/fire` commands are ever sent.
Set it `true` once you're ready for the sound/air deterrent to actually fire.
Shutdown (Ctrl+C or SIGTERM) flushes any queued events/fires before closing
the store and MQTT connection.

Currently implemented: the full detection pipeline described in
[docs/design.md](docs/design.md) -- video/webcam/stream ingest with
reconnect, pretrained YOLO + ByteTrack cat detection/tracking, zone/dwell
state machine, squat heuristic + sound/air deterrent policy with hard safety
rails, SQLite event store + JPEG snapshots, ntfy push alerts, and MQTT
publishing -- composed into one long-running `catsentry-serve` process.
Per-cat ID and the Raspberry Pi edge port are phase 2 (see design doc).
