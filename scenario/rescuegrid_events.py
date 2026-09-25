"""Shared helper for every replay/live adapter: read the ONE scenario timeline, keep its clock,
and post six-field events to the RescueGrid event bus. Zero third-party dependencies (stdlib only)
so any teammate's script can `import rescuegrid_events` without a venv.

    import sys; sys.path.insert(0, "/home/hp2/Shresth/rescuegrid/scenario")
    from rescuegrid_events import scenario_events, ScenarioClock, post_event

    clock = ScenarioClock(speed=1.0)                      # speed=10 -> 10x faster replay
    for ev in scenario_events(sources=["sensor"]):        # already in contract shape, timestamp filled in
        clock.wait_until(ev["_t"])                        # blocks until the scenario clock reaches t
        post_event(ev)                                    # -> bus -> Kenil's fusion agent -> shared Neo4j
"""
from __future__ import annotations
import json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))          # /home/hp2/Shresth/rescuegrid
SCENARIO_FILE = os.environ.get("RESCUEGRID_SCENARIO", os.path.join(ROOT, "scenario", "scenario.json"))
BUS_URL = os.environ.get("RESCUEGRID_BUS", "http://127.0.0.1:8096")
CONTRACT = ("source", "timestamp", "confidence", "entity", "claim", "raw_evidence_ref")


def load_scenario(path: str = SCENARIO_FILE) -> dict:
    with open(path) as f:
        return json.load(f)


def scenario_start(sc: dict | None = None) -> datetime:
    sc = sc or load_scenario()
    return datetime.fromisoformat(sc["scenario_start"].replace("Z", "+00:00"))


def scenario_events(sources: list[str] | None = None, sc: dict | None = None) -> list[dict]:
    """Events of the given sources (all when None), in t order, converted to the contract:
    timestamp = scenario_start + t. The scenario-only key `t` is kept as `_t` for the clock."""
    sc = sc or load_scenario()
    start = scenario_start(sc)
    out = []
    for e in sorted(sc["events"], key=lambda e: e["t"]):
        if sources and e["source"] not in sources:
            continue
        ev = {k: v for k, v in e.items() if k != "t"}
        ev["timestamp"] = (start + timedelta(seconds=e["t"])).isoformat().replace("+00:00", "Z")
        ev["_t"] = e["t"]
        out.append(ev)
    return out


def validate(ev: dict) -> dict:
    missing = [k for k in CONTRACT if k not in ev]
    if missing:
        raise ValueError(f"event missing contract fields {missing}: {ev}")
    if not 0.0 <= float(ev["confidence"]) <= 1.0:
        raise ValueError(f"confidence out of range: {ev['confidence']}")
    return ev


def post_event(ev: dict, bus_url: str = BUS_URL, raw_bytes: int | None = None, timeout: float = 30.0) -> dict:
    """POST one event to the bus. `raw_bytes` = size of the raw feed data this event summarises
    (a JPEG frame, an audio clip...) so the bus can report bandwidth avoided. Returns the bus reply
    (fusion result incl. action, entity_id, latency)."""
    body = {k: v for k, v in validate(ev).items() if not k.startswith("_")}
    if raw_bytes is not None:
        body.setdefault("details", {})["raw_bytes"] = int(raw_bytes)
    req = urllib.request.Request(f"{bus_url}/events", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"bus rejected event {ev.get('event_id')}: {e.code} {e.read()[:300].decode(errors='replace')}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"event bus not reachable at {bus_url} (start it: Shresth/rescuegrid/bus/run.sh): {e.reason}") from e


class ScenarioClock:
    """Wall clock mapped onto scenario seconds. speed=2 plays the scenario twice as fast."""
    def __init__(self, speed: float = None):
        self.speed = float(speed if speed is not None else os.environ.get("RESCUEGRID_SPEED", "1"))
        self.started = time.monotonic()

    def now(self) -> float:
        return (time.monotonic() - self.started) * self.speed

    def wait_until(self, t: float) -> None:
        while self.now() < t:
            time.sleep(min(0.05, max(0.0, (t - self.now()) / self.speed)))


def log_line(ev: dict, reply: dict | None = None) -> str:
    tail = f" -> {reply.get('action')} {reply.get('entity_id')} ({reply.get('latency_ms', '?')} ms)" if reply else ""
    return f"[t={ev.get('_t', '?'):>4}s] {ev['source']:<12} {ev['entity']:<24} {ev['claim']:<16} conf {ev['confidence']:.2f}{tail}"
