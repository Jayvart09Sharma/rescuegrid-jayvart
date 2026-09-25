#!/usr/bin/env python3
"""Human-readable dump of the live graph: entities with status/timestamps/provenance,
fused relationships, hazards, conflicts and the event audit trail. Same information you
would inspect in Neo4j Browser (queries in README.md)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rescuegrid.graph import GraphStore, to_native  # noqa: E402


def t(v):
    v = to_native(v)
    return v.strftime("%H:%M:%S") if hasattr(v, "strftime") else ("-" if v is None else str(v))


with GraphStore() as g:
    print("== ENTITIES (status | since | last_confirmed | source conf evidence) ==")
    for r in g.read_dicts("MATCH (n:Entity) RETURN n ORDER BY n.kind, n.id"):
        n = r["n"]
        flag = "  !! CONFLICT: %s says '%s' (%.2f, %s) since %s" % (n.get("conflict_source"), n.get("conflict_claim"), n.get("conflict_confidence") or 0, n.get("conflict_evidence_ref"), t(n.get("conflict_since"))) if n.get("conflict") else ""
        pos = f" @({n['lat']:.4f},{n['lon']:.4f})" if n.get("lat") is not None else ""
        print(f"  {n['id']:<24} {n.get('kind',''):<8} {str(n.get('status')):<11} since {t(n.get('status_since'))} confirmed {t(n.get('last_confirmed'))} | {n.get('source')} {n.get('confidence')} {n.get('raw_evidence_ref')}{pos}{flag}")
    print("\n== TEAM POSITIONS ==")
    for r in g.read_dicts("MATCH (n:Team) RETURN n.id AS id, n.lat AS lat, n.lon AS lon, n.position_since AS since, n.position_source AS src, n.position_evidence_ref AS ref ORDER BY n.id"):
        print(f"  {r['id']:<18} ({r['lat']:.5f}, {r['lon']:.5f}) since {t(r['since'])} via {r['src']} {r['ref']}")
    print("\n== FUSED RELATIONSHIPS ==")
    q = ("MATCH (a)-[r]->(b) WHERE type(r) IN ['NEAR','AFFECTS','DETECTED_BY','ASSIGNED_TO','CONFLICTS_WITH'] "
         "RETURN a.id AS a, type(r) AS t, b.id AS b, properties(r) AS p ORDER BY t, a, b")
    for r in g.read_dicts(q):
        p = r["p"]; bits = []
        for k in ("active", "since", "last_confirmed", "ended_at", "distance_m", "confidence", "sources", "source", "evidence_refs", "event_ids"):
            if k in p and p[k] is not None:
                bits.append(f"{k}={t(p[k]) if k in ('since','last_confirmed','ended_at') else p[k]}")
        print(f"  ({r['a']})-[:{r['t']}]->({r['b']})  " + ", ".join(bits))
    print("\n== EVENT AUDIT TRAIL ==")
    for r in g.read_dicts("MATCH (e:Event)-[:ABOUT]->(n) RETURN e, n.id AS about ORDER BY e.timestamp"):
        e = r["e"]
        print(f"  {t(e['timestamp'])} {e['id']:<10} {e['source']:<13} {e['entity']:<16}-> {r['about']:<20} {e['claim']:<16} conf {e['confidence']:<5} {'applied' if e['applied'] else 'NOT applied':<11} {e.get('action')} :: {e.get('note')}")
    print("\n== COUNTS ==")
    print("  " + ", ".join(f"{k.split(':')[1]}={v}" for k, v in g.counts().items()))
