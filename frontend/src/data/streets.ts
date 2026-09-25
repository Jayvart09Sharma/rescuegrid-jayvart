// Street routing on the twin's grid. The seed city is a 10-unit grid of streets from -50 to 50 on both axes; the
// river (z 31..39) is only crossed by the two bridges. Routes between points follow streets, never cut through
// blocks, and avoid road segments whose graph status is 'danger' (blocked). Unit positions from GPS are map-matched
// to the nearest street, as any navigation display does.
import { GRID, HALF, RIVER } from './seed'
import type { GraphNode } from '../types'

const LINES: number[] = []
for (let v = -HALF; v <= HALF; v += GRID) LINES.push(v)
const BRIDGES = new Set([-20, 20]) // x of the two bridges (seed.ts BRIDGE_X)

const key = (x: number, z: number) => `${x},${z}`

/** Nearest point on the street grid to (x, z): stay on the closer of the two grid lines through the block. */
export function snapToStreet(x: number, z: number): [number, number] {
  const cx = Math.max(-HALF, Math.min(HALF, x))
  const cz = Math.max(-HALF, Math.min(HALF, z))
  const nx = Math.round(cx / GRID) * GRID
  const nz = Math.round(cz / GRID) * GRID
  // a block interior: snap the axis that is closer to a street line, keep the other coordinate along that street
  return Math.abs(cx - nx) <= Math.abs(cz - nz) ? [nx, cz] : [cx, nz]
}

/** Segment id for the street piece between two adjacent grid points (order-independent). */
function segKey(a: [number, number], b: [number, number]) {
  return a[0] < b[0] || (a[0] === b[0] && a[1] < b[1]) ? `${key(a[0], a[1])}|${key(b[0], b[1])}` : `${key(b[0], b[1])}|${key(a[0], a[1])}`
}

/** Blocked street pieces from the graph mirror: road nodes with status 'danger' and their a/b endpoints (10-unit segments). */
export function blockedSegments(nodes: Record<string, GraphNode>): Set<string> {
  const out = new Set<string>()
  for (const n of Object.values(nodes)) {
    if ((n.label !== 'Road' && n.label !== 'Bridge') || n.status !== 'danger') continue
    const a = n.props.a as number[] | undefined
    const b = n.props.b as number[] | undefined
    if (!a || !b) continue
    out.add(segKey([a[0], a[1]], [b[0], b[1]]))
  }
  return out
}

function neighbours(x: number, z: number): [number, number][] {
  const out: [number, number][] = []
  for (const [dx, dz] of [[GRID, 0], [-GRID, 0], [0, GRID], [0, -GRID]] as [number, number][]) {
    const nx = x + dx
    const nz = z + dz
    if (nx < -HALF || nx > HALF || nz < -HALF || nz > HALF) continue
    // crossing the river is only possible on a bridge
    const crosses = (z <= RIVER.z0 && nz >= RIVER.z1) || (z >= RIVER.z1 && nz <= RIVER.z0)
    if (crosses && !BRIDGES.has(x)) continue
    out.push([nx, nz])
  }
  return out
}

/** A* over grid intersections from a to b (both snapped to intersections), avoiding blocked segments when possible. */
function astar(a: [number, number], b: [number, number], blocked: Set<string>): [number, number][] | null {
  const h = (p: [number, number]) => Math.abs(p[0] - b[0]) + Math.abs(p[1] - b[1])
  const open = new Map<string, { p: [number, number]; g: number; f: number }>()
  const came = new Map<string, string>()
  const gScore = new Map<string, number>()
  const start = key(a[0], a[1])
  open.set(start, { p: a, g: 0, f: h(a) })
  gScore.set(start, 0)
  const pts = new Map<string, [number, number]>([[start, a]])
  while (open.size) {
    let cur: { p: [number, number]; g: number; f: number } | null = null
    let curK = ''
    for (const [k, v] of open) if (!cur || v.f < cur.f) { cur = v; curK = k }
    if (!cur) break
    if (cur.p[0] === b[0] && cur.p[1] === b[1]) {
      const path: [number, number][] = [cur.p]
      let k = curK
      while (came.has(k)) { k = came.get(k)!; path.push(pts.get(k)!) }
      return path.reverse()
    }
    open.delete(curK)
    for (const n of neighbours(cur.p[0], cur.p[1])) {
      const nk = key(n[0], n[1])
      const cost = blocked.has(segKey(cur.p, n)) ? 1000 : GRID
      const g = cur.g + cost
      if (g < (gScore.get(nk) ?? Infinity)) {
        gScore.set(nk, g)
        came.set(nk, curK)
        pts.set(nk, n)
        open.set(nk, { p: n, g, f: g + h(n) })
      }
    }
  }
  return null
}

/** Street route through a list of waypoints (any positions): snapped onto streets, joined intersection by intersection. */
export function streetRoute(waypoints: [number, number][], nodes: Record<string, GraphNode>): [number, number][] {
  if (waypoints.length < 2) return waypoints
  const blocked = blockedSegments(nodes)
  const out: [number, number][] = []
  const push = (p: [number, number]) => { const l = out[out.length - 1]; if (!l || l[0] !== p[0] || l[1] !== p[1]) out.push(p) }
  const toIntersection = (p: [number, number]): [number, number] => [Math.round(p[0] / GRID) * GRID, Math.round(p[1] / GRID) * GRID]
  for (let i = 0; i < waypoints.length - 1; i++) {
    const a = snapToStreet(waypoints[i][0], waypoints[i][1])
    const b = snapToStreet(waypoints[i + 1][0], waypoints[i + 1][1])
    push(a)
    const ia = toIntersection(a)
    const ib = toIntersection(b)
    const mid = astar(ia, ib, blocked) ?? [ia, ib]
    for (const p of mid) push(p)
    push(b)
  }
  return out
}
