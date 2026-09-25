"""RescueGrid event bus (Shresth). ONE door into the graph for every adapter.

POST /events        one event or a list, six-field contract (+ optional event_id/details)
                    -> validated -> Kenil's FusionAgent (in-process) -> shared Neo4j, inside one transaction
                    -> reply: {action, entity_id, applied, latency_ms, notes...}
GET  /events/recent?n=50   last events with their fusion outcome
GET  /metrics       Shresth's benchmark numbers, live: events in/applied, p50/p95 event->graph latency,
                    raw feed bytes vs event bytes (bandwidth avoided), on-device %, per-source counts
GET  /health

Every event is also appended to bus.log.jsonl (audit + replay). The bus never writes Cypher itself:
all graph writes go through the fusion agent (rule 1: Neo4j is the only source of truth).
"""
import json, os, sys, time, threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse

KENIL = os.environ.get("RESCUEGRID_REASONING", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reasoning"))   # backend/reasoning
sys.path.insert(0, KENIL)
os.environ.setdefault("NEO4J_URI", "bolt://127.0.0.1:7688")          # the SHARED graph, unless overridden
from rescuegrid.fusion.agent import FusionAgent                       # noqa: E402
from rescuegrid.graph import GraphStore                               # noqa: E402

LOG = os.environ.get("RESCUEGRID_BUS_LOG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bus.log.jsonl"))
CONTRACT = ("source", "timestamp", "confidence", "entity", "claim", "raw_evidence_ref")

app = FastAPI(title="RescueGrid event bus")
LOCK = threading.Lock()
RECENT: deque = deque(maxlen=1000)
LAT: deque = deque(maxlen=2000)
M = {"received": 0, "applied": 0, "recorded_only": 0, "invalid": 0, "errors": 0, "raw_bytes": 0, "event_bytes": 0,
     "cloud_escalations": 0, "by_source": {}, "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}
G = None; AGENT = None

def now_iso(): return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

@app.on_event("startup")
def start():
    global G, AGENT
    G = GraphStore(); G.ping(); AGENT = FusionAgent(G)
    print(f"event bus: fusion agent from {KENIL} -> {os.environ['NEO4J_URI']}", flush=True)

def pct(xs, p):
    if not xs: return None
    s = sorted(xs); return round(s[min(len(s) - 1, int(len(s) * p))], 1)

def ingest_one(ev: dict) -> dict:
    missing = [k for k in CONTRACT if k not in ev]
    size = len(json.dumps(ev).encode())
    with LOCK:
        M["received"] += 1; M["event_bytes"] += size
        M["by_source"][ev.get("source", "?")] = M["by_source"].get(ev.get("source", "?"), 0) + 1
        M["raw_bytes"] += int((ev.get("details") or {}).get("raw_bytes") or 0)
        if missing:
            M["invalid"] += 1
            rec = {"received_at": now_iso(), "event": ev, "result": {"action": "invalid", "applied": False, "error": f"missing {missing}"}}
            RECENT.append(rec); _log(rec); return rec["result"]
        t0 = time.perf_counter()
        r = AGENT.handle(ev)                                  # one transaction; never raises
        ms = (time.perf_counter() - t0) * 1000
        LAT.append(ms)
        if r.error: M["errors"] += 1
        elif r.applied: M["applied"] += 1
        else: M["recorded_only"] += 1
        result = {"event_id": r.event_id, "entity_id": r.entity_id, "action": r.action, "applied": r.applied,
                  "created_entity": r.created_entity, "notes": r.notes, "relationships": r.relationships, "error": r.error,
                  "latency_ms": round(ms, 1), "on_device": True}
        rec = {"received_at": now_iso(), "event": ev, "result": result}
        RECENT.append(rec); _log(rec)
        return result

def _log(rec):
    try:
        with open(LOG, "a") as f: f.write(json.dumps(rec, default=str) + "\n")
    except Exception as e: print("log write failed:", e, file=sys.stderr)

@app.post("/events")
def post_events(body: Any = Body(...)):
    evs = body if isinstance(body, list) else [body]
    if not evs or not all(isinstance(e, dict) for e in evs):
        raise HTTPException(400, "body must be an event object or a list of them")
    results = [ingest_one(e) for e in evs]
    out = results[0] if not isinstance(body, list) else results
    status = 200 if all(r["action"] not in ("invalid",) for r in results) else 422
    return JSONResponse(out, status_code=status)

@app.post("/reset")
def reset_counters():
    """Forget this run's events and benchmark counters (the graph itself is reset by the caller). Used by the twin
    gateway's 'reset incident' so the next rehearsal's numbers start clean without restarting the bus."""
    with LOCK:
        n = M["received"]; RECENT.clear(); LAT.clear()
        M.update(received=0, applied=0, recorded_only=0, invalid=0, errors=0, raw_bytes=0, event_bytes=0, cloud_escalations=0,
                 by_source={}, started=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return {"reset": True, "forgot_events": n}


@app.get("/events/recent")
def recent(n: int = 50, source: str | None = None):
    with LOCK: items = list(RECENT)
    if source: items = [i for i in items if i["event"].get("source") == source]
    return {"events": items[-n:]}

@app.get("/metrics")
def metrics():
    with LOCK:
        lat = list(LAT); m = dict(M)
    total = m["received"] or 1
    return {**m,
            "event_to_graph_ms": {"p50": pct(lat, .5), "p95": pct(lat, .95), "max": round(max(lat), 1) if lat else None, "n": len(lat)},
            "bandwidth": {"raw_feed_bytes": m["raw_bytes"], "event_bytes": m["event_bytes"],
                          "avoided_ratio": round(m["raw_bytes"] / m["event_bytes"], 1) if m["event_bytes"] and m["raw_bytes"] else None,
                          "note": "raw_feed_bytes counts only events whose adapter reported details.raw_bytes (frame/clip size)"},
            "on_device_pct": round(100 * (m["received"] - m["cloud_escalations"]) / total, 1),
            "cloud_escalation_rate_pct": round(100 * m["cloud_escalations"] / total, 1),
            "scenario_clock": (G.replay_now().isoformat() if G and G.replay_now() else None)}

@app.get("/health")
def health():
    ok = False
    try: ok = bool(G and G.ping())
    except Exception: pass
    return {"status": "ok" if ok else "degraded", "neo4j": os.environ["NEO4J_URI"], "neo4j_ok": ok, "events_received": M["received"]}
