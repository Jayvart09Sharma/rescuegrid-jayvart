// ─────────────────────────────────────────────────────────────────────────────
// RescueGrid frontend contracts.
// These are the handoff shapes between the frontend and the rest of the team.
// Change them only after agreeing with the owner named on each one.
// ─────────────────────────────────────────────────────────────────────────────

/** Event-bus contract (owner: Shresth). Copied verbatim from 01-shresth.md. */
export type EventSource = 'drone_vision' | 'radio_asr' | 'gps' | 'sensor' | 'field_report'

export interface RescueEvent {
  source: EventSource
  /** ISO 8601 */
  timestamp: string
  /** 0..1 */
  confidence: number
  entity: string
  claim: string
  /** Path or id of the frame/clip/reading that produced this. Drives provenance. */
  raw_evidence_ref: string
}

/** Frontend-side envelope: the contract event plus fields the UI needs to list it. */
export interface LoggedEvent extends Omit<RescueEvent, 'source'> {
  /** 'fusion' marks a graph write derived by Kenil's correlation agent, not a raw feed. */
  source: EventSource | 'fusion'
  id: string
  /** Scenario seconds since t=0 (replay) or since the session started (live). */
  t: number
  severity: Status
  /** Readable one-liner for the timeline, e.g. "Building-14 collapse". */
  title: string
  /** Transcript for radio_asr, report text for field_report, reading for sensor. */
  detail?: string
}

/** Semantic state colors (fixed by the brief): green / yellow / red / blue. */
export type Status = 'normal' | 'warning' | 'danger' | 'conflict' | 'safe'

// ─── Graph mirror (owner: Shresth's Neo4j schema) ──────────────────────────
// The store keeps a read-only mirror of Neo4j. The 3D twin only renders this.

export type NodeLabel =
  | 'Building'
  | 'Road'
  | 'Bridge'
  | 'Unit'
  | 'Hazard'
  | 'Sensor'
  | 'Hospital'
  | 'Shelter'
  | 'StagingArea'
  | 'Incident'

export interface Provenance {
  eventId?: string
  source: EventSource | 'seed' | 'fusion'
  timestamp: string
  confidence: number
  raw_evidence_ref?: string
  claim?: string
}

export interface GraphNode {
  id: string
  label: NodeLabel
  status: Status
  /** When the current status became true. */
  since: string
  lastConfirmed: string
  sources: Provenance[]
  props: Record<string, unknown>
}

export type EdgeType = 'NEAR' | 'AT_RISK' | 'AFFECTS' | 'BLOCKS' | 'ASSIGNED_TO' | 'ROUTED_VIA' | 'CONFLICTS_WITH'

export interface GraphEdge {
  id: string
  from: string
  to: string
  type: EdgeType
  since: string
  sources: Provenance[]
}

/** A graph write, as the fusion agent (Kenil) would apply it to Neo4j. */
export type GraphPatch =
  | { op: 'upsertNode'; node: Partial<GraphNode> & { id: string; label?: NodeLabel } }
  | { op: 'setStatus'; id: string; status: Status }
  | { op: 'setProps'; id: string; props: Record<string, unknown> }
  | { op: 'addEdge'; edge: Omit<GraphEdge, 'since' | 'sources'> }
  | { op: 'removeEdge'; id: string }
  | { op: 'removeNode'; id: string }

// ─── Q&A → 3D twin handoff (owner: Kenil) ─────────────────────────────────

export interface FlyToSignal {
  /** Node id the camera should frame. */
  target: string
  /** Node ids to ring-highlight. */
  highlight: string[]
  /** Plain-language answer shown in the Q&A panel. */
  answer: string
  /** The Cypher that produced the answer, shown for transparency. */
  cypher?: string
}

// ─── Suggestions (never auto-applied, Rule 3) ─────────────────────────────

export interface Suggestion {
  id: string
  kind: 'reroute' | 'withdraw' | 'evacuate' | string
  unit: string
  reason: string
  /** World-space polyline for the suggested path (replay: scripted; live: derived from routeIds by the twin). */
  path: [number, number][]
  /** Live mode: the road ids of the graph's own route (from Kenil's reachability Cypher), unit first, destination last. */
  routeIds?: string[]
  routeNames?: string[]
  destination?: string
  blocked?: string[]
  uncertain?: string[]
  cypher?: string
  state: 'pending' | 'approved' | 'dismissed'
  createdAt: string
}

// ─── Live streams (owner: Shresth's gateway): an uploaded video or the scenario replay ──
export interface StreamInfo {
  id: string
  kind: 'stream' | 'scenario'
  camera?: string
  file?: string
  bytes?: number
  started: string
  state: string
  frames_sent?: number
  last?: string | null
  /** gateway URL of the uploaded file (loops in the camera panel) */
  video?: string | null
}

export interface CameraInfo {
  camera: string
  unit: string | null
  watching: string[]
  type: 'drone' | 'roadcam'
  callsign: string
  /** raw names from camera_entities.json (what the upload form prefills) */
  entities?: Record<string, string>
}

// ─── Radio (owner: Shresth's gateway): recording -> faster-whisper ASR -> LLM claim -> bus ──
export interface RadioMsg {
  id: string
  file: string
  /** gateway URL of the audio */
  url: string
  speaker: string
  channel: string
  transcript: string
  seconds?: number | null
  asr_ms?: number
  llm_ms?: number
  ts: string
  claim?: { entity: string | null; claim: string | null; confidence: number; summary: string; people_trapped?: number | null } | null
  bus?: { action?: string; entity_id?: string | null; applied?: boolean; latency_ms?: number } | null
  event_id?: string | null
  error?: string
  receivedAt: number
}

export interface RadioQueueItem {
  id: string
  library_id?: string
  file?: string
  speaker?: string
  delay_s?: number
  state: string
  result?: { transcript?: string; claim?: { entity: string | null; claim: string | null } | null; bus?: string | null }
}

// ─── Live camera activity (owner: Shresth's gateway, from Aditya's vision tier 1) ──
export interface CameraDetection { label: string; conf: number; bbox_xyxy: number[] }
export interface CameraFrame {
  camera: string
  /** The twin unit that stands for this camera (Drone-1...). */
  unit: string | null
  /** Twin node ids the camera is pointed at (from camera_entities.json). */
  watching: string[]
  cameraType: 'drone' | 'roadcam'
  callsign: string
  ts: string
  t: number
  scene?: { label: string; conf: number }
  gate?: string
  hazards?: string[]
  detections?: CameraDetection[]
  latency_ms?: number
  /** URL of the newest analysed frame (gateway proxy of the vision service's buffer). */
  frame?: string
  frame_ref?: string
  /** camera motion between this frame and the previous one (phase correlation, px at ref_width) */
  motion?: { dx: number; dy: number; response: number; ref_width: number } | null
  image?: { w: number; h: number }
  /** wall-clock ms when this message arrived, for "is this camera live right now" */
  receivedAt: number
  /** true when the gateway re-sent an old frame to a twin that connected after it was analysed */
  replayed?: boolean
}

// ─── Network-condition simulator status (owner: Jayvant) ──────────────────

export type NetworkState = 'normal' | 'throttled' | 'disconnected'

export interface NetworkStatus {
  state: NetworkState
  /** Current WAN bandwidth cap, kbps. null = uncapped. */
  kbps: number | null
  /** Last measured round-trip to the cloud context services, ms. null = unreachable. */
  latencyMs: number | null
}
