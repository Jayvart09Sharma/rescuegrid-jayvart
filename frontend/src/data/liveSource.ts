// Live adapter: same store API as the replay source, fed by Shresth's gateway (rescuegrid/bus/gateway.py :8097).
// Nothing here is scripted: entities, statuses, edges, events, suggestions and camera activity all come from the
// backend (Neo4j via the bus, Kenil's Q&A, Aditya's vision tier 1). Message shapes (owner: Shresth):
//   { type: 'seed',       nodes: GraphNode[], edges: GraphEdge[] }   // graph entities, merged over the seed city geometry
//   { type: 'event',      event: LoggedEvent, patches?: GraphPatch[], silent?: boolean }
//                          // patches = the Neo4j diff that event caused (the twin renders the graph, never the feeds)
//   { type: 'patch',      patches: GraphPatch[] }            // periodic graph diff (e.g. NEAR edges expiring)
//   { type: 'suggestion', suggestion: Suggestion, silent?: boolean }   // routeIds from the graph's own Cypher route
//   { type: 'cameras',    cameras: CameraInfo[] }            // which entity each camera watches (camera_entities.json)
//   { type: 'camera',     ...CameraFrame }                   // one analysed frame: scene, detections, latest-frame URL
//   { type: 'network',    status: NetworkStatus }             // Jayvant's simulator, via the gateway
//   { type: 'flyto',      signal: FlyToSignal }               // Kenil's Q&A (when pushed by someone else's question)
//   { type: 'clock',      t: number, speed?: number }         // scenario seconds (the bus's replay clock)
import { ENV } from '../config'
import { nodePos, useStore } from '../store'
import { HALF } from './seed'
import { paintAll } from '../scene/coverage'
import type {
  CameraFrame, CameraInfo, FlyToSignal, GraphEdge, GraphNode, GraphPatch, LoggedEvent, NetworkState, NetworkStatus, Provenance, RadioMsg, RadioQueueItem, StreamInfo, Suggestion,
} from '../types'

type Msg =
  | { type: 'seed'; nodes: GraphNode[]; edges: GraphEdge[] }
  | { type: 'event'; event: LoggedEvent; patches?: GraphPatch[]; silent?: boolean }
  | { type: 'patch'; patches: GraphPatch[] }
  | { type: 'suggestion'; suggestion: Suggestion; silent?: boolean }
  | { type: 'cameras'; cameras: CameraInfo[] }
  | ({ type: 'camera' } & Omit<CameraFrame, 'receivedAt'>)
  | { type: 'network'; status: NetworkStatus }
  | { type: 'flyto'; signal: FlyToSignal }
  | { type: 'clock'; t: number; speed?: number }
  | { type: 'stream'; stream: StreamInfo }
  | { type: 'radio'; radio: Omit<RadioMsg, 'receivedAt'>; silent?: boolean }
  | { type: 'reactive'; enabled: boolean }
  | { type: 'radioqueue'; items: RadioQueueItem[] }
  | { type: 'reset' }

// Scenario clock as last told by the gateway; advanced locally between ticks.
const clock = { t: 0, at: performance.now(), speed: 1 }
const seen = new Set<string>()
const cameras = new Map<string, CameraInfo>()

// Drone position = where its camera is pointed (anchor) + the camera motion integrated from the footage itself.
// Pixels of global frame shift (phase correlation at a 320 px reference) -> world units; a full-frame shift ~ one footprint.
const MOTION_K = 17 / 320
const MOTION_MIN_RESPONSE = 0.12
const flight = new Map<string, { x: number; z: number; clip: string | null; idx: number }>()

/** Polyline for a suggested route from graph ids: unit -> road segments -> destination (twin geometry, no script). */
function pathFromIds(sg: Suggestion): [number, number][] {
  const { nodes } = useStore.getState()
  const ids = [sg.unit, ...(sg.routeIds ?? []), ...(sg.destination ? [sg.destination] : [])]
  const pts: [number, number][] = []
  for (const id of ids) {
    const p = nodePos(nodes[id])
    if (p && !(pts.length && pts[pts.length - 1][0] === p[0] && pts[pts.length - 1][1] === p[1])) pts.push(p)
  }
  if (pts.length < 2) {
    const u = nodePos(nodes[sg.unit]) ?? [0, 0]
    return [u, [u[0] + 0.01, u[1]]]
  }
  return pts
}

/** Nodes the gateway cannot place itself (a hazard seen by a camera at a building) carry props.at = the entity's id;
 *  give them that entity's position from the twin's own geometry. */
function placeAt(patches: GraphPatch[]): GraphPatch[] {
  const { nodes } = useStore.getState()
  return patches.map((p) => {
    if (p.op !== 'upsertNode' || typeof p.node.props?.at !== 'string' || typeof p.node.props.x === 'number') return p
    const pos = nodePos(nodes[p.node.props.at as string])
    return pos ? { ...p, node: { ...p.node, props: { ...p.node.props, x: pos[0], z: pos[1], r: p.node.props.r ?? 8 } } } : p
  })
}

function handle(msg: Msg) {
  const s = useStore.getState()
  switch (msg.type) {
    case 'seed': {
      // The twin's seed city stays (geometry lives client-side); graph entities overlay it by id.
      const nodes = { ...s.nodes }
      for (const n of msg.nodes) {
        const prev = nodes[n.id]
        if (typeof n.props?.at === 'string' && typeof n.props.x !== 'number') {
          const pos = nodePos(nodes[n.props.at as string])
          if (pos) n.props = { ...n.props, x: pos[0], z: pos[1], r: n.props.r ?? 8 }
        }
        nodes[n.id] = prev
          ? { ...prev, ...n, label: prev.label === 'Bridge' ? 'Bridge' : n.label ?? prev.label, props: { ...prev.props, ...n.props } }
          : n
      }
      useStore.setState({ nodes, edges: { ...s.edges, ...Object.fromEntries(msg.edges.map((e) => [e.id, e])) } })
      break
    }
    case 'event': {
      const e = msg.event
      if (seen.has(e.id)) break
      seen.add(e.id)
      const prov: Provenance = {
        eventId: e.id, source: e.source, timestamp: e.timestamp, confidence: e.confidence,
        raw_evidence_ref: e.raw_evidence_ref, claim: e.claim,
      }
      s.logEvent(e, msg.silent)
      if (msg.patches?.length) s.applyPatches(placeAt(msg.patches), prov)
      break
    }
    case 'patch':
      s.applyPatches(placeAt(msg.patches))
      break
    case 'suggestion': {
      const sg = { ...msg.suggestion }
      if (!sg.path?.length) sg.path = pathFromIds(sg)
      const existing = s.suggestions.find((x) => x.id === sg.id)
      if (!existing) s.addSuggestion({ ...sg, path: sg.path.length ? sg.path : [] }, msg.silent)
      else if (existing.state !== sg.state && sg.state !== 'pending') s.decideSuggestion(sg.id, sg.state)
      break
    }
    case 'cameras': {
      cameras.clear()
      for (const c of msg.cameras) cameras.set(c.camera, c)
      useStore.setState({ cameraMap: Object.fromEntries(msg.cameras.map((c) => [c.camera, c])) })
      break
    }
    case 'stream': {
      s.setStream(msg.stream)
      const st = msg.stream
      if (st.kind === 'stream' && st.camera) {
        if (st.state === 'running') ensureDrone(st.camera)
        else if (!useStore.getState().cameras[st.camera] || performance.now() - useStore.getState().cameras[st.camera].receivedAt > 10000) retireDrone(st.camera)
      }
      break
    }
    case 'radio':
      s.addRadio({ ...msg.radio, receivedAt: performance.now() }, msg.silent)
      break
    case 'reactive':
      useStore.setState({ reactive: msg.enabled })
      break
    case 'radioqueue':
      useStore.setState({ radioQueue: msg.items })
      break
    case 'reset':
      // the backend wiped the incident: drop the mirror; a fresh seed follows on the same socket
      seen.clear()
      s.resetGraph()
      useStore.setState({ streams: s.streams.filter((x) => x.state === 'running') })
      break
    case 'camera': {
      const { type: _t, ...rest } = msg
      void _t
      const replayed = Boolean((msg as { replayed?: boolean }).replayed)
      s.setCamera({ ...rest, receivedAt: performance.now(), replayed })
      if (!replayed) ensureDrone(msg.camera)   // frames are flowing for this camera: the drone exists
      if (!cameras.has(msg.camera)) cameras.set(msg.camera, { camera: msg.camera, unit: msg.unit, watching: msg.watching, type: msg.cameraType, callsign: msg.callsign })
      // fly the drone the way the footage moves
      const clip = msg.frame_ref?.split('#')[0] ?? null
      const idx = Number(/frame(\d+)/.exec(msg.frame_ref ?? '')?.[1] ?? -1)
      const info = cameras.get(msg.camera)
      const anchor = info?.watching.map((id) => nodePos(s.nodes[id])).find(Boolean) ?? nodePos(s.nodes[msg.unit ?? ''])
      let f = flight.get(msg.camera)
      // new clip, or the clip looped back to its first frames: start again over what the camera watches
      if (!f || f.clip !== clip || (idx >= 0 && idx < f.idx)) {
        f = { x: anchor?.[0] ?? 0, z: anchor?.[1] ?? 0, clip, idx }
        flight.set(msg.camera, f)
      }
      if (idx >= 0) f.idx = idx
      const m = msg.motion
      if (m && !replayed && m.response >= MOTION_MIN_RESPONSE) {
        f.x = Math.max(-HALF - 6, Math.min(HALF + 6, f.x - m.dx * MOTION_K))
        f.z = Math.max(-HALF - 6, Math.min(HALF + 6, f.z - m.dy * MOTION_K))
      }
      break
    }
    case 'network':
      s.setNetwork(msg.status)
      break
    case 'flyto':
      s.flyToSignal(msg.signal)
      break
    case 'clock':
      clock.t = msg.t
      clock.at = performance.now()
      clock.speed = msg.speed ?? clock.speed
      break
  }
}

/** A drone exists in the twin only while its camera has a video stream (frames arriving or an upload running). */
function ensureDrone(camera: string) {
  const s = useStore.getState()
  const c = cameras.get(camera)
  if (!c?.unit || s.nodes[c.unit]) return
  const anchor = c.watching.map((id) => nodePos(s.nodes[id])).find(Boolean) ?? [0, 0]
  s.applyPatches([{ op: 'upsertNode', node: { id: c.unit, label: 'Unit', status: 'safe', props: { kind: 'drone', callsign: c.callsign, camera, cameraType: c.type, x: anchor[0], z: anchor[1] } } }])
}

function retireDrone(camera: string) {
  const s = useStore.getState()
  const c = cameras.get(camera)
  if (!c?.unit || !s.nodes[c.unit]) return
  s.applyPatches([{ op: 'removeNode', id: c.unit }])
  flight.delete(camera)
}

/** Apply each drone's flight position (anchor at what its camera watches, moved by the footage's own motion).
 *  Nothing moves unless frames are arriving; there is no scripted flight plan. */
function placeCameras() {
  const s = useStore.getState()
  const patches: GraphPatch[] = []
  const now = performance.now()
  for (const c of cameras.values()) {
    if (!c.unit || !s.nodes[c.unit]) continue
    const frame = s.cameras[c.camera]
    const streaming = Boolean(frame && !frame.replayed && now - frame.receivedAt < 3000)
    const running = s.streams.some((st) => st.camera === c.camera && st.state === 'running')
    if (!running && (!frame || frame.replayed || now - frame.receivedAt > 10000)) { retireDrone(c.camera); continue }
    const f = flight.get(c.camera)
    const anchor = c.watching.map((id) => nodePos(s.nodes[id])).find(Boolean)
    const pos = f ? [f.x, f.z] : anchor
    if (!pos) continue
    patches.push({ op: 'setProps', id: c.unit, props: { x: pos[0], z: pos[1], streaming } })
  }
  if (patches.length) s.applyPatches(patches)
}

export const live = {
  start() {
    useStore.setState({ mode: 'live', playing: true })
    seen.clear()
    flight.clear()
    paintAll()
    // the seed city's placeholder drones are not real feeds: only drones with a video stream exist in live mode
    const st = useStore.getState()
    st.applyPatches(Object.values(st.nodes).filter((n) => n.label === 'Unit' && n.props.kind === 'drone').map((n) => ({ op: 'removeNode' as const, id: n.id })))
    let ws: WebSocket | null = null
    let stop = false
    const connect = () => {
      ws = new WebSocket(ENV.liveUrl!)
      ws.onmessage = (m) => handle(JSON.parse(m.data as string) as Msg)
      ws.onclose = () => { if (!stop) window.setTimeout(connect, 1500) }
    }
    connect()
    const tick = window.setInterval(() => {
      const now = performance.now()
      useStore.getState().setClock(clock.t + ((now - clock.at) / 1000) * clock.speed)
      placeCameras()
    }, 100)
    return () => { stop = true; ws?.close(); clearInterval(tick) }
  },

  async ask(question: string): Promise<FlyToSignal> {
    const r = await fetch(ENV.qaUrl!, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ question }),
    })
    return (await r.json()) as FlyToSignal
  },

  async setNetwork(state: NetworkState, kbps: number | null) {
    await fetch(`${ENV.netsimUrl}/status`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ state, kbps }),
    })
  },

  async decide(id: string, decision: 'approved' | 'dismissed') {
    await fetch(`${ENV.restUrl}/suggestions/${id}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ decision }),
    })
  },
}
