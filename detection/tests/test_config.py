from pathlib import Path

import pytest
import yaml

from catsentry.config import BrokerConfig, ConfigError, StoreConfig, load_config

SAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "config.sample.yaml"


def test_sample_config_loads_and_validates():
    cfg = load_config(SAMPLE_CONFIG)

    assert cfg.source.url == "http://localhost:8089/stream"
    assert cfg.broker == BrokerConfig(host="localhost", port=1883)
    assert set(cfg.zones) == {"boxes", "floor_left", "floor_right"}
    assert all(len(points) >= 3 for points in cfg.zones.values())
    assert 0 < cfg.thresholds.confidence <= 1
    assert cfg.rate_limits.max_fires_per_hour == 4
    assert cfg.flags.deterrent_enabled is False
    assert cfg.ntfy.topic == "catsentry-alerts"
    assert cfg.store == StoreConfig(db_path="events.db", events_dir="events")


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_invalid_yaml_raises_config_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("source: [this is not: valid: yaml")

    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(path)


def test_non_mapping_top_level_raises_config_error(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- just\n- a\n- list\n")

    with pytest.raises(ConfigError, match="top level must be a mapping"):
        load_config(path)


def test_missing_sections_are_all_reported_together(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("source:\n  url: video.mp4\n")

    with pytest.raises(ConfigError) as exc_info:
        load_config(path)

    message = str(exc_info.value)
    for expected in (
        "'broker'",
        "'zones'",
        "'thresholds'",
        "'rate_limits'",
        "'flags'",
        "'ntfy'",
        "'store'",
    ):
        assert expected in message


def test_zones_missing_required_zone(tmp_path):
    cfg = yaml.safe_load(SAMPLE_CONFIG.read_text())
    del cfg["zones"]["floor_left"]
    del cfg["zones"]["floor_right"]
    path = tmp_path / "zones.yaml"
    path.write_text(yaml.safe_dump(cfg))

    with pytest.raises(ConfigError, match=r"floor_left.*floor_right|missing required zone"):
        load_config(path)


def test_zone_point_out_of_range(tmp_path):
    path = tmp_path / "zones.yaml"
    path.write_text(SAMPLE_CONFIG.read_text().replace("[0.60, 0.55]", "[1.5, 0.55]", 1))

    with pytest.raises(ConfigError, match=r"x, y in \[0, 1\]"):
        load_config(path)


def test_confidence_out_of_range(tmp_path):
    path = tmp_path / "conf.yaml"
    path.write_text(SAMPLE_CONFIG.read_text().replace("confidence: 0.5", "confidence: 1.5"))

    with pytest.raises(ConfigError, match="thresholds.confidence"):
        load_config(path)


def test_flag_must_be_boolean(tmp_path):
    path = tmp_path / "flags.yaml"
    path.write_text(
        SAMPLE_CONFIG.read_text().replace("deterrent_enabled: false", 'deterrent_enabled: "no"')
    )

    with pytest.raises(ConfigError, match="flags.deterrent_enabled"):
        load_config(path)


def test_port_must_be_positive_number(tmp_path):
    path = tmp_path / "broker.yaml"
    path.write_text(SAMPLE_CONFIG.read_text().replace("port: 1883", "port: -1"))

    with pytest.raises(ConfigError, match="broker.port"):
        load_config(path)
