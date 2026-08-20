# Cat Sentry — Hardware Parts List (H1)

Status: **document only — no purchases**. Buy nothing until the digital-twin
closed loop (`worldsim` + detection service) is validated end-to-end per
[design.md](design.md). This doc exists so the M4 milestone ("hardware
go/no-go decision from sim learnings") has real prices to decide against.

Prices are live-checked 2026-08-19 (today). Electronics/SD prices move —
re-check before actually ordering.

## 1. Compute: Raspberry Pi 5 8GB

| Part | Price | Link |
|---|---|---|
| Raspberry Pi 5, 8GB RAM | **$175.00** | [PiShop.us — Raspberry Pi 5 8GB](https://www.pishop.us/product/raspberry-pi-5-8gb/) (official reseller, in stock) |

**Role:** edge compute target for the Phase 2 "efficient edge AI" milestone —
runs a quantized (ncnn/onnx) YOLO once the detection service is ported off
the 4090 rig. 8GB is chosen over 4GB so there's headroom for the per-cat ID
classifier (Phase 2) without a second hardware swap.

Not a deterrent part — no sim-validation gate. It's general compute; the
only thing sim validates is whether the *pipeline* (ingest → detect → zone →
state machine) is worth porting at all.

**Price note:** budgeted at ~$80 in design.md. Actual price is **more than
double** that — see [Deviation](#deviation-from-150-200-target) below. This
single line item is the primary reason the total blows the target.

Running total: **$175.00**

## 2. Camera: IR / NoIR options

| Part | Price | Link |
|---|---|---|
| Raspberry Pi Camera Module 3 NoIR (76° FOV, 12MP IMX708) | **$27.50** | [PiShop.us — Camera Module 3 NoIR](https://www.pishop.us/product/raspberry-pi-camera-module-3-noir/) (official reseller, in stock) |
| Alternative: Camera Module 3 **Wide** NoIR (102° FOV) | $38.50 | [PiShop.us — Camera Module 3 Wide NoIR](https://www.pishop.us/product/raspberry-pi-camera-module-3-wide-noir/) (in stock) |

**Role:** replaces the sim's virtual MJPEG stream with a real one per the
[contract](contract-catsentry-v1.md) swap rule (video source URL is the only
change — detector code is untouched). NoIR (no IR-cut filter) so it stays
usable in a dim/unlit basement corner; standard lens picked over Wide because
the zone layout (`floor_left`/`floor_right`/`boxes`) is currently tuned for
worldsim's fixed camera framing at 16:9 — Wide would need the zone polygons
re-tuned in sim first. Switch to Wide only if sim proves the standard FOV
can't see all three zones from one mount point.

**Known gap, not priced here:** NoIR removes the IR-cut filter but adds no
IR *illumination*. If the real basement corner is fully dark at night, this
camera sees nothing without an IR light source (~$10–15 illuminator board,
future add, not in this list — flag at M4 if the deployment site has no
ambient light).

Not deterrent-side — sim doesn't validate this, it validates whether the
zone/detection logic transfers to a real lens's distortion and low-light
noise, which is a Phase 2 concern (per design.md, hardware camera port is
explicitly deferred past this weekend).

Running total: **$175.00 + $27.50 = $202.50**

## 3. Actuator brain: ESP32 devkit

| Part | Price | Link |
|---|---|---|
| ESP32-WROOM-32 DevKitC board (KeeYees, 38-pin, **2-pack**) | **$11.99** | [Amazon — KeeYees ESP32S DevKitC 2-pack](https://www.amazon.com/dp/B07QCP2451) (in stock) |

**Role:** runs the firmware (phase 3, not built yet) that subscribes to
`catsentry/deterrent/fire` and `catsentry/telemetry`, and drives the relay
module (§5) — the hardware mirror of worldsim's virtual turret, per the
contract's swap rule (actuator implementation changes, topics/payloads
don't). Bought as a 2-pack instead of a single official DigiKey unit
(ESP32-DEVKITC-32E, $10, but **0 in stock, backordered to Feb 2027** as of
this check) because it's cheaper per-unit and a spare survives the inevitable
bricked board during firmware bring-up.

**Validated by sim before buy:** the ESP32 only needs to exist once the
actuator side of the loop is being ported off the Unity virtual turret. Sim
validates the MQTT contract (topics, payload shape, ack timing) with a fake
actuator first — no reason to own real ESP32 hardware before that contract
is proven stable, since a `catsentry-v2` bump would just mean re-flashing
anyway.

Running total: **$202.50 + $11.99 = $214.49**

## 4. Deterrent — sound (warning stage)

| Part | Price | Link |
|---|---|---|
| Adafruit STEMMA Piezo Driver Amp — PAM8904 | **$4.95** | [Adafruit #5791](https://www.adafruit.com/product/5791) (in stock) |
| Large Enclosed Piezo Element w/Wires (30mm) | **$0.95** | [Adafruit #1739](https://www.adafruit.com/product/1739) (in stock) |
| **Subtotal (speaker + driver)** | **$5.90** | |

**Role:** the "sound first" warning stage of the deterrent policy
(`level: "sound"` in `deterrent/fire`). The PAM8904 is a proper driver (not
a bare piezo on a GPIO pin) — it boosts the ESP32's 3.3V logic to a louder
~13Vpp square wave, closer to what design.md's "piezo/speaker + driver"
line actually implies versus a self-oscillating $1 buzzer that's too quiet
to be a reliable first-stage deterrent.

**Validated by sim before buy:** this is a deterrent part — buy only after
sim confirms the state machine's sound-firing logic is sane: correct
dwell/squat timing before firing, max-fires-per-hour and global cooldown
respected, and — critically — that `boxes` zone is never targeted. A stand-in
`playsound()` call in the sim loop can validate the *timing and rate-limit*
logic without any hardware. Litterbox aversion risk (per design.md's
domain constraint) means mis-tuned timing is the one thing worth catching
before real cats hear a real speaker.

Running total: **$214.49 + $5.90 = $220.39**

## 5. Deterrent — air puff (escalation stage) + switching

| Part | Price | Link |
|---|---|---|
| 12V DC mini diaphragm air pump (DEWIN, ultra-silent) | **$9.97** | [Amazon — 12V DC Air Pump](https://www.amazon.com/dp/B07FGFPKNS) (in stock) |
| 1/4" 12V DC normally-closed solenoid air valve (Fafeicy) | **$11.54** | [Amazon — 12V NC Solenoid Air Valve](https://www.amazon.com/dp/B08K4PYL61) (in stock) |
| 12V 2A (24W) wall-wart power supply, 5.5×2.1mm barrel | **$8.99** | [Amazon — 12V 2A Power Adapter](https://www.amazon.com/dp/B013HJI0Q6) (in stock) |
| 2-channel 5V relay module w/ optocoupler (SunFounder) | **$6.79** | [Amazon — SunFounder 2-Channel Relay](https://www.amazon.com/dp/B00E0NTPP4) (in stock) |
| 4mm silicone airline tubing, 2.5m (uxcell) | **$5.59** | [Amazon — Airline Tubing](https://www.amazon.com/dp/B01MS0DXCQ) (in stock) |
| **Subtotal (air puff chain)** | **$42.88** | |

**Role:** the "air" escalation stage (`level: "air"`, fired only if SUSPECT
persists past `escalate_seconds` after an unacknowledged sound warning). The
relay module isn't in design.md's original line item but is not optional —
the ESP32's 3.3V GPIO cannot switch a 12V pump or solenoid coil directly;
something has to sit between logic and load. A 2-channel relay (pump on one
channel, valve on the other) is the simplest thing that works and is cheap
enough not to matter to the total. One 12V 2A supply powers both the pump
(~3–5W) and the valve coil (4W) with headroom.

**Validated by sim before buy:** this is the highest-risk deterrent part —
misfiring air at a cat near (not just beside) a litterbox is exactly the
litterbox-aversion failure mode design.md calls out as the reason deterrents
are floor-zone-only and sound-first. Do not buy pump/valve/tubing until sim
has proven, over many scripted runs, that: (1) air only fires after sound
was already tried and ignored, (2) the target coordinate is always in a
floor zone and never in `boxes`, and (3) the global/per-hour cooldowns hold
under repeated triggering. The relay module itself is generic hardware (no
sim-specific behavior) — bench-test it with a multimeter/LED before ever
wiring it to line power.

Running total: **$220.39 + $42.88 = $263.27**

## 6. Mounts & misc

| Part | Price | Link |
|---|---|---|
| IP65 weatherproof ABS project box, 158×90×60mm (PINFOX) | **$8.99** | [Amazon — Waterproof Project Box](https://www.amazon.com/dp/B07TS6RY85) (in stock) |
| Adjustable gooseneck clamp mount (for camera) | **$13.99** | [Amazon — Gooseneck Clamp Mount](https://www.amazon.com/dp/B083BRX7D6) (in stock) |
| Dupont jumper wire kit, 200pc M/F/M-M assortment (HiLetgo) | **$9.29** | [Amazon — Jumper Wire Kit](https://www.amazon.com/dp/B077X99KX1) (in stock) |
| **Subtotal (mounts/misc)** | **$32.27** | |

**Role:** the project box houses the ESP32 + relay module away from splash
zone (air pump exhaust, general basement dust); the gooseneck clamp mounts
the camera at a fixed elevated point with a clear line to both floor zones
(camera board zip-tied/mounted into the clamp jaw — not a purpose-built
camera mount, but the cheapest reliable option that doesn't need 3D
printing); jumper wires connect ESP32 ↔ relay ↔ piezo driver.

Not deterrent-side, no sim gate — purely mechanical/wiring.

Running total: **$263.27 + $32.27 = $295.54**

## Subtotal — design.md's six buckets

| Bucket | Price |
|---|---|
| 1. Pi 5 8GB | $175.00 |
| 2. IR camera | $27.50 |
| 3. ESP32 devkit | $11.99 |
| 4. Speaker/piezo + driver | $5.90 |
| 5. Air pump + valve + PSU (+ relay + tubing) | $42.88 |
| 6. Mounts/misc | $32.27 |
| **Subtotal** | **$295.54** |

## Essential Pi accessories (not itemized in design.md, but required to boot)

design.md's six buckets don't include a way to power or boot the Pi 5
itself. Listing them for completeness — a "buy decision" doc that's silent
on these would understate the real cost:

| Part | Price | Link |
|---|---|---|
| Official Raspberry Pi 27W USB-C power supply | **$12.95** | [PiShop.us — Pi 27W USB-C PSU](https://www.pishop.us/product/raspberry-pi-27w-usb-c-power-supply-black-us/) (in stock) |
| Official Raspberry Pi microSD card, A2/V30, 32GB | **$24.95** | [Amazon — SanDisk Ultra 32GB microSDHC A1](https://www.amazon.com/dp/B073JWXGNT) (official Pi-branded card was out of stock at check time; SanDisk A1 substituted — A2 preferred but A1 boots fine) (in stock) |
| **Subtotal (accessories)** | **$37.90** | |

## Grand total

**$295.54 (six buckets) + $37.90 (accessories) = $333.44**

## Deviation from $150–200 target

design.md estimated **$165–185** total. Actual live pricing comes in at
**~$333**, roughly **80% over** even the high end of the six-bucket
estimate. This is a real deviation, not scope creep — here's the breakdown:

1. **Pi 5 8GB is the dominant driver.** design.md budgeted ~$80; it's
   currently **$175** — more than double. This isn't a pricing mistake on
   this doc's part: Raspberry Pi's own blog and independent reporting
   (Tom's Hardware, the Raspberry Pi forums' price-rise tracking thread)
   confirm a 2026 LPDDR4 memory shortage — driven by AI-infrastructure
   demand for fab capacity — pushed Pi 5 8GB from $80 → $95 (Oct 2025) →
   $125 (Dec 2025) → $165 (Feb 2026) → $175 (Apr 2026), where it's held
   since. Raspberry Pi's founder has publicly called this temporary. **This
   one line item alone accounts for ~$95 of the overage** — if the Pi were
   still $80, the six-bucket subtotal would be $200.54, right at the top of
   the original target.
2. **microSD pricing moved with it.** The same memory shortage is hitting
   flash storage — a plain 32GB card that would have been $6–8 a year ago
   is $24.95 live today (the official Pi-branded 32GB card was out of stock
   entirely at check time). This is the accessories bucket's biggest line.
3. **The remainder is filled-in scope, not padding.** design.md's estimate
   didn't itemize a relay module, airline tubing, or a camera mount — those
   are genuinely required for the air-puff chain and camera to physically
   work, not optional extras. They add roughly $36 combined, small next to
   the memory-driven overage.

**Implication for the M4 go/no-go decision:** the loop-validation-first
approach this project already committed to (zero purchases until sim proves
the loop) buys time for exactly the kind of price normalization Raspberry Pi
has said to expect. If the Pi 5 memory shortage eases before M4, re-check
this doc — the six-bucket total could land back near $200–220 without any
change to the parts list itself. If it hasn't eased, the honest fallback
is either accepting ~$300–335 as the real 2026 price of this hardware, or
revisiting whether a Pi 5 is required at all for M5's "efficient edge AI"
milestone versus a cheaper board that can still run a quantized YOLO model.
