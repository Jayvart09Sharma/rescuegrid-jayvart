// Pre-baked Q&A fallback. Answers are computed from the graph mirror (never
// from the 3D scene) and returned in the same FlyToSignal shape Kenil's
// local-LLM layer returns in live mode.
import { useStore, prettyId } from '../store'
import { BASE_MS, fmtClock } from './clock'
import type { FlyToSignal, GraphNode } from '../types'

export const PRESETS = [
  "Who's in the most danger?",
  'Can Ambulance 2 still reach the hospital?',
  'What changed in the last 5 minutes?',
  'Any conflicting reports?',
  'Show Building 14',
]

const conf = (n: GraphNode) => {
  const s = n.sources.filter((x) => x.source !== 'seed')
  return s.length ? s.map((x) => `${x.source} ${Math.round(x.confidence * 100)}%`).join(' + ') : 'seed data'
}

export function answerLocally(q: string): FlyToSignal {
  const { nodes, edges, suggestions, approvals, t } = useStore.getState()
  const text = q.toLowerCase()
  const edgeList = Object.values(edges)

  if (/danger|risk|safe|threat/.test(text)) {
    const atRisk = edgeList.filter((e) => e.type === 'AT_RISK')
    if (atRisk.length) {
      const e = atRisk[0]
      const hz = nodes[e.to]
      return {
        target: e.from,
        highlight: [e.from, e.to, 'Building-14'],
        answer: `${prettyId(e.from)}: on scene at Building-14 and inside ${prettyId(e.to)} (CH4 ${hz?.props.ppm ?? '?'} ppm, radius ${hz?.props.r ?? '?'} m) since ${fmtClock(e.since)}. Sources: gps fix + gas-3 sensor. IC should consider withdrawal to Mission St.`,
        cypher: 'MATCH (u:Unit)-[r:AT_RISK]->(h:Hazard)\nRETURN u.id, h.id, h.ppm, r.since\nORDER BY h.ppm DESC LIMIT 3',
      }
    }
    const b14 = nodes['Building-14']
    return {
      target: 'Rescue-Team-4',
      highlight: ['Rescue-Team-4', 'Building-14'],
      answer: `No unit is inside a hazard zone yet. Closest to danger: Rescue Team 4, assigned to Building-14 (${b14?.status === 'danger' ? 'collapsed' : 'standing'}).`,
      cypher: "MATCH (u:Unit)-[:ASSIGNED_TO|NEAR]->(b)\nWHERE b.status = 'danger'\nRETURN u.id, b.id",
    }
  }

  if (/ambulance|hospital|reach|route/.test(text)) {
    const main = nodes['Main-St-5']
    const sg = suggestions.find((s) => s.unit === 'Ambulance-2')
    const approved = sg && approvals[sg.id] !== undefined
    const target = main?.status === 'danger' ? 'Main-St-5' : 'Ambulance-2'
    let answer: string
    if (main?.status !== 'danger') answer = 'Yes. Main St is clear to Valley Medical Center, direct route, ~70 s.'
    else if (approved) answer = 'Yes, on the approved detour via 1st St → Walnut Ave. Main St stays blocked.'
    else if (sg) answer = `Not via Main St, which has been blocked at Center–Maple since ${fmtClock(main.since)} (${conf(main)}). A detour via 1st St is suggested and waiting for IC approval.`
    else answer = `Not via Main St, which has been blocked since ${fmtClock(main.since)}. Computing a detour.`
    return {
      target,
      highlight: ['Ambulance-2', 'Main-St-5', 'Hospital-Valley'],
      answer,
      cypher: "MATCH p = shortestPath((u:Unit {id:'Ambulance-2'})-[:CONNECTS*]-(h:Hospital {id:'Hospital-Valley'}))\nWHERE NONE(r IN nodes(p) WHERE r.status = 'danger')\nRETURN p",
    }
  }

  if (/change|last|minutes|new|update/.test(text)) {
    const cutoff = t - 300
    const changed = Object.values(nodes)
      .filter((n) => n.status !== 'normal' && n.status !== 'safe' && n.sources.some((s) => s.source !== 'seed'))
      .filter((n) => (Date.parse(n.since) - BASE_MS) / 1000 >= cutoff)
      .sort((a, b) => Date.parse(b.since) - Date.parse(a.since))
    const list = changed.slice(0, 5).map((n) => `${prettyId(n.id)} → ${n.status} (${fmtClock(n.since)})`)
    return {
      target: changed[0]?.id ?? 'Building-14',
      highlight: changed.map((n) => n.id),
      answer: changed.length ? `${changed.length} state changes: ${list.join('; ')}.` : 'No state changes in the last 5 minutes.',
      cypher: "MATCH (n) WHERE n.since > datetime() - duration('PT5M')\nAND n.status <> 'normal'\nRETURN n.id, n.status, n.since ORDER BY n.since DESC",
    }
  }

  if (/conflict|disagree|bridge/.test(text)) {
    const c = Object.values(nodes).filter((n) => n.status === 'conflict')
    if (c.length) {
      const n = c[0]
      return {
        target: n.id,
        highlight: c.map((x) => x.id),
        answer: `${prettyId(n.id)} has conflicting reports: ${(n.props.conflict as string[]).join(' vs ')}. Not auto-resolved. Needs eyes-on confirmation.`,
        cypher: "MATCH (n {status:'conflict'})<-[:REPORTS]-(e:Event)\nRETURN n.id, e.source, e.claim, e.confidence",
      }
    }
    return { target: 'Oak-Bridge', highlight: [], answer: 'No conflicting reports in the graph right now.' }
  }

  const m = text.match(/building\s*-?\s*(\d+)/)
  if (m) {
    const id = `Building-${m[1]}`
    const n = nodes[id]
    if (n) {
      return {
        target: id,
        highlight: [id],
        answer: `${id}: ${n.status.toUpperCase()} since ${fmtClock(n.since)} · ${String(n.props.addr ?? '')} · ${conf(n)}.`,
        cypher: `MATCH (b:Building {id:'${id}'}) RETURN b`,
      }
    }
  }

  return {
    target: 'Building-14',
    highlight: [],
    answer: 'That question is not in the offline fallback set. Try one of the preset queries (the live LLM path handles free-form questions).',
  }
}
