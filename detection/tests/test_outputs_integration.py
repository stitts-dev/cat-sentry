"""Opt-in integration test: MqttPublisher against a *real* local mosquitto
broker (localhost:1883, no auth, per docs/contract-catsentry-v1.md).

Skipped by default (see pyproject.toml's `-m 'not integration'`). To run:
    "C:\\Program Files\\mosquitto\\mosquitto.exe" -p 1883
    uv run pytest -m integration tests/test_outputs_integration.py
    (Ctrl+C the broker afterwards)
"""

from __future__ import annotations

import json
import os
import socket
import time

import pytest

from catsentry.config import BrokerConfig
from catsentry.outputs import TOPIC_DETERRENT_FIRE, TOPIC_EVENT, MqttPublisher

# ponytail: CATSENTRY_TEST_BROKER_PORT override exists because Windows WinNAT
# reserves a dynamic range that swallows 1883 on some machines; the contract
# port stays 1883, only this test's broker is relocatable.
_PORT = int(os.environ.get("CATSENTRY_TEST_BROKER_PORT", "1883"))
BROKER = BrokerConfig(host="localhost", port=_PORT)


def _broker_reachable() -> bool:
    try:
        with socket.create_connection((BROKER.host, BROKER.port), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.integration
@pytest.mark.skipif(not _broker_reachable(), reason="start mosquitto.exe on localhost:1883 first")
def test_publish_event_and_fire_are_received_by_a_real_subscriber():
    import paho.mqtt.client as mqtt

    received: list[tuple[str, dict]] = []

    def on_message(_client, _userdata, msg) -> None:
        received.append((msg.topic, json.loads(msg.payload)))

    subscriber = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    subscriber.on_message = on_message
    subscriber.connect(BROKER.host, BROKER.port)
    subscriber.subscribe(TOPIC_EVENT)
    subscriber.subscribe(TOPIC_DETERRENT_FIRE)
    subscriber.loop_start()

    publisher = MqttPublisher(BROKER)
    publisher.connect()
    time.sleep(0.5)  # let both sides finish connecting

    event = {"ts": "2026-08-19T21:04:00Z", "type": "zone_enter", "zone": "floor_left"}
    fire = {"ts": "2026-08-19T21:04:03Z", "level": "sound", "target": [0.46, 0.66]}
    publisher.publish_event(event)
    publisher.publish_fire(fire)

    deadline = time.time() + 5
    while len(received) < 2 and time.time() < deadline:
        time.sleep(0.1)

    publisher.close()
    subscriber.loop_stop()
    subscriber.disconnect()

    assert (TOPIC_EVENT, event) in received
    assert (TOPIC_DETERRENT_FIRE, fire) in received
