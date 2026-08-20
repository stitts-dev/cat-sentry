"""MqttPublisher tests against a fake paho-shaped client -- no real broker
(mosquitto or otherwise) involved, matching MqttClientLike's protocol."""

from __future__ import annotations

import json

from catsentry.config import BrokerConfig
from catsentry.outputs import (
    FIRE_QUEUE_MAX,
    QOS_DETERRENT_FIRE,
    QOS_EVENT,
    RECONNECT_MAX_DELAY_S,
    RECONNECT_MIN_DELAY_S,
    TOPIC_DETERRENT_FIRE,
    TOPIC_EVENT,
    MqttPublisher,
)

BROKER = BrokerConfig(host="localhost", port=1883)


class FakeResult:
    def __init__(self, rc: int = 0) -> None:
        self.rc = rc


class FakeMqttClient:
    """Records every call a real paho.mqtt.client.Client would receive;
    `connected` toggles whether publish() reports success or NO_CONN (4),
    matching paho's own MQTT_ERR_NO_CONN return code."""

    def __init__(self) -> None:
        self.connected = True
        self.connect_async_calls: list[tuple[str, int]] = []
        self.reconnect_delay: tuple[int, int] | None = None
        self.max_queued: int | None = None
        self.loop_started = False
        self.loop_stopped = False
        self.disconnected = False
        self.published: list[tuple[str, str, int]] = []
        self.raise_on_publish: Exception | None = None

    def connect_async(self, host: str, port: int) -> None:
        self.connect_async_calls.append((host, port))

    def reconnect_delay_set(self, min_delay: int, max_delay: int) -> None:
        self.reconnect_delay = (min_delay, max_delay)

    def max_queued_messages_set(self, queue_size: int) -> None:
        self.max_queued = queue_size

    def loop_start(self) -> None:
        self.loop_started = True

    def loop_stop(self) -> None:
        self.loop_stopped = True

    def disconnect(self) -> None:
        self.disconnected = True

    def publish(self, topic: str, payload: str, qos: int) -> FakeResult:
        if self.raise_on_publish is not None:
            raise self.raise_on_publish
        if not self.connected:
            return FakeResult(rc=4)  # MQTT_ERR_NO_CONN
        self.published.append((topic, payload, qos))
        return FakeResult(rc=0)


def make_publisher(client: FakeMqttClient) -> MqttPublisher:
    return MqttPublisher(BROKER, client=client)


def test_init_configures_backoff_and_bounded_fire_queue():
    client = FakeMqttClient()
    make_publisher(client)

    assert client.reconnect_delay == (RECONNECT_MIN_DELAY_S, RECONNECT_MAX_DELAY_S)
    assert client.max_queued == FIRE_QUEUE_MAX


def test_connect_uses_async_connect_and_starts_background_loop():
    # connect_async() never blocks/raises even if the broker is down at
    # startup -- paho's loop thread (loop_start) is what actually dials in
    # and retries, so connect() itself can't fail here (see outputs.py).
    client = FakeMqttClient()
    publisher = make_publisher(client)

    publisher.connect()

    assert client.connect_async_calls == [("localhost", 1883)]
    assert client.loop_started is True


def test_close_stops_loop_and_disconnects():
    client = FakeMqttClient()
    publisher = make_publisher(client)
    publisher.connect()

    publisher.close()

    assert client.loop_stopped is True
    assert client.disconnected is True


def test_publish_event_sends_json_on_event_topic_at_qos_0():
    client = FakeMqttClient()
    publisher = make_publisher(client)
    event = {"ts": "2026-08-19T00:00:00Z", "type": "zone_enter", "zone": "floor_left"}

    publisher.publish_event(event)

    assert len(client.published) == 1
    topic, payload, qos = client.published[0]
    assert topic == TOPIC_EVENT
    assert json.loads(payload) == event
    assert qos == QOS_EVENT


def test_publish_fire_sends_json_on_deterrent_fire_topic_at_qos_1():
    client = FakeMqttClient()
    publisher = make_publisher(client)
    fire = {"ts": "2026-08-19T00:00:03Z", "level": "sound", "target": [0.46, 0.66]}

    publisher.publish_fire(fire)

    assert len(client.published) == 1
    topic, payload, qos = client.published[0]
    assert topic == TOPIC_DETERRENT_FIRE
    assert json.loads(payload) == fire
    assert qos == QOS_DETERRENT_FIRE


def test_publish_event_while_disconnected_does_not_raise_and_is_dropped():
    client = FakeMqttClient()
    client.connected = False
    publisher = make_publisher(client)

    publisher.publish_event({"ts": "x", "type": "zone_enter", "zone": "floor_left"})  # no raise

    assert client.published == []


def test_publish_fire_while_disconnected_does_not_raise():
    # paho queues QoS>=1 publishes internally and resends on reconnect (see
    # outputs.py docstring) -- from this module's side, the call just
    # shouldn't raise or crash the detection loop.
    client = FakeMqttClient()
    client.connected = False
    publisher = make_publisher(client)

    publisher.publish_fire({"ts": "x", "level": "sound", "target": [0.1, 0.1]})  # no raise

    assert client.published == []


def test_publish_swallows_client_exceptions():
    client = FakeMqttClient()
    client.raise_on_publish = OSError("socket error")
    publisher = make_publisher(client)

    publisher.publish_event({"ts": "x", "type": "zone_enter", "zone": "floor_left"})  # no raise
    publisher.publish_fire({"ts": "x", "level": "sound", "target": [0.1, 0.1]})  # no raise
