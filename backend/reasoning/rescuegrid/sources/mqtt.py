"""Live adapter for the master plan's MQTT feeds (GPS, sensors). Requires `pip install
paho-mqtt` only when actually used; the rest of the package never imports it."""
from __future__ import annotations

import queue
from typing import Iterator, Sequence

from ..contracts import Event
from .base import coerce_event


class MqttSource:
    def __init__(self, topics: Sequence[str] = ("rescuegrid/events/#",), host: str = "127.0.0.1", port: int = 1883, qos: int = 1):
        self.topics, self.host, self.port, self.qos = list(topics), host, port, qos
        self._q: "queue.Queue[Event | Exception]" = queue.Queue()

    def _on_message(self, client, userdata, msg):
        try:
            self._q.put(coerce_event(msg.payload))
        except Exception as e:  # malformed payloads must not kill the subscriber
            self._q.put(e)

    def __iter__(self) -> Iterator[Event]:
        try:
            import paho.mqtt.client as mqtt  # lazy: optional dependency
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("MqttSource needs `pip install paho-mqtt`") from e
        client = mqtt.Client()
        client.on_message = self._on_message
        client.connect(self.host, self.port)
        for t in self.topics:
            client.subscribe(t, qos=self.qos)
        client.loop_start()
        try:
            while True:
                item = self._q.get()
                if isinstance(item, Exception):
                    print(f"[MqttSource] dropped bad payload: {item}")
                    continue
                yield item
        finally:
            client.loop_stop()
            client.disconnect()
