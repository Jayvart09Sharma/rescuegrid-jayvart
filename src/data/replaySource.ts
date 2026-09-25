// Replay adapter: plays the scenario timeline into the store off one clock,
// exactly as the backend replay adapters feed Neo4j. Same store API the live
// source uses, so the UI can't tell which one is feeding it.
import { useStore } from '../store'
import { isoAt } from './clock'
import { DRONE_MOTIONS, MOTIONS, SCENARIO, SCENARIO_LENGTH, type ScenarioEntry } from './scenario'
import type { GraphPatch, LoggedEvent, Provenance } from '../types'

const REROUTE_ID = 'sg-amb2-reroute'
const AMB_SPEED = 1.8 // world units / scenario second
const GPS_HZ = 5

let cursor = 0
let lastGps = -1
let routeApplied = false

function applyEntry(en: ScenarioEntry, silent: boolean) {
  const s = useStore.getState()
  if (en.event) {
    const timestamp = isoAt(en.t)
    const id = `ev-${String(en.t).padStart(3, '0')}-${en.event.entity}`
    const e: LoggedEvent = { ...en.event, id, t: en.t, timestamp }
    const prov: Provenance = {
      eventId: id, source: e.source, timestamp, confidence: e.confidence,
      raw_evidence_ref: e.raw_evidence_ref, claim: e.claim,
    }
    s.logEvent(e, silent)
    const patches: GraphPatch[] = en.patches ?? [{ op: 'setProps', id: e.entity, props: {} }]
    s.applyPatches(patches, prov)
  } else if (en.patches) {
    s.applyPatches(en.patches)
  }
  if (en.suggestion) {
    s.addSuggestion({ ...en.suggestion, state: 'pending', createdAt: isoAt(en.t) }, silent)
  }
}

function applyUpTo(t: number, silent: boolean) {
  while (cursor < SCENARIO.length && SCENARIO[cursor].t <= t) {
    applyEntry(SCENARIO[cursor], silent)
    cursor++
  }
}

function along(path: [number, number][], dist: number): [number, number, boolean] {
  let left = dist
  for (let i = 0; i < path.length - 1; i++) {
    const [ax, az] = path[i]
    const [bx, bz] = path[i + 1]
    const len = Math.hypot(bx - ax, bz - az)
    if (left <= len) {
      const k = left / len
      return [ax + (bx - ax) * k, az + (bz - az) * k, false]
    }
    left -= len
  }
  const end = path[path.length - 1]
  return [end[0], end[1], true]
}

export function interp(wp: [number, number, number][], t: number): [number, number] {
  if (t <= wp[0][0]) return [wp[0][1], wp[0][2]]
  for (let i = 0; i < wp.length - 1; i++) {
    const [t0, x0, z0] = wp[i]
    const [t1, x1, z1] = wp[i + 1]
    if (t <= t1) {
      const k = (t - t0) / (t1 - t0)
      return [x0 + (x1 - x0) * k, z0 + (z1 - z0) * k]
    }
  }
  const last = wp[wp.length - 1]
  return [last[1], last[2]]
}

/** Stand-in for the GPS replay adapter: publishes unit fixes at GPS_HZ. */
function publishGps(t: number) {
  const s = useStore.getState()
  const patches: GraphPatch[] = []
  for (const m of MOTIONS) {
    let [x, z] = interp(m.waypoints, t)
    const props: Record<string, unknown> = { x, z }
    if (m.unit === 'Ambulance-2') {
      const sg = s.suggestions.find((g) => g.id === REROUTE_ID)
      const approvedAt = s.approvals[REROUTE_ID]
      if (sg && approvedAt !== undefined) {
        const [ax, az, arrived] = along(sg.path, (t - approvedAt) * AMB_SPEED)
        x = ax
        z = az
        props.x = x
        props.z = z
        props.state = arrived ? 'AT VALLEY MEDICAL' : 'EN ROUTE · APPROVED DETOUR'
        if (!routeApplied) {
          routeApplied = true
          props.route = sg.path
        }
      } else if (t >= 33 && s.nodes['Main-St-5']?.status === 'danger') {
        props.state = 'HOLDING · AWAITING IC'
      } else if (t >= 3) {
        props.state = 'EN ROUTE · VALLEY MEDICAL'
      }
    }
    patches.push({ op: 'setProps', id: m.unit, props })
  }
  for (const m of DRONE_MOTIONS) {
    const [x, z] = interp(m.waypoints, t)
    patches.push({ op: 'setProps', id: m.unit, props: { x, z } })
  }
  s.applyPatches(patches)
  useStore.setState({ feedPulse: { ...s.feedPulse, gps: (s.feedPulse.gps ?? 0) + 1 } })
}

export const replay = {
  length: SCENARIO_LENGTH,

  /** Rebuild graph state as of scenario time t, with no alerts fired. */
  seek(t: number) {
    const s = useStore.getState()
    s.resetGraph()
    cursor = 0
    routeApplied = false
    applyUpTo(t, true)
    publishGps(t)
    lastGps = t
    s.setClock(t)
  },

  /** Advance by dt scenario seconds. */
  tick(dt: number) {
    const s = useStore.getState()
    if (!s.playing) return
    let t = s.t + dt
    if (t >= SCENARIO_LENGTH) {
      t = SCENARIO_LENGTH
      s.setPlaying(false)
    }
    applyUpTo(t, false)
    if (t - lastGps >= 1 / GPS_HZ || t < lastGps) {
      publishGps(t)
      lastGps = t
    }
    s.setClock(t)
  },

  start() {
    this.seek(-4)
    let last = performance.now()
    let raf = 0
    const loop = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000)
      last = now
      this.tick(dt * useStore.getState().speed)
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  },
}
