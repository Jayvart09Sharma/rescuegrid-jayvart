# RescueGrid shared Neo4j (the team's single source of truth)

One Neo4j 5.26 Community instance on the ZGX Nano that every component reads from and writes to.
Rule 1 of the master plan: the 3D twin renders this graph, Q&A queries it, nobody keeps a private copy of "current state".

| | |
|---|---|
| Bolt (drivers, Kenil's layer, Jayvant's adapters) | `bolt://127.0.0.1:7688` |
| Neo4j Browser | `http://127.0.0.1:7475` (from a laptop: `ssh -L 7475:127.0.0.1:7475 -L 7688:127.0.0.1:7688 hp2@<nano>` then open http://localhost:7475) |
| user / password | `neo4j` / `rescuegrid` |
| install | `/home/hp2/Shresth/rescuegrid/neo4j/` (own JDK 21 + Neo4j, no Docker, no sudo) |
| data | `neo4j/data/` (survives restarts; see backup below) |
| memory | heap 1-2 GB + 512 MB page cache, ~2.5 GB RSS |

It binds to localhost on purpose: every teammate's code runs on the Nano, and the frontend talks to
Kenil's `/qa` endpoint (port 8095), never to Bolt directly. Ports 7688/7475 were chosen because Kenil's
private dev instance still holds 7687/7474; once that is retired, change two lines in `neo4j/conf/neo4j.conf`.

## Operate

```
cd /home/hp2/Shresth/rescuegrid/neo4j
./neo4jctl.sh start | stop | restart | status | logs [n]
./neo4jctl.sh schema        # re-apply constraints + indexes (idempotent)
./neo4jctl.sh seed          # re-apply the static world (idempotent, never overwrites live status)
./neo4jctl.sh backup        # neo4j-admin dump into backups/ (stops + restarts, ~10 s)
./neo4jctl.sh cypher "MATCH (n:Entity) RETURN n.id, n.status, n.source, n.status_since ORDER BY n.status_since DESC"
```

To wipe and replay the scenario, use Kenil's ingest (it owns the write path): `NEO4J_URI=bolt://127.0.0.1:7688 python scripts/ingest.py --reset` in his folder.

**Start at boot: done.** The systemd --user unit `rescuegrid-neo4j` is enabled (linger on), so it starts at boot and restarts on failure; `neo4jctl.sh start|stop|restart` drive that unit. For reference, the unit was created as:
(`loginctl enable-linger hp2` is already on). Create `~/.config/systemd/user/rescuegrid-neo4j.service` with
`ExecStart=/home/hp2/Shresth/rescuegrid/neo4j/neo4j/bin/neo4j console`, `Environment=JAVA_HOME=/home/hp2/Shresth/rescuegrid/neo4j/jdk`,
`Restart=on-failure`, `WantedBy=default.target`, then `systemctl --user enable --now rescuegrid-neo4j`.

## Schema

`schema/schema.cypher` (8 uniqueness constraints, temporal + point + fulltext indexes) and `schema/seed_static.cypher`
(the fictional "Pine County" downtown: 5 roads, 4 buildings, 2 facilities, 3 sensors, 4 teams) are the canonical copies.
Origin: Kenil's reasoning layer v0.1; the data model is in `schema/DATA_MODEL.md`. Every entity node carries
`status`, `status_since`, `last_confirmed`, `source`, `confidence`, `raw_evidence_ref` (+ `conflict_*` when two sources
disagree); every ingested report is an `(:Event)-[:ABOUT]->(:Entity)` audit node with `timestamp` and `ingested_at`.
Schema changes: edit here, then `./neo4jctl.sh schema`, and tell Kenil (his `resolve.py` id conventions must match).

## Connect

Python (any teammate):
```python
from neo4j import GraphDatabase
drv = GraphDatabase.driver("bolt://127.0.0.1:7688", auth=("neo4j", "rescuegrid"))
with drv.session() as s:
    rows = s.run("MATCH (e:Event)-[:ABOUT]->(n) WHERE e.timestamp >= $now - duration({minutes:5}) RETURN n.id, e.claim, e.source", now=...).data()
```
Kenil's layer: `NEO4J_URI=bolt://127.0.0.1:7688` in `kenil/rescuegrid-reasoning/.env` (done). Nothing else changes.
Adapters (vision, ASR, GPS, sensors) should NOT write Cypher themselves: post the six-field event contract to Kenil's fusion agent, which owns all writes.

## Deep test (run any time, ~30 s, cleans up after itself)

```
.venv/bin/python tests/deep_test.py        # 33 checks; --keep leaves the test events in place
```
Covers: local connectivity + auth, LAN not exposed, all 8 constraints + 11 indexes online, every seeded entity has the
temporal + provenance fields, fulltext name resolution, point-distance and road-route queries, "what changed in the last
5 minutes" semantics with timezone, constraint violation rolls back the whole transaction, 8 concurrent writers,
8 threads racing on one id, write/read latency, and data surviving a restart.

## Measured 2026-09-24 on the Nano (Nemotron + Qwen3-VL + vision tier 1 all loaded alongside)

| Metric (Shresth's benchmark list) | Result |
|---|---|
| Event -> graph write (status update + Event node + ABOUT edge, one transaction) | p50 4.6 ms, p95 6.3 ms |
| Sustained writes, 8 concurrent producers | 366 events/s, 320/320 committed |
| Query: blocked roads / last-5-min events / route to hospital / nearest units / fulltext resolve | p95 4 to 6 ms each |
| Restart | ~8 s to ready, zero data loss (540 nodes before and after) |
| Kenil's full stack via env override only | 10/10 scenario events applied, conflict detected, 95/95 tests pass, Q&A identical |
