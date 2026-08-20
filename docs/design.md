# Cat Sentry — Design Spec

Date: 2026-08-19
Status: approved design, pre-implementation
Owner: jaden

## Problem

Cats (3+) pee on the basement floor next to their litterboxes. Goal: detect the
event with a camera + vision model, identify the offender, deter mildly
(sound first, air puff escalation), and log everything. Secondary goals:
public portfolio project, reusable homelab assets, first consumer of a reusable
Unity "worldsim" project.

Domain constraint: deterrents near litterboxes risk litterbox aversion (cat starts
avoiding the boxes entirely). Therefore: mild deterrents only, aimed at floor
zones *beside* boxes, never at boxes; generous cooldowns; detection/logging is the
primary deliverable and works even with deterrent disabled. Outside-box peeing is
often medical — the per-cat event log is vet-relevant data, not just trigger input.

## Approach (approved)

**Digital-twin closed loop, zero purchases up front.** Unity sim (separate project:
`worldsim`) renders a virtual basement camera and streams real MJPEG; the real
Python detection service consumes it, runs real YOLO on the 4090 rig, publishes
real MQTT commands; the sim's virtual turret executes them. Every interface is
production-real. Hardware (Pi 5 edge port, camera, ESP32 actuator) is bought only
after the loop is validated in sim. See swap rule in
[contract](../../toolbelt/contracts/catsentry-v1.md).

Two parallel tracks against the frozen contract:
- **Track 1 (Claude, this repo):** detection service + decision engine.
- **Track 2 (Cursor/OpenAI budget, `worldsim` repo):** Unity sim. Standalone spec:
  [2026-08-19-worldsim-design.md](2026-08-19-worldsim-design.md).

## Layout

```
C:\Users\jaden\homelab\          # root repo: index, docs, toolbelt
  README.md
  docs/specs/
  toolbelt/contracts/catsentry-v1.md
  cat-sentry/                    # own git repo, public
    detection/                   # Python service
    firmware/                    # ESP32 (phase 3)
    docs/hardware.md
  worldsim/                      # own git repo, public — see worldsim spec
```

Reusable code stays in its project until a second consumer exists, then extracts
to `toolbelt/` (YAGNI). Contracts are toolbelt-first because two consumers exist
on day one.

## Track 1: detection service

Python 3.11+, `uv`-managed. Deps: `ultralytics` (YOLO), `opencv-python`,
`paho-mqtt`, `pyyaml`. SQLite via stdlib. Runs on the 4090 rig.

### Components

1. **Ingest** — `cv2.VideoCapture(source_url)` loop, reconnect on drop.
2. **Detector** — pretrained YOLO (COCO `cat` class, id 15). No training needed
   for v1. Confidence threshold config.
3. **Tracker** — ultralytics built-in ByteTrack (`model.track()`), gives stable
   per-cat track IDs within a session.
4. **Zone engine** — named polygons (normalized coords) in config YAML. Zones:
   `boxes` (the litterboxes — deterrent NEVER targets here), `floor_left`,
   `floor_right` (problem areas). Point-in-polygon on bbox bottom-center.
5. **State machine** (per track ID):
   `IDLE → IN_ZONE (dwell timer) → SUSPECT (squat heuristic) → WARNED (sound fired)
   → ESCALATED (air fired) → COOLDOWN (minutes, no re-fire)`.
   Squat heuristic v1: bbox height/width ratio drops below threshold AND centroid
   displacement < epsilon for `squat_seconds` (default 3 s). All thresholds in
   config — hardware world will need retuning (calibration knobs stay).
6. **Deterrent policy** — publishes `catsentry/deterrent/fire` per contract.
   Sound first; air only if SUSPECT persists `escalate_seconds` after sound.
   Hard safety rails: max fires per hour, global cooldown, never target `boxes`
   zone, disabled entirely via config flag (detection-only mode).
7. **Event store** — SQLite `events` table mirroring `catsentry/event` payload +
   JPEG snapshots to `events/YYYY-MM-DD/`.
8. **Alerter** — ntfy.sh push (topic in config) with snapshot on
   `squat_suspected` and `deterrent_fired`. Free, no account.
9. **Config** — single `config.yaml`: source URL, broker, zones, thresholds,
   rate limits, ntfy topic.

Threading model: MqttPublisher is non-blocking (paho background thread);
EventStore.save and NtfyNotifier.notify are synchronous by design — the C5
service runs them on a single worker thread fed by a queue, never inline on
the frame loop.

### Phase 2 (not this weekend)
- Per-cat ID: classifier on bbox crops. Training data: worldsim domain-randomized
  synthetic + accumulated real snapshots. Fills `cat_id` in events.
- Pi 5 edge port: quantized YOLO (ncnn/onnx) — the "efficient edge AI" milestone.

### Testing
- `pytest`: state machine unit tests with canned detection sequences (no video,
  no YOLO — pure logic). Zone engine point-in-polygon tests.
- YOLO smoke test: run detector over 2–3 downloaded cat clips, assert cat found.
- E2E (needs Track 2 done): mosquitto + worldsim + service; scripted sim cat
  squats in `floor_left`; assert `deterrent/fire` published within 10 s and sim
  acks. This run, screen-recorded, is the README demo GIF.

## Track 2: worldsim

Separate standalone spec (self-contained for Cursor):
[2026-08-19-worldsim-design.md](2026-08-19-worldsim-design.md). Summary: Unity 6
project, `Core` assembly (MJPEG streamer, MQTT bridge, scenario runner, dataset
capture) reusable across future scenarios (DnD universe etc.); `Scenarios/CatBasement`
is scenario #1.

## Hardware (deferred — doc only this weekend)

`cat-sentry/docs/hardware.md`, priced list, no purchase until sim validates loop:
Pi 5 8 GB (~$80), IR camera module (~$25–35), ESP32 devkit (~$8), piezo/speaker +
driver (~$5), air pump/solenoid valve + 12 V PSU (~$25–35), mounts/misc (~$20).
Total ≈ $165–185.

## Milestones

- **M1** — contract frozen, homelab scaffold, both repos initialized, worldsim
  spec handed to Cursor. (this weekend, day 1)
- **M2** — detection service works against downloaded cat video files; state
  machine tests green. (day 1–2)
- **M3** — closed loop: sim cat squats → real service → sim turret fires. Demo
  GIF. (day 2, depends on Track 2)
- **M4** — hardware go/no-go decision from sim learnings. (post-weekend)
- **M5+** — phase 2: per-cat ID, Pi edge port, ESP32 firmware, real deployment.

## Portfolio notes

Public repos: `cat-sentry`, `worldsim`. README leads with architecture diagram +
closed-loop demo GIF + "validated in a digital twin before buying hardware"
narrative. Efficiency story lands at M5 (Pi edge port).
