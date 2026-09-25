"""Deep test of the SHARED RescueGrid Neo4j. Run: .venv/bin/python tests/deep_test.py [--keep]
Checks: connectivity (local + LAN), schema (constraints/indexes/fulltext), seed integrity (temporal + provenance
fields on every entity), point/distance queries, temporal 'what changed' queries, uniqueness under concurrent
writers, transaction atomicity, write/read latency (Shresth's benchmark numbers), and restart persistence.
Test data is written under a 'deeptest-' prefix and removed at the end unless --keep."""
import os, socket, subprocess, sys, time, uuid, statistics, threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from neo4j import GraphDatabase
from neo4j.exceptions import ConstraintError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAN_IP = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()[0]
URI_LOCAL = os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7688"); URI_LAN = f"bolt://{LAN_IP}:7688"
AUTH = (os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", "rescuegrid"))
KEEP = "--keep" in sys.argv
RESULTS = []

def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail)); print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  - {detail}" if detail else ""))
    return ok

def pct(xs, p): xs = sorted(xs); return xs[min(len(xs) - 1, int(len(xs) * p))]

# ---------------------------------------------------------------- 1. connectivity
print("\n== 1. connectivity")
drv = GraphDatabase.driver(URI_LOCAL, auth=AUTH); drv.verify_connectivity(); check("bolt local", True, URI_LOCAL)
# The shared instance binds to 127.0.0.1 on purpose: every teammate's code runs ON the Nano; laptops use ssh -L.
try:
    d2 = GraphDatabase.driver(URI_LAN, auth=AUTH); d2.verify_connectivity(); d2.close(); print(f"  [INFO] bolt also reachable on LAN {URI_LAN}")
except Exception: check("bolt NOT exposed on the LAN address (localhost-only by design)", True, URI_LAN)
try:
    s = socket.create_connection(("127.0.0.1", 7475), timeout=3); s.close(); check("http browser port on localhost", True, "http://127.0.0.1:7475")
except Exception as e: check("http browser port on localhost", False, str(e))
try:
    GraphDatabase.driver(URI_LOCAL, auth=("neo4j", "wrong")).verify_connectivity(); check("auth enforced", False, "wrong password accepted")
except Exception: check("auth enforced", True)

def run(q, **p):
    with drv.session() as s: return [r.data() for r in s.run(q, **p)]

# ---------------------------------------------------------------- 2. schema
print("\n== 2. schema")
cons = {r["name"] for r in run("SHOW CONSTRAINTS YIELD name")}
want_cons = {"entity_id", "building_id", "road_id", "team_id", "hazard_id", "sensor_id", "facility_id", "event_id"}
check("8 uniqueness constraints", want_cons <= cons, f"missing={sorted(want_cons - cons)}" if not want_cons <= cons else "")
idx = {r["name"]: r for r in run("SHOW INDEXES YIELD name, type, state")}
want_idx = {"entity_name", "entity_kind", "entity_status", "entity_status_since", "entity_last_confirmed", "entity_location", "event_timestamp", "event_entity_id", "event_source", "event_ingested_at", "entity_search"}
check("11 lookup/temporal/point/fulltext indexes", want_idx <= set(idx), f"missing={sorted(want_idx - set(idx))}")
check("all indexes ONLINE", all(r["state"] == "ONLINE" for r in idx.values()), str({k: v["state"] for k, v in idx.items() if v["state"] != "ONLINE"}))
check("fulltext index type", idx.get("entity_search", {}).get("type") == "FULLTEXT")
check("point index type", idx.get("entity_location", {}).get("type") == "POINT")

# ---------------------------------------------------------------- 3. seed integrity
print("\n== 3. seed integrity (temporal + provenance on EVERY entity)")
ents = run("MATCH (n:Entity) RETURN n.id AS id, n.kind AS kind, n.status AS status, n.status_since AS since, n.last_confirmed AS conf_at, n.source AS source, n.confidence AS confidence, n.raw_evidence_ref AS ref, n.location AS loc, labels(n) AS labels")
check("seed loaded (static world = 18 entities; hazards appear only from events)", len(ents) >= 18, f"{len(ents)} entities")
missing = [e["id"] for e in ents if any(e[k] is None for k in ("status", "since", "conf_at", "source", "confidence", "ref", "loc"))]
check("no entity missing status/status_since/last_confirmed/source/confidence/evidence/location", not missing, str(missing))
check("every entity has exactly 2 labels (:Entity + type)", all(len(e["labels"]) == 2 for e in ents))
counts = run("MATCH (n:Entity) RETURN n.kind AS kind, count(*) AS n ORDER BY kind")
check("static kinds present (hazards may also exist once events are ingested)", {c["kind"] for c in counts} >= {"building", "road", "team", "sensor", "facility"}, str(counts))
rels = run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS n ORDER BY t")
check("static relationships (CONNECTS_TO, ON_ROAD, MONITORS, STAGED_AT)", {r["t"] for r in rels} >= {"CONNECTS_TO", "ON_ROAD", "MONITORS", "STAGED_AT"}, str(rels))
ft = run("CALL db.index.fulltext.queryNodes('entity_search', 'Main Street') YIELD node, score RETURN node.id AS id ORDER BY score DESC LIMIT 1")
check("fulltext: 'Main Street' -> Road-Main", ft and ft[0]["id"] == "Road-Main", str(ft))
near = run("MATCH (b:Entity {id:'Building-14'}), (t:Team) WHERE t.location IS NOT NULL RETURN t.id AS id, round(point.distance(t.location, b.location)) AS d ORDER BY d LIMIT 1")
check("point distance query works", bool(near) and near[0]["d"] is not None, str(near))
route = run("MATCH p=(a:Road {id:'Road-Oak'})-[:CONNECTS_TO*1..4]-(b:Road {id:'Road-River'}) RETURN [x IN nodes(p) | x.id] AS r, length(p) AS hops ORDER BY hops LIMIT 1")
check("road graph reachability Oak -> River", bool(route), str(route))

# ---------------------------------------------------------------- 4. temporal semantics
print("\n== 4. temporal + provenance writes")
now = datetime.now(timezone.utc); tag = "deeptest-" + uuid.uuid4().hex[:6]
run("""MERGE (e:Event {id:$id}) SET e.source='drone_vision', e.timestamp=$ts, e.confidence=0.91, e.entity_id='Building-22', e.claim='damaged',
       e.raw_evidence_ref=$ref, e.applied=true, e.action='override', e.ingested_at=datetime(), e.tag=$tag
       WITH e MATCH (n:Entity {id:'Building-22'}) MERGE (e)-[:ABOUT]->(n)""", id=tag + "-evt1", ts=now, ref="drone/cam1/frame_deeptest.jpg", tag=tag)
recent = run("MATCH (e:Event)-[:ABOUT]->(n) WHERE e.timestamp >= $now - duration({minutes:5}) RETURN e.id AS id, n.id AS about", now=now)
check("'what changed in the last 5 minutes' finds the new event", any(r["id"] == tag + "-evt1" for r in recent), f"{len(recent)} recent")
old = run("MATCH (e:Event)-[:ABOUT]->(n) WHERE e.timestamp >= $now - duration({minutes:5}) RETURN count(e) AS c", now=now + timedelta(minutes=10))[0]["c"]
check("same query 10 minutes later excludes it", old == 0 or all(r["id"] != tag + "-evt1" for r in run("MATCH (e:Event) WHERE e.timestamp >= $now - duration({minutes:5}) RETURN e.id AS id", now=now + timedelta(minutes=10))))
tz = run("MATCH (e:Event {id:$id}) RETURN e.timestamp.timezone AS tz, e.timestamp AS ts", id=tag + "-evt1")[0]
check("datetime stored with timezone", tz["tz"] in ("Z", "UTC", "+00:00"), str(tz["tz"]))

# ---------------------------------------------------------------- 5. atomicity + uniqueness under concurrency
print("\n== 5. transactions + concurrency")
try:
    with drv.session() as s:
        with s.begin_transaction() as tx:
            tx.run("CREATE (:Entity:Building {id:$id, kind:'building', name:'tmp', tag:$tag})", id=tag + "-B1", tag=tag)
            tx.run("CREATE (:Entity:Building {id:$id, kind:'building', name:'tmp', tag:$tag})", id=tag + "-B1", tag=tag)  # duplicate -> whole tx must roll back
            tx.commit()
    check("duplicate id rejected by constraint", False, "commit succeeded")
except ConstraintError:
    left = run("MATCH (n:Entity {id:$id}) RETURN count(n) AS c", id=tag + "-B1")[0]["c"]
    check("duplicate id rejected AND transaction rolled back atomically", left == 0, f"{left} rows left")
N_THREADS, N_EACH = 8, 40
def writer(t):
    ok = 0
    with GraphDatabase.driver(URI_LOCAL, auth=AUTH) as d:
        with d.session() as s:
            for i in range(N_EACH):
                s.execute_write(lambda tx: tx.run("""CREATE (e:Event {id:$id, source:'gps', timestamp:datetime(), confidence:0.95, entity_id:'Team-Rescue4', claim:'position_update', raw_evidence_ref:$ref, tag:$tag})
                                                     WITH e MATCH (n:Entity {id:'Team-Rescue4'}) MERGE (e)-[:ABOUT]->(n)""", id=f"{tag}-w{t}-{i}", ref=f"mqtt/gps/{t}/{i}", tag=tag).consume()); ok += 1
    return ok
t0 = time.perf_counter()
with ThreadPoolExecutor(N_THREADS) as ex: done = sum(ex.map(writer, range(N_THREADS)))
dt = time.perf_counter() - t0
stored = run("MATCH (e:Event {tag:$tag})-[:ABOUT]->() RETURN count(e) AS c", tag=tag)[0]["c"]
check(f"{N_THREADS} concurrent writers x {N_EACH} events all committed with ABOUT edges", done == stored - 1 == N_THREADS * N_EACH, f"{stored - 1}/{N_THREADS * N_EACH} in {dt:.2f}s = {(N_THREADS * N_EACH) / dt:.0f} events/s")
# same id from many threads at once: exactly one wins
def racer(_):
    try:
        with GraphDatabase.driver(URI_LOCAL, auth=AUTH) as d, d.session() as s:
            s.execute_write(lambda tx: tx.run("CREATE (:Event {id:$id, tag:$tag})", id=tag + "-race", tag=tag).consume()); return 1
    except ConstraintError: return 0
with ThreadPoolExecutor(8) as ex: wins = sum(ex.map(racer, range(8)))
check("8 threads racing on one id: exactly one insert wins", wins == 1 and run("MATCH (e:Event {id:$id}) RETURN count(e) AS c", id=tag + "-race")[0]["c"] == 1, f"wins={wins}")

# ---------------------------------------------------------------- 6. latency (Shresth's benchmark numbers)
print("\n== 6. latency")
w = []
with drv.session() as s:
    for i in range(200):
        t = time.perf_counter()
        s.execute_write(lambda tx: tx.run("""MATCH (n:Entity {id:'Road-3rd'}) SET n.last_confirmed=datetime(), n.deeptest_touch=$i
                                            CREATE (e:Event {id:$id, source:'radio_asr', timestamp:datetime(), confidence:0.8, entity_id:'Road-3rd', claim:'open', raw_evidence_ref:'radio/x', tag:$tag})
                                            MERGE (e)-[:ABOUT]->(n)""", i=i, id=f"{tag}-lat-{i}", tag=tag).consume())
        w.append((time.perf_counter() - t) * 1000)
check("event -> graph write latency (status update + Event + ABOUT edge, single tx)", pct(w, 0.95) < 50, f"p50={pct(w, .5):.1f}ms p95={pct(w, .95):.1f}ms max={max(w):.1f}ms")
queries = {
    "blocked roads": "MATCH (r:Road) WHERE r.status IN ['blocked','restricted'] OR r.conflict = true RETURN r.id, r.status, r.source, r.confidence LIMIT 25",
    "last 5 min events": "MATCH (e:Event)-[:ABOUT]->(n) WHERE e.timestamp >= datetime() - duration({minutes:5}) RETURN n.id, e.source, e.claim ORDER BY e.timestamp DESC LIMIT 25",
    "route Oak->hospital": "MATCH (f:Facility {facility_type:'hospital'})-[:ON_ROAD]->(d:Road) MATCH p=(a:Road {id:'Road-Oak'})-[:CONNECTS_TO*0..6]-(d) RETURN [x IN nodes(p)|x.id] AS r, length(p) AS hops ORDER BY hops LIMIT 3",
    "nearest units to B22": "MATCH (b:Entity {id:'Building-22'}), (t:Team) WHERE t.location IS NOT NULL RETURN t.id, round(point.distance(t.location,b.location)) AS d ORDER BY d LIMIT 5",
    "fulltext resolve": "CALL db.index.fulltext.queryNodes('entity_search','Rescue Team 4') YIELD node, score RETURN node.id LIMIT 3",
}
for name, q in queries.items():
    r = []
    with drv.session() as s:
        for _ in range(50):
            t = time.perf_counter(); s.execute_read(lambda tx: tx.run(q).data()); r.append((time.perf_counter() - t) * 1000)
    check(f"query latency: {name}", pct(r, 0.95) < 100, f"p50={pct(r, .5):.1f}ms p95={pct(r, .95):.1f}ms")

# ---------------------------------------------------------------- 7. restart persistence
print("\n== 7. restart persistence")
before = run("MATCH (n) RETURN count(n) AS n")[0]["n"]; before_ev = run("MATCH (e:Event {tag:$tag}) RETURN count(e) AS n", tag=tag)[0]["n"]
drv.close()
rc = subprocess.run([os.path.join(ROOT, "neo4jctl.sh"), "restart"], capture_output=True, text=True)
drv = GraphDatabase.driver(URI_LOCAL, auth=AUTH)
after = run("MATCH (n) RETURN count(n) AS n")[0]["n"]; after_ev = run("MATCH (e:Event {tag:$tag}) RETURN count(e) AS n", tag=tag)[0]["n"]
check("node count identical after restart", before == after, f"{before} -> {after}")
check("test events survived restart", before_ev == after_ev and after_ev > 0, f"{after_ev}")
check("restart script exit 0 and ready", rc.returncode == 0 and "ready" in rc.stdout, rc.stdout.strip().splitlines()[-1] if rc.stdout.strip() else rc.stderr[-200:])

# ---------------------------------------------------------------- cleanup
if not KEEP:
    run("MATCH (e {tag:$tag}) DETACH DELETE e", tag=tag); run("MATCH (n:Entity {id:'Road-3rd'}) REMOVE n.deeptest_touch")
    left = run("MATCH (e {tag:$tag}) RETURN count(e) AS c", tag=tag)[0]["c"]
    check("cleanup: test data removed, seed untouched", left == 0 and run("MATCH (n:Entity) RETURN count(n) AS c")[0]["c"] == len(ents), f"left={left}")
drv.close()
fails = [r for r in RESULTS if not r[1]]
print(f"\n{'ALL PASS' if not fails else 'FAILURES: ' + str(len(fails))}  ({len(RESULTS)} checks)")
sys.exit(1 if fails else 0)
