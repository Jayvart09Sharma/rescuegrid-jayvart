// Single client-side store. `nodes`/`edges` are a read-only mirror of Neo4j:
// they change only through GraphPatches coming from a source (replay or live).
// The 3D twin renders from here and never feeds facts back into it.
import { create } from 'zustand'
import type {
  CameraFrame, CameraInfo, FlyToSignal, GraphEdge, GraphNode, GraphPatch, LoggedEvent, NetworkStatus, Provenance, RadioMsg, RadioQueueItem, Status, StreamInfo, Suggestion,
} from './types'
import { buildSeed } from './data/seed'
import { isoAt } from './data/clock'

export type CloudService = 'satellite' | 'weather' | 'gis'
export interface CloudTile {
  status: 'ok' | 'loading' | 'slow' | 'down'
  latencyMs: number | null
  lastOkAt: number | null
}

export interface Alert {
  id: string
  title: string
  severity: Status
  sub: string
}

export interface QAState {
  question: string
  status: 'idle' | 'thinking' | 'done'
  signal?: FlyToSignal
}

interface State {
  mode: 'replay' | 'live'
  nodes: Record<string, GraphNode>
  edges: Record<string, GraphEdge>
  events: LoggedEvent[]
  suggestions: Suggestion[]
  /** scenario seconds at which each suggestion was approved (replay motion uses this) */
  approvals: Record<string, number>
  network: NetworkStatus
  cloud: Record<CloudService, CloudTile>
  cloudStats: { ok: number; slow: number; failed: number }
  t: number
  playing: boolean
  speed: number
  selectedEventId: string | null
  selectedNodeId: string | null
  hoverId: string | null
  flyTo: (FlyToSignal & { nonce: number }) | null
  qa: QAState
  alerts: Alert[]
  /** bumps whenever something dramatic happens; HUD shakes/flashes off it */
  shock: { nonce: number; severity: Status; amp?: number }
  /** events per source in the last few seconds, for feed sparklines */
  feedPulse: Record<string, number>
  /** live mode: the newest tier-1 result per camera (Aditya's vision service via the gateway) */
  cameras: Record<string, CameraFrame>
  /** live mode: which entity each camera watches (camera_entities.json via the gateway) */
  cameraMap: Record<string, CameraInfo>
  /** live mode: uploaded video streams / scenario replays running on the Nano */
  streams: StreamInfo[]
  /** the start screen (choose the stream) */
  starter: boolean
  /** live mode: radio transmissions received (newest last); the player auto-plays the newest */
  radio: RadioMsg[]
  /** which radio message the player is playing now (id) */
  radioPlaying: string | null
  /** live mode: the reactive incident simulation (simulated feeds react to real detections) */
  reactive: boolean
  /** live mode: staged radio transmissions playing in order on the Nano */
  radioQueue: RadioQueueItem[]

  resetGraph: () => void
  applyPatches: (patches: GraphPatch[], prov?: Provenance, silent?: boolean) => void
  logEvent: (e: LoggedEvent, silent?: boolean) => void
  addSuggestion: (s: Suggestion, silent?: boolean) => void
  decideSuggestion: (id: string, decision: 'approved' | 'dismissed') => void
  setNetwork: (n: Partial<NetworkStatus>) => void
  setCloud: (svc: CloudService, tile: Partial<CloudTile>) => void
  bumpCloudStat: (k: 'ok' | 'slow' | 'failed') => void
  setClock: (t: number) => void
  setPlaying: (p: boolean) => void
  setSpeed: (s: number) => void
  selectEvent: (id: string | null) => void
  selectNode: (id: string | null) => void
  setHover: (id: string | null) => void
  flyToSignal: (s: FlyToSignal) => void
  setQA: (q: Partial<QAState>) => void
  dismissAlert: (id: string) => void
  setCamera: (c: CameraFrame) => void
  setMode: (mode: 'replay' | 'live') => void
  setStream: (s: StreamInfo) => void
  addRadio: (r: RadioMsg, silent?: boolean) => void
}

const seedNodes = () => Object.fromEntries(buildSeed().map((n) => [n.id, n]))
const initialCloud = (): Record<CloudService, CloudTile> => ({
  satellite: { status: 'loading', latencyMs: null, lastOkAt: null },
  weather: { status: 'loading', latencyMs: null, lastOkAt: null },
  gis: { status: 'loading', latencyMs: null, lastOkAt: null },
})

let alertSeq = 0
let flySeq = 0

export const useStore = create<State>((set, get) => ({
  mode: 'replay',
  nodes: seedNodes(),
  edges: {},
  events: [],
  suggestions: [],
  approvals: {},
  network: { state: 'normal', kbps: null, latencyMs: 140 },
  cloud: initialCloud(),
  cloudStats: { ok: 0, slow: 0, failed: 0 },
  t: 0,
  playing: true,
  speed: 1,
  selectedEventId: null,
  selectedNodeId: null,
  hoverId: null,
  flyTo: null,
  qa: { question: '', status: 'idle' },
  alerts: [],
  shock: { nonce: 0, severity: 'normal' },
  feedPulse: {},
  cameras: {},
  cameraMap: {},
  streams: [],
  starter: false,
  radio: [],
  radioPlaying: null,
  reactive: true,
  radioQueue: [],

  resetGraph: () =>
    set({
      nodes: seedNodes(),
      edges: {},
      events: [],
      suggestions: [],
      approvals: {},
      alerts: [],
      selectedEventId: null,
      flyTo: null,
      qa: { question: '', status: 'idle' },
      cameras: {},
      feedPulse: {},
    }),

  applyPatches: (patches, prov) => {
    const nodes = { ...get().nodes }
    const edges = { ...get().edges }
    const now = prov?.timestamp ?? isoAt(get().t)
    const touch = (id: string) => {
      const n = nodes[id]
      if (!n) return undefined
      const copy = { ...n, lastConfirmed: now, props: { ...n.props } }
      if (prov && !copy.sources.some((s) => s.eventId && s.eventId === prov.eventId)) {
        copy.sources = [...copy.sources, prov]
      }
      nodes[id] = copy
      return copy
    }
    for (const p of patches) {
      switch (p.op) {
        case 'upsertNode': {
          const prev = nodes[p.node.id]
          nodes[p.node.id] = {
            ...({ status: 'normal', label: 'Incident', props: {} } as const),
            ...prev,
            ...p.node,
            since: prev?.since ?? now,
            lastConfirmed: now,
            sources: [...(prev?.sources ?? []), ...(prov ? [prov] : [])],
          } as GraphNode
          break
        }
        case 'setStatus': {
          const n = touch(p.id)
          if (n && n.status !== p.status) {
            n.status = p.status
            n.since = now
          }
          break
        }
        case 'setProps': {
          const n = nodes[p.id]
          // GPS ticks carry no provenance; keep them from bloating `sources`.
          if (n) nodes[p.id] = prov ? { ...touch(p.id)!, props: { ...n.props, ...p.props } } : { ...n, props: { ...n.props, ...p.props } }
          break
        }
        case 'addEdge':
          edges[p.edge.id] = { ...p.edge, since: now, sources: prov ? [prov] : [] }
          break
        case 'removeEdge':
          delete edges[p.id]
          break
        case 'removeNode':
          delete nodes[p.id]
          for (const [eid, e] of Object.entries(edges)) if (e.from === p.id || e.to === p.id) delete edges[eid]
          break
      }
    }
    set({ nodes, edges })
  },

  logEvent: (e, silent) => {
    const s = get()
    const feedPulse = { ...s.feedPulse, [e.source]: (s.feedPulse[e.source] ?? 0) + 1 }
    const next: Partial<State> = { events: [...s.events, e], feedPulse }
    if (!silent && (e.severity === 'danger' || e.severity === 'conflict')) {
      const alert: Alert = {
        id: `al-${++alertSeq}`,
        title: e.title,
        severity: e.severity,
        sub: `${e.source.toUpperCase()} · ${Math.round(e.confidence * 100)}% · ${e.entity}`,
      }
      next.alerts = [...s.alerts.slice(-2), alert]
      next.shock = { nonce: s.shock.nonce + 1, severity: e.severity }
    }
    set(next)
  },

  addSuggestion: (sg, silent) => {
    const s = get()
    if (s.suggestions.some((x) => x.id === sg.id)) return
    set({
      suggestions: [...s.suggestions, sg],
      ...(silent
        ? {}
        : {
            alerts: [...s.alerts.slice(-2), {
              id: `al-${++alertSeq}`, title: `Suggested reroute · ${sg.unit.replace(/-/g, ' ')}`,
              severity: 'safe' as Status, sub: 'COMMANDER APPROVAL REQUIRED',
            }],
          }),
    })
  },

  decideSuggestion: (id, decision) => {
    const s = get()
    set({
      suggestions: s.suggestions.map((x) => (x.id === id ? { ...x, state: decision } : x)),
      approvals: decision === 'approved' ? { ...s.approvals, [id]: s.t } : s.approvals,
    })
  },

  setNetwork: (n) => {
    const prev = get().network.state
    set({ network: { ...get().network, ...n } })
    if (n.state && n.state !== prev) set({ shock: { nonce: get().shock.nonce + 1, severity: n.state === 'normal' ? 'safe' : 'warning' } })
  },
  setCloud: (svc, tile) => set({ cloud: { ...get().cloud, [svc]: { ...get().cloud[svc], ...tile } } }),
  bumpCloudStat: (k) => set({ cloudStats: { ...get().cloudStats, [k]: get().cloudStats[k] + 1 } }),
  setClock: (t) => set({ t }),
  setPlaying: (playing) => set({ playing }),
  setSpeed: (speed) => set({ speed }),
  selectEvent: (selectedEventId) => set({ selectedEventId, selectedNodeId: null }),
  selectNode: (selectedNodeId) => set({ selectedNodeId, selectedEventId: null }),
  setHover: (hoverId) => set({ hoverId }),
  flyToSignal: (s) => {
    const nonce = ++flySeq
    set({ flyTo: { ...s, nonce } })
    // Highlights fade after a while; the Q&A panel keeps the answer (it reads qa.signal).
    setTimeout(() => { if (get().flyTo?.nonce === nonce) set({ flyTo: null }) }, 12000)
  },
  setQA: (q) => set({ qa: { ...get().qa, ...q } }),
  dismissAlert: (id) => set({ alerts: get().alerts.filter((a) => a.id !== id) }),
  setCamera: (c) => set({ cameras: { ...get().cameras, [c.camera]: c } }),
  setMode: (mode) => set({ mode }),
  setStream: (st) => set({ streams: [...get().streams.filter((x) => x.id !== st.id), st].sort((a, b) => a.started.localeCompare(b.started)) }),
  addRadio: (r, silent) => {
    if (get().radio.some((x) => x.id === r.id)) return
    set({ radio: [...get().radio.slice(-40), r], ...(silent ? {} : { radioPlaying: r.id }) })
  },
}))

/** World position of any node, for the camera and labels. */
export function nodePos(n: GraphNode | undefined): [number, number] | null {
  if (!n) return null
  const p = n.props
  if (typeof p.x === 'number' && typeof p.z === 'number') return [p.x, p.z]
  if (Array.isArray(p.a) && Array.isArray(p.b)) {
    const a = p.a as number[]
    const b = p.b as number[]
    return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
  }
  return null
}

export const prettyId = (id: string) => id.replace(/-/g, ' ')
