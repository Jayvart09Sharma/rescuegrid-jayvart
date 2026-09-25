"""RescueGrid frontend gateway (Shresth). The ONE door from the backend into Pranay's Command Twin.

The twin keeps a read-only mirror of the graph and only ever receives it over one socket, so this process:
  * WS   /stream            seed (the graph as the twin's nodes/edges) -> then, for every event the bus ingests,
                            {type:'event', event, patches} where `patches` is the DIFF of the Neo4j graph before/after
                            that event (rule 1: the twin renders the graph, never the feeds); plus periodic
                            {type:'patch'} diffs, {type:'clock', t} (scenario seconds), {type:'network'}, {type:'suggestion'}
  * POST /qa                {question, mode?} -> Kenil's Q&A (:8095) -> FlyToSignal {target, highlight[], answer, cypher}
  * GET/POST /netsim/status Jayvant's network-condition simulator, in the twin's shape {state, kbps, latencyMs}
                            (forwarded to his Flask service on :5000 when it is up)
  * POST /suggestions/{id}  the IC's decision on a suggested reroute -> recorded in the graph through the bus (rule 3)
  * GET  /evidence/{ref}    the frame behind a raw_evidence_ref (Aditya's v2 frames, the scripted samples)
  * GET  /                  the built twin (mockfrontend/edgeAI/frontend/dist-live) so everything is same-origin
  * GET  /health, /mapping

Ids: the twin's seed city uses its own ids (Main-St-5, Rescue-Team-4, Hospital-Valley...). MAP below translates the
graph's ids (Road-Main, Team-Rescue4, Facility-CountyGeneral...) to the twin's; anything unmapped is upserted under its
graph id. Positions: the twin is a synthetic grid, so lat/lon is mapped with an affine fit through three anchors
(Building 14, Lincoln HS, County General) - units move on the right streets relative to those landmarks.

Run:  cd Shresth/rescuegrid/bus && .venv/bin/uvicorn gateway:app --host 127.0.0.1 --port 8097
Env:  NEO4J_URI (shared graph), RESCUEGRID_BUS (:8096), RESCUEGRID_QA (:8095), RESCUEGRID_NETSIM (:5000),
      RESCUEGRID_VISION_FRAMES (backend/vision/frames), RESCUEGRID_TWIN_DIST (frontend/dist-live), RESCUEGRID_SCENARIO
"""
import asyncio, json, os, re, subprocess, sys, threading, time, uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)                       # <repo>/backend
KENIL = os.environ.get("RESCUEGRID_REASONING", os.path.join(ROOT, "reasoning"))
sys.path.insert(0, KENIL)
os.environ.setdefault("NEO4J_URI", "bolt://127.0.0.1:7688")
from rescuegrid.graph import GraphStore  # noqa: E402

BUS = os.environ.get("RESCUEGRID_BUS", "http://127.0.0.1:8096")
QA = os.environ.get("RESCUEGRID_QA", "http://127.0.0.1:8095")
NETSIM = os.environ.get("RESCUEGRID_NETSIM", "http://127.0.0.1:5000")
VISION = os.environ.get("RESCUEGRID_VISION", "http://127.0.0.1:8091")
VISION_FRAMES = os.environ.get("RESCUEGRID_VISION_FRAMES", os.path.join(ROOT, "vision", "frames"))
SAMPLES = os.path.join(ROOT, "vision", "samples")
DIST = os.environ.get("RESCUEGRID_TWIN_DIST", os.path.join(os.path.dirname(ROOT), "frontend", "dist-live"))
SCENARIO = os.environ.get("RESCUEGRID_SCENARIO", os.path.join(ROOT, "scenario", "scenario.json"))
ASR = os.environ.get("RESCUEGRID_ASR", "http://127.0.0.1:8090")
LLM = os.environ.get("RESCUEGRID_LLM", "http://127.0.0.1:8080/v1")
RADIO_LIB = os.path.join(ROOT, "radio", "library")
RADIO_UPLOADS = os.path.join(ROOT, "radio", "uploads")
UPLOADS = os.environ.get("RESCUEGRID_UPLOADS", os.path.join(ROOT, "videos", "uploads"))
VISION_PY = os.environ.get("RESCUEGRID_VISION_PY", os.path.join(ROOT, "vision", ".venv", "bin", "python"))
VISION_ADAPTER = os.environ.get("RESCUEGRID_VISION_ADAPTER", os.path.join(ROOT, "vision", "replay.py"))

# ---------------------------------------------------------------- id + label mapping (graph -> twin)
MAP = {
    "Building-14": "Building-14", "Building-7": "Building-7", "Building-22": "Building-22", "Building-3": "Building-3",
    "Road-Main": "Main-St-5", "Road-Bridge": "Oak-Bridge", "Road-River": "River-Rd-5", "Road-Oak": "Oak-Ave-6", "Road-3rd": "1st-St-5",
    "Facility-CountyGeneral": "Hospital-Valley", "Facility-LincolnHS": "Shelter-Lincoln",
    "Team-Rescue4": "Rescue-Team-4", "Team-Ambulance2": "Ambulance-2", "Team-Engine7": "Engine-7", "Team-Ambulance1": "Medic-5",
    "Sensor-Gas3": "Sensor-Gas-3", "Sensor-Seismic1": "Sensor-Seismic-1", "Sensor-Water2": "Sensor-Water-2",
    "Hazard-gas-Sensor-Gas3": "Hazard-Gas-1", "Hazard-seismic-Sensor-Seismic1": "Incident-EQ-1", "Hazard-water-Sensor-Water2": "Hazard-Water-1",
}
RMAP = {v: k for k, v in MAP.items()}
# where the twin's seed puts the mapped landmarks (src/data/seed.ts) - used for the hazard/incident overlays
TWIN_POS = {"Sensor-Gas-3": (11, -3), "Sensor-Seismic-1": (-6, -26), "Sensor-Water-2": (22, 29), "Building-14": (5, -5)}
LABEL = {"building": "Building", "road": "Road", "team": "Unit", "hazard": "Hazard", "sensor": "Sensor", "facility": "Hospital"}
# affine fit lat/lon -> twin x/z through three landmarks (graph seed lat/lon -> twin seed x/z)
ANCHORS = [((37.3352, -121.8893), (5, -5)), ((37.3320, -121.8950), (-15, 25)), ((37.3400, -121.8850), (35, -5))]


def _solve3(rows):
    """Gaussian elimination for a 3x3 system [[a,b,c,d],...] -> (x,y,z)."""
    m = [list(map(float, r)) for r in rows]
    for i in range(3):
        p = max(range(i, 3), key=lambda r: abs(m[r][i])); m[i], m[p] = m[p], m[i]
        for r in range(3):
            if r != i and m[i][i]:
                f = m[r][i] / m[i][i]; m[r] = [a - f * b for a, b in zip(m[r], m[i])]
    return [m[i][3] / m[i][i] for i in range(3)]


AX = _solve3([[lon, lat, 1, x] for (lat, lon), (x, z) in ANCHORS]); AZ = _solve3([[lon, lat, 1, z] for (lat, lon), (x, z) in ANCHORS])


def to_xz(lat, lon):
    if lat is None or lon is None: return None
    return (round(AX[0] * lon + AX[1] * lat + AX[2], 2), round(AZ[0] * lon + AZ[1] * lat + AZ[2], 2))


def twin_id(gid: str) -> str: return MAP.get(gid, gid)


def status_of(n: dict) -> str:
    """Graph status vocabulary -> the twin's five colours."""
    if n.get("conflict"): return "conflict"
    k, s = n.get("kind"), (n.get("status") or "").lower()
    if k == "building": return {"collapsed": "danger", "damaged": "warning"}.get(s, "normal")
    if k == "road": return {"blocked": "danger", "restricted": "warning"}.get(s, "normal")
    if k == "team": return "danger" if n.get("_at_risk") else ("warning" if s == "out_of_service" else "safe")
    if k == "hazard": return "danger" if s == "active" else "normal"
    if k == "sensor": return {"spike": "danger", "offline": "warning"}.get(s, "normal")
    if k == "facility": return {"full": "warning", "closed": "danger"}.get(s, "safe")
    return "normal"


def iso(v) -> Optional[str]:
    if v is None: return None
    if hasattr(v, "to_native"): v = v.to_native()
    if isinstance(v, datetime): return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(v)


# ---------------------------------------------------------------- graph snapshot
# a team is "at risk" (red) when it is inside an active LOCAL hazard zone (gas, water, fire); the seismic hazard is the
# incident itself (every unit is inside its radius from t=0), so it is shown as Incident-EQ-1, not as a per-unit risk
ENTITY_Q = """MATCH (n:Entity) OPTIONAL MATCH (n)-[r:NEAR {active:true}]->(h:Hazard {status:'active'}) WHERE h.hazard_type <> 'seismic'
WITH n, count(h) AS risk
RETURN n.id AS id, n.kind AS kind, n.name AS name, n.status AS status, n.status_since AS status_since, n.last_confirmed AS last_confirmed,
       n.source AS source, n.confidence AS confidence, n.raw_evidence_ref AS raw_evidence_ref, n.conflict AS conflict, n.conflict_claim AS conflict_claim,
       n.conflict_source AS conflict_source, n.conflict_confidence AS conflict_confidence, n.lat AS lat, n.lon AS lon, n.position_since AS position_since,
       n.callsign AS callsign, n.unit_type AS unit_type, n.level AS level, n.unit AS unit, n.radius_m AS radius_m, n.hazard_type AS hazard_type,
       n.reading AS reading, n.sensor_type AS sensor_type, n.beds_available AS beds_available, n.occupancy_est AS occupancy_est,
       n.auto_created AS auto_created, risk > 0 AS _at_risk"""
EDGE_Q = """MATCH (a:Entity)-[r:NEAR|AFFECTS|ASSIGNED_TO]->(b:Entity) WHERE coalesce(r.active, true) = true
RETURN a.id AS a, b.id AS b, type(r) AS t, r.since AS since, r.sources AS sources, r.source AS source, r.confidence AS confidence, r.distance_m AS distance_m"""


class Snapshot:
    def __init__(self, nodes: dict, edges: dict): self.nodes, self.edges = nodes, edges


def snapshot(g: GraphStore) -> Snapshot:
    nodes = {}
    for n in g.read_dicts(ENTITY_Q):
        tid = twin_id(n["id"]); props: dict[str, Any] = {"graph_id": n["id"], "name": n.get("name"), "graph_status": n.get("status"),
                                                        "source": n.get("source"), "confidence": n.get("confidence"), "evidence": n.get("raw_evidence_ref")}
        if n.get("conflict"):
            props["conflict"] = [f"{n.get('source')}: {n.get('status')} ({(n.get('confidence') or 0):.2f})",
                                 f"{n.get('conflict_source')}: {n.get('conflict_claim')} ({(n.get('conflict_confidence') or 0):.2f})"]
        k = n.get("kind")
        if k == "team":
            xz = to_xz(n.get("lat"), n.get("lon"))
            if xz: props["x"], props["z"] = xz
            props["callsign"] = n.get("name"); props["kind"] = {"fire": "engine"}.get(n.get("unit_type") or "", n.get("unit_type") or "rescue")
            props["state"] = (n.get("status") or "").upper().replace("_", " ")
        elif k == "hazard":
            sid = {"gas": "Sensor-Gas-3", "seismic": "Sensor-Seismic-1", "water": "Sensor-Water-2"}.get(n.get("hazard_type") or "")
            rest = n["id"].split("-", 2)[2] if n["id"].count("-") >= 2 else ""
            if rest.startswith("Sensor-") or not rest:
                if sid in TWIN_POS: props["x"], props["z"] = TWIN_POS[sid]
            else:
                props["at"] = twin_id(rest)                 # camera-seen hazard: sits on the entity it was seen at (twin places it)
                if twin_id(rest) in TWIN_POS: props["x"], props["z"] = TWIN_POS[twin_id(rest)]
            if n.get("description"): props["description"] = n.get("description")
            props["kind"] = n.get("hazard_type"); props["r"] = max(4.0, min(14.0, float(n.get("radius_m") or 50) / 6))
            if n.get("hazard_type") == "gas": props["ppm"] = n.get("level")
            if n.get("hazard_type") == "seismic": props["mag"] = n.get("level")
        elif k == "sensor":
            if n.get("reading") is not None: props["reading"] = f"{n.get('reading')} {n.get('unit') or ''}".strip()
        elif k == "facility":
            if n.get("beds_available") is not None: props["beds"] = n.get("beds_available")
        elif k == "building" and (n.get("status") == "collapsed"): props["collapsed"] = True
        elif k == "building": props["collapsed"] = False
        nodes[tid] = {"id": tid, "label": ("Incident" if tid == "Incident-EQ-1" else "Bridge" if tid.endswith("Bridge") else LABEL.get(k, "Incident")),
                      "status": status_of(n), "since": iso(n.get("status_since")), "lastConfirmed": iso(n.get("last_confirmed")), "props": props,
                      "_position_since": iso(n.get("position_since"))}
    edges = {}
    for e in g.read_dicts(EDGE_Q):
        a, b, t = twin_id(e["a"]), twin_id(e["b"]), e["t"]
        typ = "AT_RISK" if (t == "NEAR" and e["b"].startswith("Hazard-") and not e["b"].startswith("Hazard-seismic")) else t
        eid = f"e-{a}-{typ}-{b}".lower()
        edges[eid] = {"id": eid, "from": a, "to": b, "type": typ, "since": iso(e.get("since")),
                      "sources": [{"source": s, "timestamp": iso(e.get("since")), "confidence": e.get("confidence") or 0.9}
                                  for s in (e.get("sources") or ([e.get("source")] if e.get("source") else []))]}
    return Snapshot(nodes, edges)


def diff(prev: Optional[Snapshot], cur: Snapshot) -> list:
    """Graph snapshot diff -> the twin's GraphPatch list."""
    out = []
    for tid, n in cur.nodes.items():
        p = prev.nodes.get(tid) if prev else None
        if p is None:
            if prev is not None or tid not in RMAP:  # new to the graph mid-run, or an entity the twin's seed does not have
                out.append({"op": "upsertNode", "node": {"id": tid, "label": n["label"], "status": n["status"], "props": n["props"]}})
            continue
        if p["status"] != n["status"]: out.append({"op": "setStatus", "id": tid, "status": n["status"]})
        changed = {k: v for k, v in n["props"].items() if p["props"].get(k) != v}
        if changed: out.append({"op": "setProps", "id": tid, "props": changed})
    for eid, e in cur.edges.items():
        if not prev or eid not in prev.edges: out.append({"op": "addEdge", "edge": {"id": eid, "from": e["from"], "to": e["to"], "type": e["type"]}})
    if prev:
        for eid in prev.edges:
            if eid not in cur.edges: out.append({"op": "removeEdge", "id": eid})
    return out


AUDIT_Q = """MATCH (e:Event)-[:ABOUT]->(n:Entity) WHERE e.applied = true
RETURN n.id AS id, e.id AS event_id, e.source AS source, e.timestamp AS timestamp, e.confidence AS confidence, e.raw_evidence_ref AS ref, e.claim AS claim
ORDER BY e.timestamp"""


def seed_payload(cur: Snapshot) -> dict:
    """The graph as the twin's nodes/edges. Each node's `sources` is its audit trail (the applied Event nodes ABOUT it),
    so a browser that connects late still gets provenance click-through for every fact."""
    audit: dict[str, list] = {}
    try:
        for r in G.read_dicts(AUDIT_Q):
            audit.setdefault(twin_id(r["id"]), []).append({"eventId": r["event_id"], "source": r["source"], "timestamp": iso(r["timestamp"]),
                                                            "confidence": r["confidence"] or 0, "raw_evidence_ref": r["ref"], "claim": r["claim"]})
    except Exception as e: print("audit query failed:", e, file=sys.stderr)
    nodes = []
    for tid, n in cur.nodes.items():
        srcs = [{"source": "seed", "timestamp": n["since"], "confidence": 1.0}] + audit.get(tid, [])
        nodes.append({"id": tid, "label": n["label"], "status": n["status"], "since": n["since"], "lastConfirmed": n["lastConfirmed"],
                      "sources": srcs, "props": n["props"]})
    edges = [{k: v for k, v in e.items()} for e in cur.edges.values()]
    return {"type": "seed", "nodes": nodes, "edges": edges}


# ---------------------------------------------------------------- events (bus records -> twin timeline rows)
SEVERITY = {"collapsed": "danger", "blocked": "danger", "spike": "danger", "damaged": "warning", "restricted": "warning",
            "full": "warning", "closed": "danger", "out_of_service": "warning", "offline": "warning"}


def load_scenario_start() -> datetime:
    try:
        with open(SCENARIO) as f: return datetime.fromisoformat(json.load(f)["scenario_start"].replace("Z", "+00:00"))
    except Exception: return datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)


T0 = load_scenario_start()


def scen_t(ts: str) -> float:
    try: return round((datetime.fromisoformat(ts.replace("Z", "+00:00")) - T0).total_seconds(), 2)
    except Exception: return 0.0


def logged_event(rec: dict, name_of: dict) -> dict:
    ev, res = rec["event"], rec.get("result") or {}
    d = ev.get("details") or {}
    gid = str(res.get("entity_id") or ev.get("entity") or "?"); tid = twin_id(gid)   # str(): adversarial payloads may send a list
    claim = str(ev.get("claim", "")).lower()
    action = res.get("action")
    sev = "conflict" if action == "conflict" else SEVERITY.get(claim, "safe" if ev.get("source") in ("gps", "field_report") or claim in ("open", "on_scene", "near", "en_route") else "normal")
    if action in ("stale", "duplicate", "invalid", "ambiguous", "error") and sev == "danger": sev = "warning"
    pretty = name_of.get(gid) or ev.get("entity")
    title = {"position_update": f"{pretty} position fix", "near": f"{pretty} near {d.get('target', '')}".strip(), "on_scene": f"{pretty} on scene {d.get('target', '')}".strip()}.get(
        claim, f"{pretty} {claim.replace('_', ' ')}")
    if ev.get("source") == "sensor" and claim == "normal" and d.get("reading") is not None: title = f"{pretty} reading {d.get('reading')} {d.get('unit') or ''}".strip()
    if action == "conflict": title = f"Conflicting reports, {pretty}"
    elif action == "stale": title += " (stale, not applied)"
    elif action == "confirm": title += " (confirmed)"
    vlm = d.get("vlm") if isinstance(d.get("vlm"), dict) else {}
    detail = d.get("transcript") or d.get("text") or vlm.get("description")
    if not detail:
        detail = d.get("note")
        if d.get("reading") is not None: detail = f"{d.get('reading')} {d.get('unit', '')} · {detail or ''}".strip(" ·")
        if d.get("lat") is not None: detail = f"{d.get('lat')}, {d.get('lon')} · {detail or ''}".strip(" ·")
    return {"id": ev.get("event_id") or res.get("event_id") or rec.get("received_at"), "source": ev.get("source"), "timestamp": ev.get("timestamp"),
            "confidence": float(ev.get("confidence") or 0), "entity": tid, "claim": ev.get("claim"), "raw_evidence_ref": ev.get("raw_evidence_ref"),
            "t": scen_t(ev.get("timestamp", "")), "severity": sev, "title": title, "detail": detail,
            "fusion": {"action": action, "applied": res.get("applied"), "latency_ms": res.get("latency_ms"), "notes": (res.get("notes") or [])[:3], "entity_id": gid}}


# ---------------------------------------------------------------- state + broadcast
app = FastAPI(title="RescueGrid twin gateway")
G: Optional[GraphStore] = None
CLIENTS: set[WebSocket] = set()
STATE: dict[str, Any] = {"snap": None, "last_received": "", "events": [], "network": {"state": "normal", "kbps": None, "latencyMs": 140},
                         "suggestions": {}, "clock": {"t": 0.0, "wall": time.time(), "speed": 1.0, "first": None}, "names": {}, "errors": 0}
LOOP: Optional[asyncio.AbstractEventLoop] = None
QUEUE: "asyncio.Queue[dict]" = None  # type: ignore


def broadcast(msg: dict):
    if LOOP and QUEUE is not None: LOOP.call_soon_threadsafe(QUEUE.put_nowait, msg)


async def fanout():
    while True:
        msg = await QUEUE.get(); data = json.dumps(msg, default=str); dead = []
        for ws in list(CLIENTS):
            try: await ws.send_text(data)
            except Exception: dead.append(ws)
        for ws in dead: CLIENTS.discard(ws)


def clock_t() -> float:
    """Scenario seconds. Frozen at t until the first real input of an incident arrives (after a reset nothing runs)."""
    c = STATE["clock"]
    if c.get("first") is None and not STATE["events"]: return round(c["t"], 2)
    return round(c["t"] + (time.time() - c["wall"]) * c["speed"], 2)


def poller():
    """Background thread: new bus records -> event + graph diff; periodic graph diff; clock."""
    global G
    last_tick = 0.0
    while True:
        try:
            r = httpx.get(f"{BUS}/events/recent", params={"n": 500}, timeout=5).json()["events"]
            new = [x for x in r if x["received_at"] > STATE["last_received"]]
            for rec in new:
                STATE["last_received"] = rec["received_at"]          # advance first: one bad record must never stall the stream
                try: ev = logged_event(rec, STATE["names"])
                except Exception as e:
                    STATE["errors"] += 1; print("gateway: could not translate record:", repr(e)[:160], file=sys.stderr, flush=True); continue
                cur = snapshot(G); patches = diff(STATE["snap"], cur); STATE["snap"] = cur
                STATE["events"].append(ev)
                # scenario clock: follow the newest event; estimate replay speed from wall vs scenario deltas
                c = STATE["clock"]; wall = datetime.fromisoformat(rec["received_at"]).timestamp()
                if c["first"] is None: c["first"] = (ev["t"], wall)
                elif wall - c["first"][1] > 5:
                    c["speed"] = max(0.25, min(60.0, (ev["t"] - c["first"][0]) / (wall - c["first"][1])))
                if ev["t"] >= c["t"] - 1: c["t"], c["wall"] = ev["t"], time.time()
                broadcast({"type": "event", "event": ev, "patches": patches})
                maybe_suggest(cur, patches)
                try: react_to(rec, ev)
                except Exception as e: print("reactive:", repr(e)[:200], file=sys.stderr, flush=True)
            if time.time() - last_tick > 2.0:
                cur = snapshot(G); patches = diff(STATE["snap"], cur); STATE["snap"] = cur; last_tick = time.time()
                if patches: broadcast({"type": "patch", "patches": patches})
                broadcast({"type": "clock", "t": clock_t(), "speed": STATE["clock"]["speed"]})
        except Exception as e:
            STATE["errors"] += 1; print("gateway poll:", repr(e)[:200], file=sys.stderr, flush=True); time.sleep(1)
        time.sleep(0.25)


def maybe_suggest(cur: Snapshot, changed: list):
    """Rule 3 in practice: whenever a road's status changes, ASK THE GRAPH (Kenil's pre-baked reachability intent) whether
    the ambulance staged at the shelter can still reach the hospital. If a road on its route is blocked or uncertain,
    the graph's own alternative route is surfaced as a suggestion the IC must approve. No coordinates, no fixed detour:
    `routeIds` are the road ids from the Cypher path; the twin draws them. Never applied automatically."""
    roads = [p for p in changed if p["op"] == "setStatus" and cur.nodes.get(p["id"], {}).get("label") in ("Road", "Bridge")]
    if not roads or STATE["suggestions"].get("sg-amb2-reroute", {}).get("state") == "pending": return
    try:
        qa = httpx.post(f"{QA}/qa", json={"question": "Can Ambulance 2 still reach the hospital?", "mode": "fallback"}, timeout=30).json()
    except Exception as e:
        print("suggestion: Q&A unavailable:", e, file=sys.stderr); return
    ev = qa.get("evidence") or []
    routes = [r for r in ev if isinstance(r, dict) and r.get("route")]
    blocked = sorted({twin_id(x) for r in routes for x in (r.get("hard_blocked") or [])})
    uncertain = sorted({twin_id(x) for r in routes for x in (r.get("uncertain") or [])})
    if not (blocked or uncertain or qa.get("requires_commander_approval")): return   # every route clean: nothing to suggest
    best = routes[0] if routes else {}
    hl = qa.get("highlight") or {}
    sg = {"id": "sg-amb2-reroute", "kind": "reroute", "unit": twin_id(hl.get("id") if (hl.get("type") == "team") else "Team-Ambulance2"),
          "reason": ((qa.get("suggestion") or "") + " " + (qa.get("answer") or "")).strip()[:700],
          "routeIds": [twin_id(x) for x in best.get("route", [])], "routeNames": best.get("names") or [], "blocked": blocked, "uncertain": uncertain,
          "destination": twin_id(next((x for x in (hl.get("ids") or []) if x.startswith("Facility-")), "Facility-CountyGeneral")),
          "path": [], "state": "pending", "createdAt": iso(datetime.now(timezone.utc)), "cypher": qa.get("cypher"), "confidence": qa.get("confidence")}
    sg["unit"] = "Ambulance-2" if sg["unit"] == "Team-Ambulance2" else sg["unit"]
    STATE["suggestions"][sg["id"]] = sg; broadcast({"type": "suggestion", "suggestion": sg})


# ---------------------------------------------------------------- cameras (vision v2 tier-1 activity -> the twin's drones)
CAMERAS: dict[str, dict] = {}      # camera id -> {"unit": twin unit id, "watching": [twin ids], "type": drone|roadcam}
CAM_LAST: dict[str, float] = {}    # camera id -> wall time of the last frame we relayed
CAM_MSG: dict[str, dict] = {}      # camera id -> the last camera message (re-sent to a twin that connects later)


def load_cameras():
    """camera_entities.json as the vision service serves it: which seeded entities each camera looks at. Cameras become
    the twin's drone units in order (drone-1 -> Drone-1 ...); a road camera is a fixed unit. Nothing else is assumed."""
    try: cams = httpx.get(f"{VISION}/health", timeout=5).json().get("cameras") or {}
    except Exception as e: print("gateway: vision not reachable:", e, file=sys.stderr); cams = {}
    names = {}
    try: names = {n["id"]: n["name"] for n in G.all_entity_names()}
    except Exception: pass
    alias = {}
    for gid, nm in names.items(): alias[gid.lower()] = gid; alias[(nm or "").lower()] = gid
    # the twin flies drones only (no road cameras): drone-1..N, N = RESCUEGRID_DRONES (default 3), prefilled from the map
    CAMERAS.clear()
    for i in range(1, int(os.environ.get("RESCUEGRID_DRONES", "3")) + 1):
        cam = f"drone-{i}"; ents = cams.get(cam) or {}
        watching = []
        for k in ("building", "road", "bridge", "facility"):
            v = ents.get(k)
            if v: watching.append(twin_id(alias.get(str(v).lower(), str(v))))
        CAMERAS[cam] = {"unit": f"Drone-{i}", "watching": watching, "type": "drone", "camera": cam, "callsign": f"Drone {i}",
                        "entities": {k: v for k, v in ents.items() if k in ("building", "road", "bridge", "facility")}}
    print("gateway cameras:", CAMERAS, flush=True)


def camera_msg(e: dict, now: float) -> dict:
    cam = e["source"]["id"]
    info = CAMERAS.get(cam) or {"unit": None, "watching": [], "type": e["source"].get("type"), "camera": cam, "callsign": cam}
    return {"type": "camera", "camera": cam, "unit": info["unit"], "watching": info["watching"], "cameraType": info["type"], "callsign": info["callsign"],
            "ts": e.get("ts"), "t": scen_t(e.get("ts") or ""), "scene": e.get("scene"), "gate": (e.get("gate") or {}).get("decision"),
            "hazards": (e.get("gate") or {}).get("active"), "detections": e.get("detections"), "latency_ms": e.get("latency_ms"),
            "frame": f"/evidence/latest/{cam}.jpg?v={int(now * 1000)}", "frame_ref": e.get("frame_ref"), "motion": e.get("motion"), "image": e.get("image"),
            "position": (lambda xz: {"x": xz[0], "z": xz[1]} if xz else None)(to_xz(CAM_POS.get(cam, {}).get("lat"), CAM_POS.get(cam, {}).get("lon"))) if cam in CAM_POS else None,
            "over": CAM_POS.get(cam, {}).get("over")}


def camera_poller():
    """Relay tier-1 vision events (one per analysed frame) as {type:'camera'} messages, at most ~3/s per camera."""
    since = None
    try:  # the vision service remembers its last frames: start from its newest tier-1 result per camera
        for e in httpx.get(f"{VISION}/v1/vision/events", params={"tier": 1, "limit": 400}, timeout=5).json().get("events") or []:
            CAM_MSG[e["source"]["id"]] = camera_msg(e, time.time()); since = e.get("ts") or since
    except Exception as e: print("gateway: no vision history:", repr(e)[:120], file=sys.stderr)
    while True:
        try:
            r = httpx.get(f"{VISION}/v1/vision/events", params={"tier": 1, "limit": 100, **({"since": since} if since else {})}, timeout=5).json()
            evs = r.get("events") or []
            for e in evs:
                since = e.get("ts") or since
                cam = e["source"]["id"]
                if cam not in CAMERAS: continue   # road cameras and unknown feeds stay backend-only
                now = time.time()
                if now - CAM_LAST.get(cam, 0) < 0.33: continue
                CAM_LAST[cam] = now
                msg = camera_msg(e, now); CAM_MSG[cam] = msg; broadcast(msg)
        except Exception as e:
            print("gateway camera poll:", repr(e)[:160], file=sys.stderr, flush=True); time.sleep(2)
        time.sleep(0.3)


@app.on_event("startup")
async def start():
    global G, LOOP, QUEUE
    LOOP = asyncio.get_running_loop(); QUEUE = asyncio.Queue()
    G = GraphStore(); G.ping()
    STATE["names"] = {n["id"]: n["name"] for n in G.all_entity_names()}
    STATE["snap"] = snapshot(G)
    # replay what the bus already ingested this run so a browser that opens late sees the whole timeline
    try:
        for rec in httpx.get(f"{BUS}/events/recent", params={"n": 500}, timeout=5).json()["events"]:
            STATE["last_received"] = rec["received_at"]
            try: STATE["events"].append(logged_event(rec, STATE["names"]))
            except Exception as e: print("gateway: skipped record at start:", repr(e)[:160], file=sys.stderr)
        if STATE["events"]: STATE["clock"].update(t=max(e["t"] for e in STATE["events"]), wall=time.time(), first=(0.0, time.time()))
        else:
            latest = G.replay_now()   # bus history empty (restart) but the graph remembers where the incident clock is
            if latest: STATE["clock"].update(t=(latest - T0).total_seconds(), wall=time.time(), first=(0.0, time.time()))
    except Exception as e: print("gateway: bus not reachable at start:", e, file=sys.stderr)
    load_cameras()
    kill_orphan_adapters("gateway restarted; adapters from the previous gateway would keep feeding the incident")
    asyncio.create_task(fanout())
    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=camera_poller, daemon=True).start()
    print(f"twin gateway: graph {os.environ['NEO4J_URI']} bus {BUS} qa {QA} dist {DIST} ({'found' if os.path.isdir(DIST) else 'MISSING'})", flush=True)


# ---------------------------------------------------------------- routes
@app.websocket("/stream")
async def stream(ws: WebSocket):
    await ws.accept(); CLIENTS.add(ws)
    try:
        await ws.send_text(json.dumps(seed_payload(STATE["snap"]), default=str))
        for ev in STATE["events"]: await ws.send_text(json.dumps({"type": "event", "event": ev, "silent": True}, default=str))
        for sg in STATE["suggestions"].values(): await ws.send_text(json.dumps({"type": "suggestion", "suggestion": sg, "silent": True}))
        await ws.send_text(json.dumps({"type": "cameras", "cameras": list(CAMERAS.values())}))
        for m in CAM_MSG.values(): await ws.send_text(json.dumps({**m, "replayed": True}))
        for st in STREAMS.values(): await ws.send_text(json.dumps({"type": "stream", "stream": stream_status(st)}, default=str))
        for r in RADIO_LOG[-20:]: await ws.send_text(json.dumps({"type": "radio", "radio": r, "silent": True}, default=str))
        await ws.send_text(json.dumps({"type": "radioqueue", **radio_queue_status()}, default=str))
        await ws.send_text(json.dumps({"type": "network", "status": STATE["network"]}))
        await ws.send_text(json.dumps({"type": "clock", "t": clock_t(), "speed": STATE["clock"]["speed"]}))
        while True: await ws.receive_text()   # the twin sends nothing yet; keeps the socket open
    except WebSocketDisconnect: pass
    except Exception: pass
    finally: CLIENTS.discard(ws)


@app.post("/qa")
async def qa(body: dict):
    q = (body or {}).get("question"); mode = (body or {}).get("mode", "auto")
    if not q: raise HTTPException(400, "question required")
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=240) as c: r = (await c.post(f"{QA}/qa", json={"question": q, "mode": mode})).json()
    except Exception as e:
        return {"target": "Staging-A", "highlight": [], "answer": f"Q&A service unavailable: {e}", "cypher": None}
    hl = r.get("highlight") or {}; ids = [twin_id(i) for i in ([hl.get("id")] if hl.get("id") else []) + list(hl.get("ids") or []) if i]
    answer = r.get("answer", "")
    if r.get("suggestion"): answer += f"\n\nSUGGESTED - COMMANDER APPROVAL REQUIRED: {r['suggestion']}"
    for c in r.get("conflicts") or []:
        answer += f"\n\nCONFLICTING REPORTS on {c.get('name') or c.get('id')}: {c.get('source')} says {c.get('status')} ({c.get('confidence')}), {c.get('competing_source')} says {c.get('competing_claim')} ({c.get('competing_confidence')})."
    sig = {"target": ids[0] if ids else "Staging-A", "highlight": ids, "answer": answer, "cypher": r.get("cypher"),
           "mode": r.get("mode"), "intent": r.get("intent"), "confidence": r.get("confidence"), "requires_commander_approval": r.get("requires_commander_approval"),
           "provenance": r.get("provenance"), "as_of": r.get("as_of"), "latency_ms": round((time.perf_counter() - t0) * 1000)}
    return sig   # not broadcast: the asking twin flies itself; a second viewer would double-fly


@app.get("/questions")
async def questions():
    try: return httpx.get(f"{QA}/questions", timeout=5).json()
    except Exception as e: return {"questions": [], "error": str(e)}


@app.get("/netsim/status")
async def net_get(): return STATE["network"]


@app.post("/netsim/status")
async def net_set(body: dict):
    state = (body or {}).get("state")
    if state not in ("normal", "throttled", "disconnected"): raise HTTPException(400, "state must be normal|throttled|disconnected")
    kbps = (body or {}).get("kbps")
    lat = 140 if state == "normal" else None if state == "disconnected" else int(256 / max(64, kbps or 256) * 2600)
    STATE["network"] = {"state": state, "kbps": kbps, "latencyMs": lat}
    fwd = None
    try:
        async with httpx.AsyncClient(timeout=3) as c: fwd = (await c.post(f"{NETSIM}/network/state", json={"state": state})).json()
    except Exception as e: fwd = {"error": f"network simulator not reachable at {NETSIM}: {type(e).__name__}"}
    broadcast({"type": "network", "status": STATE["network"]})
    return {**STATE["network"], "simulator": fwd}


@app.post("/suggestions/{sid}")
async def decide(sid: str, body: dict):
    decision = (body or {}).get("decision")
    if decision not in ("approved", "dismissed"): raise HTTPException(400, "decision must be approved|dismissed")
    sg = STATE["suggestions"].get(sid)
    if not sg: raise HTTPException(404, "unknown suggestion")
    sg["state"] = decision; sg["decidedAt"] = iso(datetime.now(timezone.utc))
    # the IC's decision is a fact with provenance: it goes into the graph through the bus like every other report
    ev = {"source": "field_report", "timestamp": scen_now_iso(), "confidence": 1.0,
          "entity": "Ambulance 2", "claim": "en_route" if decision == "approved" else "available",
          "raw_evidence_ref": f"ui/ic-decision/{sid}", "event_id": f"ic-{sid}-{int(time.time())}",
          "details": {"text": f"IC {decision} suggested reroute {sid}: {sg['reason'][:200]}", "reporter": "IC (watch officer console)", "decision": decision, "suggestion_id": sid}}
    try:
        async with httpx.AsyncClient(timeout=30) as c: res = (await c.post(f"{BUS}/events", json=ev)).json()
    except Exception as e: res = {"error": str(e)}
    broadcast({"type": "suggestion", "suggestion": sg, "silent": True})
    if decision == "approved" and sg.get("kind") == "reroute" and REACTIVE["enabled"]:
        # the unit actually goes: simulated GPS fixes along the graph route (road lat/lon), then an arrival report + radio
        unit_gid = RMAP.get(sg["unit"], sg["unit"]); team = G.get_entity(unit_gid)
        ids = [RMAP.get(x, x) for x in sg.get("routeIds") or []] + [RMAP.get(sg.get("destination") or "", sg.get("destination") or "")]
        pts = []
        for x in ids:
            e = G.get_entity(x) if x else None
            if e and e.get("lat") is not None: pts.append((e["lat"], e["lon"]))
        if team and team.get("lat") is not None and pts:
            dest = G.get_entity(ids[-1]) or {}
            threading.Thread(target=sim_drive, args=(team, pts, "on_scene", dest.get("name"), f"Dispatch, {team['name']}, arrived {dest.get('name') or 'destination'}, patient transfer in progress.", VOICE_FOR.get(team["id"], "ryan")), daemon=True).start()
            threading.Thread(target=sim_radio, args=(f"Dispatch, {team['name']}, copy the reroute, proceeding via {', '.join(sg.get('routeNames') or [])}.", team["name"], VOICE_FOR.get(team["id"], "ryan")), daemon=True).start()
    return {"suggestion": sg, "recorded": res}


def scen_now_iso() -> str:
    """'Now' on the scenario clock for live inputs (radio, IC decisions): never older than the graph's newest evidence,
    otherwise the fusion agent would rightly call a fresh transmission stale."""
    from datetime import timedelta
    t = T0 + timedelta(seconds=clock_t())
    try:
        latest = G.replay_now()
        if latest and latest.replace(tzinfo=latest.tzinfo or timezone.utc) >= t:
            t = latest + timedelta(seconds=1)
            STATE["clock"].update(t=(t - T0).total_seconds(), wall=time.time())
    except Exception: pass
    return iso(t)


# ---------------------------------------------------------------- streams: an uploaded video played through the vision service
STREAMS: dict[str, dict] = {}


ADAPTER_NAMES = ("flight_stream.py", "replay.py")


def kill_orphan_adapters(reason: str) -> int:
    """Camera / scenario adapters are child processes of this gateway. If the gateway restarted, or a reset must stop
    everything, find any adapter still running (by its command line under /proc) and stop it, so nothing keeps
    feeding frames or events into a fresh incident."""
    n = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid(): continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f: argv = f.read().split(b"\0")
        except Exception: continue
        if len(argv) > 1 and argv[0].decode(errors="ignore").endswith("python") and any(argv[1].decode(errors="ignore").endswith(a) for a in ADAPTER_NAMES):
            try: os.kill(int(pid), 15); n += 1; print(f"gateway: stopped adapter pid {pid} ({reason}): {argv[1].decode(errors='ignore')}", flush=True)
            except Exception: pass
        elif len(argv) > 1 and argv[0].decode(errors="ignore").endswith("python3") and argv[1].decode(errors="ignore").endswith("scenario/replay.py"):
            try: os.kill(int(pid), 15); n += 1; print(f"gateway: stopped scenario replay pid {pid} ({reason})", flush=True)
            except Exception: pass
    return n


def stream_status(st: dict) -> dict:
    p = st["proc"]; rc = p.poll()
    frames = 0; done_line = None
    try:
        for line in open(st["log"], errors="replace"):
            if "tier1=" in line: frames += 1
            elif line.startswith("sent ") or "CLAIM" in line or "Error" in line or "error" in line: done_line = line.strip()[:200]
    except Exception: pass
    return {k: v for k, v in st.items() if k != "proc"} | {"state": "running" if rc is None else ("finished" if rc == 0 else f"failed ({rc})"),
                                                            "frames_sent": frames, "last": done_line,
                                                            "video": f"/streams/{st['id']}/video" if st.get("path") else None}


def spawn_stream(kind: str, cmd: list, cwd: str, meta: dict) -> dict:
    sid = f"{kind}-{uuid.uuid4().hex[:6]}"
    log_path = os.path.join(UPLOADS, f"{sid}.log"); os.makedirs(UPLOADS, exist_ok=True)
    log = open(log_path, "w")
    p = subprocess.Popen(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, text=True)
    STREAMS[sid] = {"id": sid, "kind": kind, "started": iso(datetime.now(timezone.utc)), "pid": p.pid, "proc": p, "log": log_path, "cmd": " ".join(cmd)[:300], **meta}
    st = stream_status(STREAMS[sid]); broadcast({"type": "stream", "stream": st}); return st


FLIGHT_ADAPTER = os.environ.get("RESCUEGRID_FLIGHT_ADAPTER", os.path.join(ROOT, "vision", "flight_stream.py"))
CAM_POS: dict[str, dict] = {}   # camera -> latest telemetry (lat, lon, over, entities)


def nearest_of_kind(lat: float, lon: float, kind: str, radius_m: float = 320.0):
    rows = G.read_dicts("MATCH (n:Entity {kind:$k}) WHERE n.lat IS NOT NULL RETURN n.id AS id, n.name AS name, n.lat AS lat, n.lon AS lon", k=kind)
    best = min(rows, key=lambda r: haversine_m(lat, lon, r["lat"], r["lon"]), default=None)
    return best if best and haversine_m(lat, lon, best["lat"], best["lon"]) <= radius_m else None


def build_flight_plan(names: list[str]) -> dict:
    """Ordered entity names -> waypoints with lat/lon and, for each, the building / road / bridge the camera can
    see from there (nearest of each kind within 320 m). Unknown names are skipped."""
    alias = {}
    for n in G.all_entity_names():
        alias[n["id"].lower()] = n["id"]; alias[(n.get("name") or "").lower()] = n["id"]
    wps = []
    for nm in names:
        gid = alias.get(nm.strip().lower())
        e = G.get_entity(gid) if gid else None
        if not e or e.get("lat") is None: continue
        ents = {}
        b = nearest_of_kind(e["lat"], e["lon"], "building"); r = nearest_of_kind(e["lat"], e["lon"], "road")
        if b: ents["building"] = b["id"]
        if r: ents["road"] = r["name"]
        if e["id"] == "Road-Bridge" or (r and r["id"] == "Road-Bridge"): ents["bridge"] = "Bridge Street"
        if e.get("kind") == "building": ents["building"] = e["id"]
        if e.get("kind") == "road": ents["road"] = e.get("name")
        wps.append({"id": e["id"], "name": e.get("name"), "kind": e.get("kind"), "lat": e["lat"], "lon": e["lon"], "entities": ents})
    return {"waypoints": wps, "speed_mps": 12.0}


@app.post("/cameras/{camera}/telemetry")
async def camera_telemetry(camera: str, body: dict):
    """Where the drone is right now (from the flight adapter; a live drone would send its GPS)."""
    CAM_POS[camera] = {**body, "at": time.time()}
    xz = to_xz(body.get("lat"), body.get("lon"))
    if camera in CAMERAS:
        CAMERAS[camera]["over"] = body.get("over"); CAMERAS[camera]["entities"] = body.get("entities") or CAMERAS[camera].get("entities")
        CAMERAS[camera]["watching"] = [twin_id(v) for v in (body.get("entities") or {}).values()] or CAMERAS[camera]["watching"]
    broadcast({"type": "telemetry", "camera": camera, "unit": CAMERAS.get(camera, {}).get("unit"), "x": xz[0] if xz else None, "z": xz[1] if xz else None,
               "over": body.get("over"), "watching": [twin_id(v) for v in (body.get("entities") or {}).values()], "t": body.get("t")})
    return {"ok": True}


@app.post("/streams")
async def start_stream(file: UploadFile = File(...), camera: str = Form("drone-1"), building: str = Form(""), road: str = Form(""), bridge: str = Form(""),
                       plan: str = Form(""), fps: float = Form(2.0), speed: float = Form(1.0), loop: bool = Form(False)):
    """Upload a video and play it into the vision service as `camera`, one frame per request at `fps`, exactly like a live
    feed. `building` / `road` say what the camera is looking at (seeded names; default = camera_entities.json). Claims are
    whatever the detector confirms; nothing about the file is assumed."""
    os.makedirs(UPLOADS, exist_ok=True)
    name = f"{int(time.time())}_{re.sub(r'[^A-Za-z0-9._-]+', '_', file.filename or 'stream.mp4')}"
    path = os.path.join(UPLOADS, name); size = 0
    with open(path, "wb") as f:
        while chunk := await file.read(1 << 20): f.write(chunk); size += len(chunk)
    if size == 0: raise HTTPException(400, "empty upload")
    ents = {k: v for k, v in (("building", building.strip()), ("road", road.strip()), ("bridge", bridge.strip())) if v}
    for st in STREAMS.values():   # one stream per drone: a new upload for the same camera replaces the running one
        if st.get("camera") == camera and st["proc"].poll() is None: st["proc"].terminate()
    REACTIVE["armed"] = True
    names = [x for x in re.split(r"[,;\n]+", plan) if x.strip()]
    fp = build_flight_plan(names) if names else {"waypoints": []}
    if fp["waypoints"]:
        # the drone flies the plan across the clip; each frame is tagged with what is nearest to it at that moment
        plan_path = path + ".plan.json"; json.dump(fp, open(plan_path, "w"))
        cmd = [VISION_PY, FLIGHT_ADAPTER, path, "--url", VISION, "--source-id", camera, "--plan", plan_path, "--ts-start", scen_now_iso(),
               "--speed", str(speed), "--fps", str(fps), "--telemetry", f"http://127.0.0.1:{os.environ.get('RESCUEGRID_GATEWAY_PORT', '8097')}"]
        if loop: cmd.append("--loop")
        return spawn_stream("stream", cmd, os.path.dirname(FLIGHT_ADAPTER), {"camera": camera, "file": file.filename, "path": path, "bytes": size,
                            "plan": [w["name"] for w in fp["waypoints"]], "fps": fps, "speed": speed, "loop": loop})
    cmd = [VISION_PY, VISION_ADAPTER, path, "--url", VISION, "--source-id", camera, "--source-type", "roadcam" if camera.startswith("roadcam") else "drone",
           "--ts-start", scen_now_iso(), "--speed", str(speed), "--fps", str(fps)]
    if ents: cmd += ["--entities", json.dumps(ents)]
    if loop: cmd.append("--loop")
    return spawn_stream("stream", cmd, os.path.dirname(VISION_ADAPTER), {"camera": camera, "file": file.filename, "path": path, "bytes": size, "entities": ents, "fps": fps, "speed": speed, "loop": loop})


@app.post("/scenario/start")
async def scenario_start(body: dict | None = None):
    """Run the full rehearsal (all five feeds, clips through vision) on one clock, from the gateway. {speed: 1}."""
    speed = float((body or {}).get("speed") or 1.0)
    if any(st["kind"] == "scenario" and st["proc"].poll() is None for st in STREAMS.values()): raise HTTPException(409, "a scenario replay is already running")
    try: httpx.post(f"{VISION}/v1/vision/reset", timeout=5)
    except Exception: pass
    REACTIVE["armed"] = True
    cmd = ["python3", os.path.join(ROOT, "scenario", "replay.py"), "--speed", str(speed)]
    return spawn_stream("scenario", cmd, os.path.join(ROOT, "scenario"), {"file": "scenario.json", "speed": speed})


@app.get("/streams")
async def list_streams(): return {"streams": [stream_status(st) for st in STREAMS.values()]}


@app.get("/streams/{sid}/video")
async def stream_video(sid: str):
    """The uploaded file itself, so the twin can loop it in the camera panel under the live detection boxes."""
    st = STREAMS.get(sid)
    if not st or not st.get("path") or not os.path.isfile(st["path"]): raise HTTPException(404, "no video for this stream")
    return FileResponse(st["path"], media_type="video/mp4", headers={"Accept-Ranges": "bytes"})


@app.delete("/streams/{sid}")
async def stop_stream(sid: str):
    st = STREAMS.get(sid)
    if not st: raise HTTPException(404, "unknown stream")
    if st["proc"].poll() is None: st["proc"].terminate()
    out = stream_status(st); broadcast({"type": "stream", "stream": out}); return out


CLIENT_ERRORS: list[dict] = []


@app.post("/client-error")
async def client_error(body: dict):
    """The twin posts uncaught browser errors here (main.tsx), so a crash on someone's laptop can be read on the Nano."""
    rec = {"at": now_iso_wall(), **{k: str(v)[:2000] for k, v in (body or {}).items()}}
    CLIENT_ERRORS.append(rec); del CLIENT_ERRORS[:-50]
    print(f"CLIENT ERROR {rec.get('kind','')}: {rec.get('message','')[:300]} @ {rec.get('url','')} :: {rec.get('stack','')[:400]}", file=sys.stderr, flush=True)
    return {"ok": True}


@app.get("/client-errors")
async def client_errors(): return {"errors": CLIENT_ERRORS}


@app.get("/entities")
async def entities():
    """Seeded entity names by kind, for the upload form (what a camera can be pointed at)."""
    out: dict[str, list] = {}
    for n in G.all_entity_names(): out.setdefault(n.get("kind") or "other", []).append({"id": n["id"], "name": n.get("name")})
    return out


@app.post("/reset")
async def reset_incident():
    """Wipe the shared graph back to the static seed, forget the bus counters and this gateway's history, stop running
    streams, and re-seed every connected twin. The graph is the source of truth; everything else follows it."""
    STATE["epoch"] += 1; REACTIVE["armed"] = False
    for st in STREAMS.values():
        if st["proc"].poll() is None: st["proc"].terminate()
    kill_orphan_adapters("reset")
    for it in RADIO_QUEUE: it["cancelled"] = True
    try: httpx.post(f"{VISION}/v1/vision/reset", timeout=5)
    except Exception: pass
    for _ in range(40):   # a Qwen assessment already running would post its claim into the fresh graph: let it drain first (<= 8 s)
        try:
            st = httpx.get(f"{VISION}/v1/vision/stats", timeout=3).json()
            if st["gate_triggers"] <= st["vlm_calls"] + st["vlm_merged"]: break
        except Exception: break
        await asyncio.sleep(0.2)
    G.reset()
    try: httpx.post(f"{BUS}/reset", timeout=10)
    except Exception as e: print("bus reset failed:", e, file=sys.stderr)
    STATE["events"].clear(); STATE["suggestions"].clear(); STATE["last_received"] = now_iso_wall(); CAM_MSG.clear()
    REACTIVE["fired"].clear(); REACTIVE["log"].clear(); RADIO_LOG.clear()
    for it in RADIO_QUEUE: it["cancelled"] = True
    RADIO_QUEUE.clear()
    STATE["clock"].update(t=0.0, wall=time.time(), speed=1.0, first=None)
    STATE["snap"] = snapshot(G)
    broadcast({"type": "reset"}); broadcast(seed_payload(STATE["snap"])); broadcast({"type": "cameras", "cameras": list(CAMERAS.values())})
    broadcast({"type": "clock", "t": 0.0, "speed": 1.0})
    return {"reset": True, "counts": G.counts()}


def now_iso_wall() -> str: return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------- reactive incident simulation
# The simulated feeds (sensors, GPS, radio voices, field reports: the master plan's replay adapters) no longer run on a
# fixed clock. They REACT to what the real pipeline detects: a collapse seen by a drone makes the gas sensor next to that
# building spike, a rescue unit roll (GPS fixes along the way), the crew call it in on the radio (spoken by Piper, heard
# by faster-whisper, turned into a claim by the LLM). Everything simulated is marked details.simulated=true and
# raw_evidence_ref sim/... ; vision, ASR, fusion and the graph stay real. Timings are the delays a real incident would have.
REACTIVE = {"enabled": True, "fired": set(), "log": [], "armed": False}   # armed by START INCIDENT / scenario / an upload; a reset disarms
STATE["epoch"] = 0                                                              # bumped by every reset; in-flight work from before is dropped
RADIO_PY = os.path.join(ROOT, "radio", ".venv", "bin", "python"); RADIO_SAY = os.path.join(ROOT, "radio", "say.py")
UNIT_SPEED_MPS = {"rescue": 16.0, "ambulance": 22.0, "fire": 16.0, "police": 24.0}   # m/s with lights and sirens (58-86 km/h)
VOICE_FOR = {"Team-Rescue4": "lessac", "Team-Engine7": "ryan", "Team-Ambulance2": "ryan", "Team-Ambulance1": "lessac", "Dispatch": "lessac"}


def sim_post(ev: dict, note: str) -> dict:
    ev.setdefault("details", {})["simulated"] = True
    ev["details"]["sim_note"] = note
    try: r = httpx.post(f"{BUS}/events", json=ev, timeout=60).json()
    except Exception as e: r = {"error": str(e)}
    REACTIVE["log"].append({"at": now_iso_wall(), "note": note, "event": {k: ev[k] for k in ("source", "entity", "claim")}, "result": r.get("action")})
    return r


def sim_radio(text: str, speaker: str, voice: str = "ryan"):
    """Speak a line with Piper, then run it through the REAL radio path (ASR -> LLM -> bus), like any transmission."""
    os.makedirs(RADIO_UPLOADS, exist_ok=True)
    name = f"sim_{int(time.time() * 1000)}_{re.sub(r'[^A-Za-z0-9]+', '_', speaker)[:16]}.wav"
    path = os.path.join(RADIO_UPLOADS, name)
    try:
        subprocess.run([RADIO_PY, RADIO_SAY, path, voice, text], check=True, capture_output=True, timeout=60)
        process_radio(path, name, speaker, "ch3", note="simulated crew voice (Piper); transcript and claim are real")
    except Exception as e:
        print("sim_radio failed:", repr(e)[:200], file=sys.stderr, flush=True)


def geo_step(lat1, lon1, lat2, lon2, frac):
    return lat1 + (lat2 - lat1) * frac, lon1 + (lon2 - lon1) * frac


def haversine_m(lat1, lon1, lat2, lon2):
    import math
    R = 6371000.0; p1, p2 = math.radians(lat1), math.radians(lat2); dp = p2 - p1; dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def sim_drive(team: dict, waypoints: list, arrive_claim: str | None, arrive_target: str | None, arrive_text: str | None, voice: str, fix_s: float = 1.5):
    """Simulated GPS: fixes every fix_s seconds along the waypoints (graph lat/lon of the roads), at the unit's speed.
    Each fix is a normal gps event; the fusion agent derives NEAR edges from it like from a real tracker."""
    speed = UNIT_SPEED_MPS.get(team.get("unit_type") or "", 10.0)
    lat, lon = team["lat"], team["lon"]
    for wlat, wlon in waypoints:
        dist = haversine_m(lat, lon, wlat, wlon); steps = max(1, int(dist / (speed * fix_s)))
        for i in range(1, steps + 1):
            if not REACTIVE["enabled"]: return
            lat2, lon2 = geo_step(lat, lon, wlat, wlon, i / steps)
            sim_post({"source": "gps", "timestamp": scen_now_iso(), "confidence": 0.95, "entity": team["name"], "claim": "position_update",
                      "raw_evidence_ref": f"sim/gps/{team['id']}/{int(time.time())}", "details": {"lat": round(lat2, 6), "lon": round(lon2, 6), "speed_mps": speed}},
                     f"{team['name']} moving ({dist:.0f} m leg)")
            time.sleep(fix_s)
        lat, lon = wlat, wlon
    # arrival is reported by voice only (same source as the en-route call, so the fusion policy sees a progression, not
    # two sources disagreeing); the LLM turns "on scene" into the on_scene claim
    if arrive_text: sim_radio(arrive_text, team["name"], voice)


def nearest_available_team(lat, lon, unit_type="rescue"):
    rows = G.read_dicts("MATCH (t:Team) WHERE t.status IN ['available','en_route'] AND t.lat IS NOT NULL RETURN t.id AS id, t.name AS name, t.unit_type AS unit_type, t.lat AS lat, t.lon AS lon, t.status AS status")
    rows = [r for r in rows if r["unit_type"] == unit_type] or rows
    return min(rows, key=lambda r: haversine_m(lat, lon, r["lat"], r["lon"]), default=None)


def react_to(rec: dict, ev: dict):
    """Fire consequences for an applied event, once per (entity, claim) per incident."""
    if not REACTIVE["enabled"] or not REACTIVE["armed"]: return
    res = rec.get("result") or {}; src = rec["event"]
    d0 = src.get("details") or {}
    is_quake = src.get("source") == "sensor" and str(src.get("claim", "")).lower() == "spike" and (d0.get("sensor_type") == "seismic" or "Seismic" in str(src.get("entity", "")))
    if d0.get("simulated") and not is_quake: return                          # never chain off our own simulated feeds (the quake itself is allowed: its consequences are the point)
    if res.get("action") not in ("override", "confirm", "conflict", "near", "create"): return
    gid = res.get("entity_id") or ""; claim = str(src.get("claim", "")).lower(); key = (gid, claim)
    if key in REACTIVE["fired"]: return
    ent = G.get_entity(gid) if gid else None
    if not ent: return
    def go(fn, *a): threading.Thread(target=fn, args=a, daemon=True).start()

    if ent.get("kind") == "building" and claim in ("collapsed", "damaged"):
        REACTIVE["fired"].add(key)
        def chain():
            name = ent.get("name") or gid; lat, lon = ent.get("lat"), ent.get("lon")
            # 1. dispatch acknowledges on the radio (spoken -> ASR -> LLM -> graph), ~5 s later
            time.sleep(5); sim_radio(f"All units, dispatch. Drone shows {name} {claim}. Nearest rescue unit respond to {name}, heavy rescue assignment.", "Dispatch", VOICE_FOR["Dispatch"])
            # 2. the nearest rescue unit rolls: radio + GPS track + on-scene report
            team = nearest_available_team(lat, lon, "rescue") if lat is not None else None
            if team:
                time.sleep(4); sim_radio(f"Dispatch, {team['name']}, en route to {name}, E T A two minutes.", team["name"], VOICE_FOR.get(team["id"], "joe"))
                go(sim_drive, team, [(lat, lon)], "on_scene", name, f"{team['name']} on scene {name}, beginning primary search.", VOICE_FOR.get(team["id"], "joe"))
            # 3. the gas main next to a collapsed building lets go ~25 s after the collapse: the sensor that MONITORS it spikes
            if claim == "collapsed":
                time.sleep(25)
                sens = G.read_dicts("MATCH (s:Sensor)-[:MONITORS]->(b:Entity {id:$id}) WHERE s.sensor_type='gas' RETURN s.id AS id, s.name AS name, s.threshold AS threshold LIMIT 1", id=gid)
                if sens:
                    sim_post({"source": "sensor", "timestamp": scen_now_iso(), "confidence": 0.97, "entity": sens[0]["id"], "claim": "spike",
                              "raw_evidence_ref": f"sim/sensors/gas/{sens[0]['id']}/{int(time.time())}", "details": {"reading": round((sens[0].get('threshold') or 25) * 1.8, 1), "unit": "ppm", "sensor_type": "gas"}},
                             f"gas sensor next to {name} spikes after the collapse")
                    time.sleep(6)
                    if team: sim_radio(f"Dispatch, {team['name']}, strong gas odor at the north entrance of {name}, requesting utility shutoff.", team["name"], VOICE_FOR.get(team["id"], "joe"))
        go(chain)

    elif ent.get("kind") == "road" and claim in ("blocked", "restricted"):
        REACTIVE["fired"].add(key)
        def chain():
            time.sleep(4)
            sim_radio(f"All units, dispatch. {ent.get('name') or gid} is {claim}, {'avoid it' if claim == 'blocked' else 'expect delays'}. Units heading to County General use an alternate route.", "Dispatch", VOICE_FOR["Dispatch"])
        go(chain)

    elif is_quake:
        REACTIVE["fired"].add(key)
        def chain():
            # structural damage across the district, scaled to the magnitude: 911 callers and the triage team report the
            # other seeded buildings over the next minute (simulated reports; the drone's own claims stay real)
            mag = float(d0.get("reading") or 5.0)
            bl = G.read_dicts("MATCH (b:Building) WHERE b.lat IS NOT NULL RETURN b.id AS id, b.name AS name, b.lat AS lat, b.lon AS lon, b.floors AS floors, b.status AS status ORDER BY b.id")
            epi = G.read_dicts("MATCH (s:Sensor {sensor_type:'seismic'}) RETURN s.lat AS lat, s.lon AS lon LIMIT 1")
            elat, elon = (epi[0]["lat"], epi[0]["lon"]) if epi else (None, None)
            rng = __import__("random").Random(int(mag * 100))
            for b in bl:
                if b["id"] == "Building-14": continue                        # the drone reports Building 14 itself
                dist = haversine_m(elat, elon, b["lat"], b["lon"]) if elat is not None else 500
                sev = (mag - 4.5) * 1.2 - dist / 900 + (b.get("floors") or 2) * 0.12 + rng.uniform(-0.3, 0.3)
                claim = "collapsed" if sev > 1.05 else "damaged" if sev > 0.35 else None
                if not claim: continue
                delay = 8 + rng.uniform(0, 40)
                def report(b=b, claim=claim, delay=delay):
                    time.sleep(delay)
                    if not REACTIVE["enabled"]: return
                    txt = (f"Caller reports {b['name']} has collapsed, people may be inside." if claim == "collapsed"
                           else f"Structural triage: {b['name']} has facade cracks and displaced floors, residents evacuating.")
                    sim_post({"source": "field_report", "timestamp": scen_now_iso(), "confidence": 0.75 if claim == "collapsed" else 0.7, "entity": b["id"], "claim": claim,
                              "raw_evidence_ref": f"sim/fieldreport/911/{b['id']}/{int(time.time())}", "details": {"text": txt, "reporter": "911 caller" if claim == "collapsed" else "Structural triage team", "magnitude": mag}},
                             f"quake damage report: {b['name']} {claim}")
                go(report)
        go(chain)

    elif ent.get("kind") == "hazard" and claim == "spike" or (ent.get("kind") == "sensor" and claim == "spike"):
        REACTIVE["fired"].add(key)
        def chain():
            time.sleep(3)
            rows = G.read_dicts("MATCH (t:Team)-[n:NEAR {active:true}]->(h:Hazard {status:'active'}) WHERE h.hazard_type='gas' RETURN t.id AS id, t.name AS name, h.name AS hazard, n.distance_m AS d LIMIT 3")
            for r in rows:
                sg = {"id": f"sg-withdraw-{r['id']}".lower(), "kind": "withdraw", "unit": twin_id(r["id"]), "path": [], "routeIds": [],
                      "reason": f"{r['name']} is {r['d']:.0f} m inside the {r['hazard']} zone. Suggest withdrawing to a safe distance until utility shutoff is confirmed. Commander approval required.",
                      "state": "pending", "createdAt": iso(datetime.now(timezone.utc))}
                if sg["id"] not in STATE["suggestions"]:
                    STATE["suggestions"][sg["id"]] = sg; broadcast({"type": "suggestion", "suggestion": sg})
        go(chain)


SEISMIC_PREROLL = [(0, 0.02), (1, 0.05), (2, 0.14), (3, 0.31), (4, 0.9)]   # (second, magnitude) before the quake at t=5


def seismic_incident(mag: float = 5.8):
    """The earthquake as the sensor would report it: five seconds of live seismic readings, then the spike.
    Simulated sensor telemetry (marked as such); the fusion agent writes the readings to the graph like any sensor."""
    sens = G.read_dicts("MATCH (s:Sensor {sensor_type:'seismic'}) RETURN s.id AS id LIMIT 1")
    if not sens: return
    sid = sens[0]["id"]; t_start = time.time()
    for sec, r in SEISMIC_PREROLL:
        while time.time() - t_start < sec: time.sleep(0.05)
        sim_post({"source": "sensor", "timestamp": scen_now_iso(), "confidence": 0.99, "entity": sid, "claim": "normal",
                  "raw_evidence_ref": f"sim/sensors/seismic/{sid}/{int(time.time())}", "details": {"reading": r, "unit": "magnitude", "sensor_type": "seismic"}}, f"seismic reading {r}")
    while time.time() - t_start < 5: time.sleep(0.05)
    sim_post({"source": "sensor", "timestamp": scen_now_iso(), "confidence": 0.99, "entity": sid, "claim": "spike",
              "raw_evidence_ref": f"sim/sensors/seismic/{sid}/{int(time.time())}", "details": {"reading": mag, "unit": "magnitude", "sensor_type": "seismic", "note": "earthquake"}},
             f"earthquake M{mag} after 5 s of readings")


@app.post("/incident/start")
async def incident_start(body: dict | None = None):
    """START INCIDENT: the seismic sensor starts measuring now; the quake fires 5 s later. {magnitude?: 5.8}"""
    mag = float((body or {}).get("magnitude") or 5.8)
    REACTIVE["armed"] = True
    threading.Thread(target=seismic_incident, args=(mag,), daemon=True).start()
    return {"started": True, "quake_in_s": 5, "magnitude": mag}


@app.post("/reactive")
async def reactive_set(body: dict | None = None):
    on = bool((body or {}).get("enabled", True)); REACTIVE["enabled"] = on
    broadcast({"type": "reactive", "enabled": on}); return {"enabled": on}


@app.get("/reactive")
async def reactive_get(): return {"enabled": REACTIVE["enabled"], "fired": sorted(REACTIVE["fired"]), "log": REACTIVE["log"][-40:]}


# ---------------------------------------------------------------- radio: audio -> ASR (faster-whisper) -> LLM claim -> bus
RADIO_LOG: list[dict] = []
CLAIM_SCHEMA = {"type": "object", "properties": {
    "entity": {"type": ["string", "null"]},
    "claim": {"type": ["string", "null"], "enum": ["intact", "damaged", "collapsed", "open", "restricted", "blocked", "available", "en_route", "on_scene", "out_of_service", "full", "closed", None]},
    "confidence": {"type": "number"}, "summary": {"type": "string"}, "people_trapped": {"type": ["integer", "null"]}},
    "required": ["entity", "claim", "confidence", "summary", "people_trapped"]}


def radio_claim(transcript: str, speaker: str) -> dict:
    """One radio transmission -> ONE contract claim, by the local LLM with a JSON schema (entity names from the seed)."""
    names = [f"{n.get('name')} ({n.get('kind')})" for n in G.all_entity_names() if n.get("kind") != "hazard"]
    sysmsg = ("You turn one radio transmission from a county EOC channel into ONE structured claim about ONE entity from this list, "
              "using the exact name: " + "; ".join(names) + ". Claims by kind: building intact|damaged|collapsed; road open|restricted|blocked; "
              "team available|en_route|on_scene|out_of_service; facility open|full|closed. A unit reporting its own movement is a claim about "
              "that unit: 'en route', 'on route', 'responding', 'heading to', 'ETA' mean en_route; 'on scene', 'arrived', 'at' mean on_scene. "
              "'Holding at', 'staged', 'awaiting' mean available. A unit's movement, position or holding is ALWAYS a claim about the UNIT "
              "(the team), never about the place it is at (a shelter or hospital is not on_scene). Only the STATUS of a building, road, unit or facility is a claim: "
              "reports of a gas odor, smoke smell, people trapped, requests (utility shutoff, more units), ETAs or questions are NOT status "
              "claims: then entity and claim are null (still fill summary and people_trapped). If the transmission makes no claim about a listed entity, entity and claim are null. "
              "confidence 0.5-0.95 by how explicit and first-hand the speaker is (second-hand or old information is lower). "
              "people_trapped = number of people reported trapped, else null. summary = one EOC-style sentence. "
              "Examples: 'Ambulance 2 holding at Lincoln staging awaiting a route' -> entity 'Ambulance 2', claim 'available'. "
              "'Rescue 4 en route to Building 14, ETA two minutes' -> 'Rescue Team 4', 'en_route'. "
              "'Rescue 4 on scene Building 14, strong gas odor' -> 'Rescue Team 4', 'on_scene'. "
              "'Main Street is blocked, debris across all lanes' -> 'Main Street', 'blocked'. "
              "'We can hear voices under the debris at Building 14, two people trapped' -> entity null, claim null, people_trapped 2. "
              "'Requesting a second engine' -> entity null, claim null.")
    body = {"model": "llm", "temperature": 0, "max_tokens": 160, "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": sysmsg}, {"role": "user", "content": f'Transcript: "{transcript}" Speaker: {speaker or "unknown"}'}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "claim", "schema": CLAIM_SCHEMA}}}
    r = httpx.post(f"{LLM}/chat/completions", json=body, timeout=60).json()
    out = json.loads(r["choices"][0]["message"]["content"])
    out["llm_ms"] = None; return out


def process_radio(path: str, file_name: str, speaker: str, channel: str, note: str = "") -> dict:
    epoch = STATE["epoch"]
    t0 = time.perf_counter()
    with open(path, "rb") as f:
        asr = httpx.post(f"{ASR}/v1/audio/transcriptions", files={"file": (file_name, f.read(), "audio/wav")}, data={"response_format": "verbose_json"}, timeout=120).json()
    t_asr = time.perf_counter()
    transcript = (asr.get("text") or "").strip()
    rec = {"id": f"radio-{uuid.uuid4().hex[:6]}", "file": file_name, "url": f"/radio/{file_name}", "speaker": speaker, "channel": channel,
           "transcript": transcript, "seconds": asr.get("duration"), "asr_ms": round((t_asr - t0) * 1000), "ts": scen_now_iso(), "note": note,
           "claim": None, "bus": None, "event_id": None}
    if STATE["epoch"] != epoch:
        rec["note"] = "dropped: incident was reset while this transmission was being processed"; return rec
    if transcript:
        try:
            c = radio_claim(transcript, speaker); rec["claim"] = c; rec["llm_ms"] = round((time.perf_counter() - t_asr) * 1000)
            if STATE["epoch"] != epoch: rec["note"] = "dropped: incident was reset while this transmission was being processed"; return rec
            if c.get("entity") and c.get("claim"):
                ev = {"source": "radio_asr", "timestamp": rec["ts"], "confidence": max(0.05, min(0.99, float(c.get("confidence") or 0.7))),
                      "entity": c["entity"], "claim": c["claim"], "raw_evidence_ref": f"radio/{file_name}",
                      "event_id": f"radio-{uuid.uuid4().hex[:10]}",
                      "details": {"transcript": transcript, "speaker": speaker, "channel": channel, "summary": c.get("summary"),
                                  "people_trapped": c.get("people_trapped"), "raw_bytes": os.path.getsize(path), "asr_ms": rec["asr_ms"], "llm_ms": rec["llm_ms"]}}
                rec["bus"] = httpx.post(f"{BUS}/events", json=ev, timeout=60).json(); rec["event_id"] = ev["event_id"]
        except Exception as e:
            rec["error"] = f"claim extraction failed: {e}"
    RADIO_LOG.append(rec); broadcast({"type": "radio", "radio": rec}); return rec


@app.get("/radio/library")
async def radio_library():
    try: return {"lines": json.load(open(os.path.join(RADIO_LIB, "library.json")))}
    except Exception as e: return {"lines": [], "error": str(e)}


@app.post("/radio/library/{line_id}")
async def radio_play_library(line_id: str):
    """Transmit one built-in line: it goes through the real ASR and claim extraction, like any recording."""
    lib = {l["id"]: l for l in json.load(open(os.path.join(RADIO_LIB, "library.json")))}
    if line_id not in lib: raise HTTPException(404, "unknown radio line")
    l = lib[line_id]
    return await asyncio.get_running_loop().run_in_executor(None, process_radio, os.path.join(RADIO_LIB, l["file"]), l["file"], l["speaker"], l["channel"], "library")


@app.post("/radio")
async def radio_upload(file: UploadFile = File(...), speaker: str = Form("Field unit"), channel: str = Form("ch3")):
    """Upload a radio recording (wav/mp3/m4a): saved, transcribed by faster-whisper on the Nano, turned into a claim by the
    local LLM, posted to the bus, and played to every connected twin."""
    os.makedirs(RADIO_UPLOADS, exist_ok=True)
    name = f"{int(time.time())}_{re.sub(r'[^A-Za-z0-9._-]+', '_', file.filename or 'radio.wav')}"
    path = os.path.join(RADIO_UPLOADS, name)
    with open(path, "wb") as f:
        while chunk := await file.read(1 << 20): f.write(chunk)
    return await asyncio.get_running_loop().run_in_executor(None, process_radio, path, name, speaker, channel, "upload")


# ---- radio queue: the start screen stages transmissions, START INCIDENT plays them in order with gaps (server side,
#      so it keeps going after the popup closes). Each one still goes through ASR -> LLM -> bus when its turn comes.
RADIO_QUEUE: list[dict] = []


def radio_queue_status() -> dict: return {"items": [{k: v for k, v in i.items()} for i in RADIO_QUEUE]}


def run_radio_queue(items: list):
    for it in items:
        it["state"] = "waiting"; broadcast({"type": "radioqueue", **radio_queue_status()})
        for _ in range(int(max(0.0, float(it.get("delay_s") or 0)) * 10)):
            if it.get("cancelled"): break
            time.sleep(0.1)
        if it.get("cancelled"): it["state"] = "cancelled"; continue
        it["state"] = "transmitting"; broadcast({"type": "radioqueue", **radio_queue_status()})
        try:
            if it.get("library_id"):
                lib = {l["id"]: l for l in json.load(open(os.path.join(RADIO_LIB, "library.json")))}
                l = lib[it["library_id"]]; rec = process_radio(os.path.join(RADIO_LIB, l["file"]), l["file"], l["speaker"], l["channel"], "library (queued)")
            else:
                rec = process_radio(os.path.join(RADIO_UPLOADS, it["file"]), it["file"], it.get("speaker") or "Field unit", it.get("channel") or "ch3", "upload (queued)")
            it["state"] = "done"; it["result"] = {"transcript": rec.get("transcript"), "claim": rec.get("claim"), "bus": (rec.get("bus") or {}).get("action")}
        except Exception as e:
            it["state"] = f"failed: {str(e)[:120]}"
        broadcast({"type": "radioqueue", **radio_queue_status()})


@app.post("/radio/hold")
async def radio_hold(file: UploadFile = File(...)):
    """Save a recording WITHOUT transmitting it (the start screen stages it; /radio/queue plays it later)."""
    os.makedirs(RADIO_UPLOADS, exist_ok=True)
    name = f"{int(time.time())}_{re.sub(r'[^A-Za-z0-9._-]+', '_', file.filename or 'radio.wav')}"
    path = os.path.join(RADIO_UPLOADS, name); size = 0
    with open(path, "wb") as f:
        while chunk := await file.read(1 << 20): f.write(chunk); size += len(chunk)
    return {"file": name, "bytes": size}


@app.post("/radio/queue")
async def radio_queue(body: dict):
    """{items: [{library_id | file, speaker?, channel?, delay_s}]}: transmit in order, each after its delay."""
    items = [{"id": f"rq-{uuid.uuid4().hex[:6]}", "state": "queued", **i} for i in (body or {}).get("items") or []]
    if not items: raise HTTPException(400, "no items")
    RADIO_QUEUE.extend(items)
    threading.Thread(target=run_radio_queue, args=(items,), daemon=True).start()
    broadcast({"type": "radioqueue", **radio_queue_status()})
    return radio_queue_status()


@app.get("/radio/queue")
async def radio_queue_get(): return radio_queue_status()


@app.delete("/radio/queue")
async def radio_queue_clear():
    for it in RADIO_QUEUE:
        if it.get("state") in ("queued", "waiting"): it["cancelled"] = True
    broadcast({"type": "radioqueue", **radio_queue_status()}); return radio_queue_status()


@app.get("/radio/log")
async def radio_log(): return {"radio": RADIO_LOG[-50:]}


@app.get("/radio/{name}")
async def radio_file(name: str):
    for d in (RADIO_LIB, RADIO_UPLOADS):
        p = os.path.join(d, os.path.basename(name))
        if os.path.isfile(p): return FileResponse(p, media_type="audio/wav" if p.endswith(".wav") else "audio/mpeg")
    raise HTTPException(404, "no such radio file")


@app.get("/evidence/latest/{camera}.jpg")
async def latest_frame(camera: str):
    """The newest frame the vision service analysed for this camera (its in-memory buffer), for the twin's camera panel."""
    try:
        async with httpx.AsyncClient(timeout=5) as c: r = await c.get(f"{VISION}/v1/vision/latest/{camera}")
    except Exception as e: raise HTTPException(502, f"vision service unreachable: {e}")
    if r.status_code != 200: raise HTTPException(404, "no frame from this camera yet")
    from fastapi.responses import Response
    return Response(r.content, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Frame-Ts": r.headers.get("x-frame-ts", "")})


@app.get("/evidence/{ref:path}")
async def evidence(ref: str):
    if ref.startswith("radio/"):
        for d in (RADIO_LIB, RADIO_UPLOADS):
            p = os.path.join(d, os.path.basename(ref))
            if os.path.isfile(p): return FileResponse(p, media_type="audio/wav")
        raise HTTPException(404, "radio clip not on disk (scripted radio events have no audio)")
    if ref.startswith("vision/frames/"): path = os.path.join(VISION_FRAMES, os.path.basename(ref))
    elif ref.startswith("vision/samples/"): path = os.path.join(SAMPLES, os.path.basename(ref))
    else: raise HTTPException(404, "no evidence file for this ref (audio/sensor/report refs are ids, not files)")
    if not os.path.isfile(path): raise HTTPException(404, "evidence frame not found")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/health")
async def health():
    ok = False
    try: ok = bool(G and G.ping())
    except Exception: pass
    return {"status": "ok" if ok else "degraded", "neo4j_ok": ok, "clients": len(CLIENTS), "events": len(STATE["events"]), "clock_t": clock_t(), "cameras": CAMERAS,
            "speed_est": STATE["clock"]["speed"], "network": STATE["network"], "suggestions": list(STATE["suggestions"]), "poll_errors": STATE["errors"], "dist": os.path.isdir(DIST)}


@app.get("/mapping")
async def mapping(): return {"graph_to_twin": MAP, "affine_x": AX, "affine_z": AZ, "anchors": ANCHORS}


@app.get("/state")
async def state():
    return {"nodes": STATE["snap"].nodes if STATE["snap"] else {}, "edges": STATE["snap"].edges if STATE["snap"] else {}, "events": STATE["events"][-50:]}


if os.path.isdir(DIST): app.mount("/", StaticFiles(directory=DIST, html=True), name="twin")
else:
    @app.get("/")
    async def no_dist(): return JSONResponse({"error": f"twin build not found at {DIST}; build it with `npm run build -- --outDir dist-live` (see frontend README)"}, status_code=503)
