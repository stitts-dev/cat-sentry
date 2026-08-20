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
