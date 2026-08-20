"""config.yaml loading + validation.

Validates every field named in docs/design.md (source, broker, zones,
thresholds, rate_limits, flags, ntfy, store) -- and every field is wired up
by now: catsentry.zones/catsentry.dwell consume `zones` and
`thresholds.dwell_seconds`; catsentry.policy adds the rest of `thresholds`
(squat_*, escalate_seconds), `rate_limits`, and `flags.deterrent_enabled`;
catsentry.service composes a full `Config` into the running pipeline --
`store` into EventStore, `ntfy` into NtfyNotifier, `broker` into
MqttPublisher. The C1 tracer CLI still only consumes `source.url`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from catsentry.zones import REQUIRED_ZONES


class ConfigError(ValueError):
    """Raised with every validation problem found, not just the first."""


@dataclass(frozen=True)
class SourceConfig:
    url: str


@dataclass(frozen=True)
class BrokerConfig:
    host: str
    port: int


@dataclass(frozen=True)
class ThresholdsConfig:
    confidence: float
    dwell_seconds: float
    squat_seconds: float
    squat_aspect_ratio: float
    centroid_epsilon: float
    escalate_seconds: float


@dataclass(frozen=True)
class RateLimitsConfig:
    max_fires_per_hour: int
    cooldown_minutes: float


@dataclass(frozen=True)
class FlagsConfig:
    deterrent_enabled: bool


@dataclass(frozen=True)
class NtfyConfig:
    topic: str


@dataclass(frozen=True)
class StoreConfig:
    db_path: str
    events_dir: str


@dataclass(frozen=True)
class Config:
    source: SourceConfig
    broker: BrokerConfig
    zones: dict[str, list[tuple[float, float]]]
    thresholds: ThresholdsConfig
    rate_limits: RateLimitsConfig
    flags: FlagsConfig
    ntfy: NtfyConfig
    store: StoreConfig


def _section(raw: dict, name: str, errors: list[str]) -> dict:
    """Pull a top-level mapping out of the config, recording an error (and
    returning {} so downstream field lookups don't also blow up) if it's
    missing or the wrong shape."""
    val = raw.get(name)
    if not isinstance(val, dict):
        errors.append(f"'{name}': missing or not a mapping")
        return {}
    return val


# The typed getters below return a harmless placeholder ("", 0.0, False) after
# recording an error -- the value is discarded anyway once `errors` is
# non-empty, and non-Optional returns keep Config construction free of
# type-ignore noise.


def _str(section: dict, key: str, prefix: str, errors: list[str]) -> str:
    val = section.get(key)
    if not isinstance(val, str) or not val.strip():
        errors.append(f"'{prefix}.{key}': expected a non-empty string, got {val!r}")
        return ""
    return val


def _num(
    section: dict, key: str, prefix: str, errors: list[str], *, positive: bool = False
) -> float:
    if key not in section:
        errors.append(f"'{prefix}.{key}': missing")
        return 0.0
    val = section[key]
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        errors.append(f"'{prefix}.{key}': expected a number, got {val!r}")
        return 0.0
    if positive and val <= 0:
        errors.append(f"'{prefix}.{key}': must be > 0, got {val}")
        return 0.0
    return float(val)


def _bool(section: dict, key: str, prefix: str, errors: list[str]) -> bool:
    val = section.get(key)
    if not isinstance(val, bool):
        errors.append(f"'{prefix}.{key}': expected true/false, got {val!r}")
        return False
    return val


def _zones(raw: dict, errors: list[str]) -> dict[str, list[tuple[float, float]]]:
    val = raw.get("zones")
    if not isinstance(val, dict) or not val:
        errors.append("'zones': missing or empty mapping")
        return {}

    zones: dict[str, list[tuple[float, float]]] = {}
    for name, points in val.items():
        if not isinstance(points, list) or len(points) < 3:
            errors.append(f"'zones.{name}': need a list of at least 3 [x, y] points")
            continue
        parsed: list[tuple[float, float]] = []
        ok = True
        for i, point in enumerate(points):
            valid_point = (
                isinstance(point, (list, tuple))
                and len(point) == 2
                and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in point)
                and all(0.0 <= c <= 1.0 for c in point)
            )
            if not valid_point:
                errors.append(f"'zones.{name}[{i}]': expected [x, y] with x, y in [0, 1]")
                ok = False
                continue
            parsed.append((float(point[0]), float(point[1])))
        if ok:
            zones[name] = parsed

    missing = [z for z in REQUIRED_ZONES if z not in val]
    if missing:
        errors.append(f"'zones': missing required zone(s) {missing} (see docs/design.md)")
    return zones


def load_config(path: str | Path) -> Config:
    """Load and validate a catsentry config.yaml, raising ConfigError with
    every problem found (not just the first) if anything is wrong."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping, got {type(raw).__name__}")

    errors: list[str] = []

    source = _section(raw, "source", errors)
    url = _str(source, "url", "source", errors)

    broker = _section(raw, "broker", errors)
    host = _str(broker, "host", "broker", errors)
    port = _num(broker, "port", "broker", errors, positive=True)

    zones = _zones(raw, errors)

    thresholds = _section(raw, "thresholds", errors)
    threshold_vals = {
        field: _num(thresholds, field, "thresholds", errors, positive=True)
        for field in (
            "confidence",
            "dwell_seconds",
            "squat_seconds",
            "squat_aspect_ratio",
            "centroid_epsilon",
            "escalate_seconds",
        )
    }
    if threshold_vals["confidence"] > 1.0:
        errors.append(
            f"'thresholds.confidence': must be in (0, 1], got {threshold_vals['confidence']}"
        )

    rate_limits = _section(raw, "rate_limits", errors)
    max_fires_per_hour = _num(
        rate_limits, "max_fires_per_hour", "rate_limits", errors, positive=True
    )
    cooldown_minutes = _num(rate_limits, "cooldown_minutes", "rate_limits", errors, positive=True)

    flags = _section(raw, "flags", errors)
    deterrent_enabled = _bool(flags, "deterrent_enabled", "flags", errors)

    ntfy = _section(raw, "ntfy", errors)
    topic = _str(ntfy, "topic", "ntfy", errors)

    store = _section(raw, "store", errors)
    db_path = _str(store, "db_path", "store", errors)
    events_dir = _str(store, "events_dir", "store", errors)

    if errors:
        bullets = "\n  - ".join(errors)
        raise ConfigError(f"invalid config {path}:\n  - {bullets}")

    return Config(
        source=SourceConfig(url=url),
        broker=BrokerConfig(host=host, port=int(port)),
        zones=zones,
        thresholds=ThresholdsConfig(**threshold_vals),
        rate_limits=RateLimitsConfig(
            max_fires_per_hour=int(max_fires_per_hour),
            cooldown_minutes=cooldown_minutes,
        ),
        flags=FlagsConfig(deterrent_enabled=deterrent_enabled),
        ntfy=NtfyConfig(topic=topic),
        store=StoreConfig(db_path=db_path, events_dir=events_dir),
    )
