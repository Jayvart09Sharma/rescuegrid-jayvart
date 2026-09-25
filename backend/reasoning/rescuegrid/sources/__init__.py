"""Pluggable event sources. A source is anything iterable that yields Event objects; the
fusion agent never cares where they came from. Swap JsonFileSource for MqttSource (or write
a 5-line adapter for Shresth's bus) and the write logic is untouched."""
from .base import EventSource, InMemorySource, JsonFileSource, JsonlStdinSource, coerce_event
from .mqtt import MqttSource

__all__ = ["EventSource", "InMemorySource", "JsonFileSource", "JsonlStdinSource", "MqttSource", "coerce_event"]
