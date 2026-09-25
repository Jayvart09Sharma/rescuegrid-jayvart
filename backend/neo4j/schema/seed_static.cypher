// ============================================================================
// RescueGrid - static world seed ("Pine County" downtown, fictional but geo-real coords)
// Loaded once at startup (master plan: hospitals/shelters = static seed dataset).
// Provenance for seeded facts: source='seed_dataset', confidence=1.0.
// Dynamic fields (status, status_since, last_confirmed, provenance) are set ON CREATE only,
// so re-running the seed never overwrites live status written by the fusion agent.
// ============================================================================

// ---------- Roads (topology for reachability) ----------
MERGE (n:Entity:Road {id:'Road-Main'})   ON CREATE SET n.status='open', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Road-Main', n.created_at=datetime()
  SET n.kind='road', n.name='Main Street',  n.aliases=['Main Street','Main St','Main'], n.lat=37.3355, n.lon=-121.8890, n.location=point({latitude:37.3355, longitude:-121.8890}), n.lanes=4, n.seeded=true, n.updated_at=datetime();
MERGE (n:Entity:Road {id:'Road-Oak'})    ON CREATE SET n.status='open', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Road-Oak', n.created_at=datetime()
  SET n.kind='road', n.name='Oak Avenue',   n.aliases=['Oak Avenue','Oak Ave','Oak'], n.lat=37.3325, n.lon=-121.8945, n.location=point({latitude:37.3325, longitude:-121.8945}), n.lanes=2, n.seeded=true, n.updated_at=datetime();
MERGE (n:Entity:Road {id:'Road-3rd'})    ON CREATE SET n.status='open', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Road-3rd', n.created_at=datetime()
  SET n.kind='road', n.name='3rd Street',   n.aliases=['3rd Street','Third Street','3rd St'], n.lat=37.3372, n.lon=-121.8868, n.location=point({latitude:37.3372, longitude:-121.8868}), n.lanes=2, n.seeded=true, n.updated_at=datetime();
MERGE (n:Entity:Road {id:'Road-River'})  ON CREATE SET n.status='open', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Road-River', n.created_at=datetime()
  SET n.kind='road', n.name='River Road',   n.aliases=['River Road','River Rd'], n.lat=37.3395, n.lon=-121.8858, n.location=point({latitude:37.3395, longitude:-121.8858}), n.lanes=4, n.seeded=true, n.updated_at=datetime();
MERGE (n:Entity:Road {id:'Road-Bridge'}) ON CREATE SET n.status='open', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Road-Bridge', n.created_at=datetime()
  SET n.kind='road', n.name='Bridge Street', n.aliases=['Bridge Street','Bridge St','the bridge','Pine Creek Bridge'], n.lat=37.3360, n.lon=-121.8935, n.location=point({latitude:37.3360, longitude:-121.8935}), n.lanes=2, n.seeded=true, n.updated_at=datetime();

// road graph (undirected semantics; stored once, queried both ways)
MATCH (a:Road {id:'Road-Oak'}),   (b:Road {id:'Road-Main'})   MERGE (a)-[r:CONNECTS_TO]->(b) SET r.length_m=650,  r.source='seed_dataset';
MATCH (a:Road {id:'Road-Main'}),  (b:Road {id:'Road-River'})  MERGE (a)-[r:CONNECTS_TO]->(b) SET r.length_m=540,  r.source='seed_dataset';
MATCH (a:Road {id:'Road-Main'}),  (b:Road {id:'Road-3rd'})    MERGE (a)-[r:CONNECTS_TO]->(b) SET r.length_m=300,  r.source='seed_dataset';
MATCH (a:Road {id:'Road-3rd'}),   (b:Road {id:'Road-River'})  MERGE (a)-[r:CONNECTS_TO]->(b) SET r.length_m=280,  r.source='seed_dataset';
MATCH (a:Road {id:'Road-Oak'}),   (b:Road {id:'Road-Bridge'}) MERGE (a)-[r:CONNECTS_TO]->(b) SET r.length_m=420,  r.source='seed_dataset';
MATCH (a:Road {id:'Road-Bridge'}),(b:Road {id:'Road-River'})  MERGE (a)-[r:CONNECTS_TO]->(b) SET r.length_m=900,  r.source='seed_dataset';

// ---------- Buildings ----------
MERGE (n:Entity:Building {id:'Building-14'}) ON CREATE SET n.status='intact', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Building-14', n.created_at=datetime()
  SET n.kind='building', n.name='Building 14', n.aliases=['Building 14','Bldg 14','B14'], n.lat=37.3352, n.lon=-121.8893, n.location=point({latitude:37.3352, longitude:-121.8893}), n.building_type='apartment', n.floors=4, n.occupancy_est=120, n.seeded=true, n.updated_at=datetime();
MERGE (n:Entity:Building {id:'Building-7'})  ON CREATE SET n.status='intact', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Building-7', n.created_at=datetime()
  SET n.kind='building', n.name='Building 7',  n.aliases=['Building 7','Bldg 7','B7'], n.lat=37.3374, n.lon=-121.8870, n.location=point({latitude:37.3374, longitude:-121.8870}), n.building_type='office', n.floors=3, n.occupancy_est=60, n.seeded=true, n.updated_at=datetime();
MERGE (n:Entity:Building {id:'Building-22'}) ON CREATE SET n.status='intact', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Building-22', n.created_at=datetime()
  SET n.kind='building', n.name='Building 22', n.aliases=['Building 22','Bldg 22','B22'], n.lat=37.3330, n.lon=-121.8930, n.location=point({latitude:37.3330, longitude:-121.8930}), n.building_type='retail', n.floors=2, n.occupancy_est=40, n.seeded=true, n.updated_at=datetime();
MERGE (n:Entity:Building {id:'Building-3'})  ON CREATE SET n.status='intact', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Building-3', n.created_at=datetime()
  SET n.kind='building', n.name='Building 3',  n.aliases=['Building 3','Bldg 3','B3'], n.lat=37.3390, n.lon=-121.8862, n.location=point({latitude:37.3390, longitude:-121.8862}), n.building_type='warehouse', n.floors=1, n.occupancy_est=10, n.seeded=true, n.updated_at=datetime();

// ---------- Facilities (hospital / shelter) ----------
MERGE (n:Entity:Facility {id:'Facility-CountyGeneral'}) ON CREATE SET n.status='open', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Facility-CountyGeneral', n.created_at=datetime()
  SET n.kind='facility', n.facility_type='hospital', n.name='County General Hospital', n.aliases=['County General Hospital','County General','the hospital','hospital'], n.lat=37.3400, n.lon=-121.8850, n.location=point({latitude:37.3400, longitude:-121.8850}), n.capacity=200, n.beds_available=18, n.seeded=true, n.updated_at=datetime();
MERGE (n:Entity:Facility {id:'Facility-LincolnHS'})     ON CREATE SET n.status='open', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Facility-LincolnHS', n.created_at=datetime()
  SET n.kind='facility', n.facility_type='shelter', n.name='Lincoln High School Shelter', n.aliases=['Lincoln High School','Lincoln High','the shelter','shelter','staging area'], n.lat=37.3320, n.lon=-121.8950, n.location=point({latitude:37.3320, longitude:-121.8950}), n.capacity=300, n.beds_available=300, n.seeded=true, n.updated_at=datetime();

// ---------- Teams / units ----------
MERGE (n:Entity:Team {id:'Team-Rescue4'})    ON CREATE SET n.status='available', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Team-Rescue4', n.created_at=datetime(), n.lat=37.3321, n.lon=-121.8948, n.location=point({latitude:37.3321, longitude:-121.8948}), n.position_since=datetime('2026-09-25T13:00:00Z'), n.position_source='seed_dataset', n.position_evidence_ref='seed/static_world_v1#staging'
  SET n.kind='team', n.unit_type='rescue', n.name='Rescue Team 4', n.callsign='Rescue 4', n.aliases=['Rescue Team 4','Rescue 4','RT4','Team 4','Rescue-4'], n.personnel=6, n.seeded=true, n.updated_at=datetime();
MERGE (n:Entity:Team {id:'Team-Ambulance2'}) ON CREATE SET n.status='available', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Team-Ambulance2', n.created_at=datetime(), n.lat=37.3322, n.lon=-121.8947, n.location=point({latitude:37.3322, longitude:-121.8947}), n.position_since=datetime('2026-09-25T13:00:00Z'), n.position_source='seed_dataset', n.position_evidence_ref='seed/static_world_v1#staging'
  SET n.kind='team', n.unit_type='ambulance', n.name='Ambulance 2', n.callsign='Medic 2', n.aliases=['Ambulance 2','Ambo 2','Medic 2','A2','Ambulance-2'], n.personnel=2, n.seeded=true, n.updated_at=datetime();
MERGE (n:Entity:Team {id:'Team-Ambulance1'}) ON CREATE SET n.status='available', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Team-Ambulance1', n.created_at=datetime(), n.lat=37.3399, n.lon=-121.8851, n.location=point({latitude:37.3399, longitude:-121.8851}), n.position_since=datetime('2026-09-25T13:00:00Z'), n.position_source='seed_dataset', n.position_evidence_ref='seed/static_world_v1#staging'
  SET n.kind='team', n.unit_type='ambulance', n.name='Ambulance 1', n.callsign='Medic 1', n.aliases=['Ambulance 1','Ambo 1','Medic 1','A1','Ambulance-1'], n.personnel=2, n.seeded=true, n.updated_at=datetime();
MERGE (n:Entity:Team {id:'Team-Engine7'})    ON CREATE SET n.status='available', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Team-Engine7', n.created_at=datetime(), n.lat=37.3380, n.lon=-121.8860, n.location=point({latitude:37.3380, longitude:-121.8860}), n.position_since=datetime('2026-09-25T13:00:00Z'), n.position_source='seed_dataset', n.position_evidence_ref='seed/static_world_v1#staging'
  SET n.kind='team', n.unit_type='fire', n.name='Engine 7', n.callsign='Engine 7', n.aliases=['Engine 7','E7','Fire Engine 7'], n.personnel=4, n.seeded=true, n.updated_at=datetime();

// ---------- Sensors ----------
MERGE (n:Entity:Sensor {id:'Sensor-Gas3'})     ON CREATE SET n.status='normal', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Sensor-Gas3', n.created_at=datetime()
  SET n.kind='sensor', n.sensor_type='gas', n.name='Gas Sensor 3', n.aliases=['Gas Sensor 3','GasSensor-3','gas sensor 3','GS3'], n.lat=37.3350, n.lon=-121.8896, n.location=point({latitude:37.3350, longitude:-121.8896}), n.unit='ppm', n.threshold=25.0, n.seeded=true, n.updated_at=datetime();
MERGE (n:Entity:Sensor {id:'Sensor-Seismic1'}) ON CREATE SET n.status='normal', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Sensor-Seismic1', n.created_at=datetime()
  SET n.kind='sensor', n.sensor_type='seismic', n.name='Seismic Sensor 1', n.aliases=['Seismic Sensor 1','Seismic-1','seismometer 1'], n.lat=37.3360, n.lon=-121.8900, n.location=point({latitude:37.3360, longitude:-121.8900}), n.unit='magnitude', n.threshold=4.0, n.seeded=true, n.updated_at=datetime();
MERGE (n:Entity:Sensor {id:'Sensor-Water2'})   ON CREATE SET n.status='normal', n.status_since=datetime('2026-09-25T13:00:00Z'), n.last_confirmed=datetime('2026-09-25T13:00:00Z'), n.source='seed_dataset', n.confidence=1.0, n.raw_evidence_ref='seed/static_world_v1#Sensor-Water2', n.created_at=datetime()
  SET n.kind='sensor', n.sensor_type='water', n.name='Water Level Sensor 2', n.aliases=['Water Level Sensor 2','Water-2','creek gauge 2'], n.lat=37.3361, n.lon=-121.8936, n.location=point({latitude:37.3361, longitude:-121.8936}), n.unit='m', n.threshold=2.5, n.seeded=true, n.updated_at=datetime();

// ---------- Static topology relationships ----------
MATCH (b:Building {id:'Building-14'}), (r:Road {id:'Road-Main'})  MERGE (b)-[x:ON_ROAD]->(r) SET x.source='seed_dataset';
MATCH (b:Building {id:'Building-7'}),  (r:Road {id:'Road-3rd'})   MERGE (b)-[x:ON_ROAD]->(r) SET x.source='seed_dataset';
MATCH (b:Building {id:'Building-22'}), (r:Road {id:'Road-Oak'})   MERGE (b)-[x:ON_ROAD]->(r) SET x.source='seed_dataset';
MATCH (b:Building {id:'Building-3'}),  (r:Road {id:'Road-River'}) MERGE (b)-[x:ON_ROAD]->(r) SET x.source='seed_dataset';
MATCH (f:Facility {id:'Facility-CountyGeneral'}), (r:Road {id:'Road-River'}) MERGE (f)-[x:ON_ROAD]->(r) SET x.source='seed_dataset';
MATCH (f:Facility {id:'Facility-LincolnHS'}),     (r:Road {id:'Road-Oak'})   MERGE (f)-[x:ON_ROAD]->(r) SET x.source='seed_dataset';
MATCH (s:Sensor {id:'Sensor-Gas3'}),   (b:Building {id:'Building-14'}) MERGE (s)-[x:MONITORS]->(b) SET x.source='seed_dataset';
MATCH (s:Sensor {id:'Sensor-Water2'}), (r:Road {id:'Road-Bridge'})    MERGE (s)-[x:MONITORS]->(r) SET x.source='seed_dataset';
// units start staged at the shelter / hospital
MATCH (t:Team {id:'Team-Rescue4'}),    (f:Facility {id:'Facility-LincolnHS'})     MERGE (t)-[x:STAGED_AT]->(f) SET x.source='seed_dataset', x.since=datetime('2026-09-25T13:00:00Z');
MATCH (t:Team {id:'Team-Ambulance2'}), (f:Facility {id:'Facility-LincolnHS'})     MERGE (t)-[x:STAGED_AT]->(f) SET x.source='seed_dataset', x.since=datetime('2026-09-25T13:00:00Z');
MATCH (t:Team {id:'Team-Ambulance1'}), (f:Facility {id:'Facility-CountyGeneral'}) MERGE (t)-[x:STAGED_AT]->(f) SET x.source='seed_dataset', x.since=datetime('2026-09-25T13:00:00Z');
