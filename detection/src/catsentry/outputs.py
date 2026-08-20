"""MQTT publisher: catsentry/event (QoS 0) and catsentry/deterrent/fire
(QoS 1) per docs/contract-catsentry-v1.md, via paho-mqtt.

# ponytail: reconnect-with-backoff and buffering are both handled by paho
# itself rather than hand-rolled here. `connect_async()` + `loop_start()`
# runs paho's own `loop_forever(retry_first_connection=True)` in a
# background thread, which retries the *first* connect and every
# subsequent drop with exponential backoff (`reconnect_delay_set`) --
# a broker down at start or mid-run both self-heal with zero extra code.
# For buffering: QoS 0 (`catsentry/event`) is fire-and-forget by contract,
# so paho just drops a publish while disconnected -- fine, the next frame's
# event supersedes it. QoS 1 (`catsentry/deterrent/fire`) is a real
# decision to run hardware, so paho queues it internally and resends on
# reconnect (standard QoS>=1 semantics); `max_queued_messages_set` below
# only bounds that queue so a multi-day outage can't grow it forever.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

from catsentry.config import BrokerConfig

logger = logging.getLogger(__name__)

TOPIC_EVENT = "catsentry/event"
TOPIC_DETERRENT_FIRE = "catsentry/deterrent/fire"

QOS_EVENT = 0
QOS_DETERRENT_FIRE = 1

RECONNECT_MIN_DELAY_S = 1
RECONNECT_MAX_DELAY_S = 30

# Cap on paho's internal outgoing QoS>=1 queue (only deterrent/fire uses
# QoS>0 here) -- see module docstring.
FIRE_QUEUE_MAX = 100


class MqttClientLike(Protocol):
    """The slice of paho.mqtt.client.Client this module drives -- lets
    tests inject a fake instead of a real socket-backed client."""

    def connect_async(self, host: str, port: int) -> None: ...
    def reconnect_delay_set(self, min_delay: int, max_delay: int) -> None: ...
    def max_queued_messages_set(self, queue_size: int) -> object: ...
    def loop_start(self) -> object: ...
    def loop_stop(self) -> object: ...
    def disconnect(self) -> object: ...
    def publish(self, topic: str, payload: str, qos: int) -> object: ...


def _default_client() -> MqttClientLike:
    import paho.mqtt.client as mqtt

    return mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)


class MqttPublisher:
    """Publishes event/fire dicts to the broker described by `broker`.

    `connect()` never blocks and never raises, even if the broker is down --
    see module docstring. `publish_event`/`publish_fire` don't raise either,
    so a mid-run broker hiccup never takes the detection loop down with it.
    """

    def __init__(self, broker: BrokerConfig, *, client: MqttClientLike | None = None) -> None:
        self._broker = broker
        self._client = client if client is not None else _default_client()
        self._client.reconnect_delay_set(
            min_delay=RECONNECT_MIN_DELAY_S, max_delay=RECONNECT_MAX_DELAY_S
        )
        self._client.max_queued_messages_set(FIRE_QUEUE_MAX)

    def connect(self) -> None:
        self._client.connect_async(self._broker.host, self._broker.port)
        self._client.loop_start()

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def publish_event(self, event: dict) -> None:
        """catsentry/event, QoS 0."""
        self._publish(TOPIC_EVENT, event, QOS_EVENT)

    def publish_fire(self, fire: dict) -> None:
        """catsentry/deterrent/fire, QoS 1."""
        self._publish(TOPIC_DETERRENT_FIRE, fire, QOS_DETERRENT_FIRE)

    def _publish(self, topic: str, payload: dict, qos: int) -> None:
        try:
            result = self._client.publish(topic, json.dumps(payload), qos=qos)
            rc = getattr(result, "rc", 0)
            if rc != 0:
                logger.warning("mqtt publish to %s not sent (rc=%s), broker unreachable", topic, rc)
        except OSError as exc:
            logger.warning("mqtt publish to %s failed: %s", topic, exc)
