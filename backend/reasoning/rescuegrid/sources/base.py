from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol, runtime_checkable

from ..contracts import Event


def coerce_event(obj: Any) -> Event:
    if isinstance(obj, Event):
        return obj
    if isinstance(obj, (bytes, str)):
        obj = json.loads(obj)
    return Event.model_validate(obj)


@runtime_checkable
class EventSource(Protocol):
    """Anything that yields Event objects in arrival order. Replay adapters yield a fixed list;
    live adapters block on a socket/queue and yield forever."""

    def __iter__(self) -> Iterator[Event]: ...


class InMemorySource:
    def __init__(self, events: Iterable[Any]):
        self._events = [coerce_event(e) for e in events]

    def __iter__(self) -> Iterator[Event]:
        yield from self._events

    def __len__(self) -> int:
        return len(self._events)


class JsonFileSource(InMemorySource):
    """Replay adapter: a JSON array file (events/dummy_events.json) or a .jsonl file."""

    def __init__(self, path: str | Path, sort_by_timestamp: bool = True):
        p = Path(path)
        text = p.read_text()
        if p.suffix == ".jsonl":
            raw = [json.loads(l) for l in text.splitlines() if l.strip()]
        else:
            raw = json.loads(text)
        evs = [coerce_event(e) for e in raw]
        if sort_by_timestamp:
            evs.sort(key=lambda e: e.timestamp)
        super().__init__(evs)
        self.path = p


class JsonlStdinSource:
    """Live adapter with zero dependencies: one JSON event per line on stdin.
    e.g.  mosquitto_sub -t 'rescuegrid/events/#' | python scripts/ingest.py --source stdin"""

    def __init__(self, stream=None, on_error=None):
        self._stream = stream or sys.stdin
        self._on_error = on_error or (lambda line, exc: sys.stderr.write(f"[JsonlStdinSource] skipped bad line: {exc}\n"))

    def __iter__(self) -> Iterator[Event]:
        for line in self._stream:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                yield coerce_event(line)
            except Exception as e:  # malformed payloads are logged and skipped; the stream stays alive
                self._on_error(line, e)
