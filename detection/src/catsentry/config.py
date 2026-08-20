"""config.yaml loading + validation.

Validates every field named in docs/design.md (source, broker, zones,
thresholds, rate_limits, flags, ntfy) even though the C1 tracer CLI only
consumes `source.url` so far.

# ponytail: broker/thresholds/rate_limits/flags/ntfy are validated but not
# wired to anything yet -- the zone engine, dwell/squat state machine, and
# deterrent policy land in later issues (C2+) and will consume them then.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

REQUIRED_ZONES = ("boxes", "floor_left", "floor_right")


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
class Config:
    source: SourceConfig
    broker: BrokerConfig
    zones: dict[str, list[tuple[float, float]]]
    thresholds: ThresholdsConfig
    rate_limits: RateLimitsConfig
    flags: FlagsConfig
    ntfy: NtfyConfig


def _section(raw: dict, name: str, errors: list[str]) -> dict:
    """Pull a top-level mapping out of the config, recording an error (and
    returning {} so downstream field lookups don't also blow up) if it's
    missing or the wrong shape."""
    val = raw.get(name)
    if not isinstance(val, dict):
        errors.append(f"'{name}': missing or not a mapping")
        return {}
    return val


def _str(section: dict, key: str, prefix: str, errors: list[str]) -> str | None:
    val = section.get(key)
    if not isinstance(val, str) or not val.strip():
        errors.append(f"'{prefix}.{key}': expected a non-empty string, got {val!r}")
        return None
    return val


def _num(
    section: dict, key: str, prefix: str, errors: list[str], *, positive: bool = False
) -> float | None:
    if key not in section:
        errors.append(f"'{prefix}.{key}': missing")
        return None
    val = section[key]
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        errors.append(f"'{prefix}.{key}': expected a number, got {val!r}")
        return None
    if positive and val <= 0:
        errors.append(f"'{prefix}.{key}': must be > 0, got {val}")
        return None
    return float(val)


def _bool(section: dict, key: str, prefix: str, errors: list[str]) -> bool | None:
    val = section.get(key)
    if not isinstance(val, bool):
        errors.append(f"'{prefix}.{key}': expected true/false, got {val!r}")
        return None
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
    confidence = _num(thresholds, "confidence", "thresholds", errors, positive=True)
    if confidence is not None and not (0.0 < confidence <= 1.0):
        errors.append(f"'thresholds.confidence': must be in (0, 1], got {confidence}")
    dwell_seconds = _num(thresholds, "dwell_seconds", "thresholds", errors, positive=True)
    squat_seconds = _num(thresholds, "squat_seconds", "thresholds", errors, positive=True)
    squat_aspect_ratio = _num(
        thresholds, "squat_aspect_ratio", "thresholds", errors, positive=True
    )
    centroid_epsilon = _num(thresholds, "centroid_epsilon", "thresholds", errors, positive=True)
    escalate_seconds = _num(thresholds, "escalate_seconds", "thresholds", errors, positive=True)

    rate_limits = _section(raw, "rate_limits", errors)
    max_fires_per_hour = _num(
        rate_limits, "max_fires_per_hour", "rate_limits", errors, positive=True
    )
    cooldown_minutes = _num(rate_limits, "cooldown_minutes", "rate_limits", errors, positive=True)

    flags = _section(raw, "flags", errors)
    deterrent_enabled = _bool(flags, "deterrent_enabled", "flags", errors)

    ntfy = _section(raw, "ntfy", errors)
    topic = _str(ntfy, "topic", "ntfy", errors)

    if errors:
        bullets = "\n  - ".join(errors)
        raise ConfigError(f"invalid config {path}:\n  - {bullets}")

    return Config(
        source=SourceConfig(url=url),  # type: ignore[arg-type]
        broker=BrokerConfig(host=host, port=int(port)),  # type: ignore[arg-type]
        zones=zones,
        thresholds=ThresholdsConfig(
            confidence=confidence,  # type: ignore[arg-type]
            dwell_seconds=dwell_seconds,  # type: ignore[arg-type]
            squat_seconds=squat_seconds,  # type: ignore[arg-type]
            squat_aspect_ratio=squat_aspect_ratio,  # type: ignore[arg-type]
            centroid_epsilon=centroid_epsilon,  # type: ignore[arg-type]
            escalate_seconds=escalate_seconds,  # type: ignore[arg-type]
        ),
        rate_limits=RateLimitsConfig(
            max_fires_per_hour=int(max_fires_per_hour),  # type: ignore[arg-type]
            cooldown_minutes=cooldown_minutes,  # type: ignore[arg-type]
        ),
        flags=FlagsConfig(deterrent_enabled=deterrent_enabled),  # type: ignore[arg-type]
        ntfy=NtfyConfig(topic=topic),  # type: ignore[arg-type]
    )
