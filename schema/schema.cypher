// ============================================================================
// RescueGrid reasoning layer - Neo4j schema (Neo4j 5.x Community)
// Every entity node carries TWO labels: :Entity plus its type label
// (:Building :Road :Team :Hazard :Sensor :Facility). :Facility is subtyped by
// facility_type = 'hospital' | 'shelter'.
//
// Temporal + provenance fields present on EVERY entity node (see docs/DATA_MODEL.md):
//   status           current claim about the entity ('collapsed', 'blocked', 'spike', ...)
//   status_since     DATETIME - when the current status became true (event timestamp)
//   last_confirmed   DATETIME - most recent event that (re)confirmed the current status
//   source           provenance of the current status: 'drone_vision'|'radio_asr'|'gps'|'sensor'|'field_report'|'seed_dataset'
//   confidence       FLOAT 0..1 of the current status
//   raw_evidence_ref frame / clip / message id that produced the current status
// Full history is kept as (:Event)-[:ABOUT]->(:Entity) nodes, one per ingested event.
// Community edition has no property-existence constraints, so the ingestion code
// (rescuegrid/graph.py) is the guarantor that these fields are always written.
// ============================================================================

// --- identity ---------------------------------------------------------------
CREATE CONSTRAINT entity_id   IF NOT EXISTS FOR (n:Entity)   REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT building_id IF NOT EXISTS FOR (n:Building) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT road_id     IF NOT EXISTS FOR (n:Road)     REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT team_id     IF NOT EXISTS FOR (n:Team)     REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT hazard_id   IF NOT EXISTS FOR (n:Hazard)   REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT sensor_id   IF NOT EXISTS FOR (n:Sensor)   REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT facility_id IF NOT EXISTS FOR (n:Facility) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT event_id    IF NOT EXISTS FOR (e:Event)    REQUIRE e.id IS UNIQUE;

// --- lookup / temporal indexes ---------------------------------------------
CREATE INDEX entity_name           IF NOT EXISTS FOR (n:Entity) ON (n.name);
CREATE INDEX entity_kind           IF NOT EXISTS FOR (n:Entity) ON (n.kind);
CREATE INDEX entity_status         IF NOT EXISTS FOR (n:Entity) ON (n.status);
CREATE INDEX entity_status_since   IF NOT EXISTS FOR (n:Entity) ON (n.status_since);
CREATE INDEX entity_last_confirmed IF NOT EXISTS FOR (n:Entity) ON (n.last_confirmed);
CREATE POINT INDEX entity_location IF NOT EXISTS FOR (n:Entity) ON (n.location);
CREATE INDEX event_timestamp       IF NOT EXISTS FOR (e:Event) ON (e.timestamp);
CREATE INDEX event_entity_id       IF NOT EXISTS FOR (e:Event) ON (e.entity_id);
CREATE INDEX event_source          IF NOT EXISTS FOR (e:Event) ON (e.source);
CREATE INDEX event_ingested_at     IF NOT EXISTS FOR (e:Event) ON (e.ingested_at);

// --- name resolution ("Main Street" -> Road-Main) --------------------------
CREATE FULLTEXT INDEX entity_search IF NOT EXISTS FOR (n:Entity) ON EACH [n.name, n.id, n.aliases];
