// Static seed graph: the city as Neo4j holds it at startup (buildings, roads,
// bridges, facilities, sensors, units). Deterministic so every run and every
// seek lands on identical geometry. In live mode this comes from GET /graph.
import type { GraphNode, NodeLabel, Status } from '../types'
import { BASE_ISO } from './clock'

export const GRID = 10
export const HALF = 50
/** River runs east–west between these z values; only bridges cross it. */
export const RIVER = { z0: 31, z1: 39 }

export const V_STREETS = [
  'Bay Rd', 'Elm Ave', 'Ash Ave', 'Oak Ave', 'Birch Ave', 'Center Ave',
  'Maple Ave', 'Walnut Ave', 'Cherry Ave', 'Spruce Ave', 'Hill Rd',
]
export const H_STREETS = [
  'North Rd', 'Pine St', 'Cedar St', 'Market St', 'Mission St', 'Main St',
  '1st St', '2nd St', 'River Rd', 'Harbor Rd', 'Port Rd',
]
const BRIDGE_X: Record<number, string> = { [-20]: 'Oak-Bridge', 20: 'Walnut-Bridge' }

const slug = (s: string) => s.replace(/\s+/g, '-')

function rng(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6d2b79f5) >>> 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function node(id: string, label: NodeLabel, props: Record<string, unknown>, status: Status = 'normal'): GraphNode {
  return {
    id,
    label,
    status,
    since: BASE_ISO,
    lastConfirmed: BASE_ISO,
    sources: [{ source: 'seed', timestamp: BASE_ISO, confidence: 1 }],
    props,
  }
}

/** Blocks reserved for named facilities (block centre → node). */
const RESERVED: Record<string, () => GraphNode> = {
  '35,-5': () => node('Hospital-Valley', 'Hospital', { x: 35, z: -5, name: 'Valley Medical Center', beds: 38, w: 8, d: 8, h: 7 }, 'safe'),
  '-25,-35': () => node('Hospital-StMary', 'Hospital', { x: -25, z: -35, name: "St. Mary's Hospital", beds: 12, w: 7, d: 7, h: 6 }, 'safe'),
  '-15,25': () => node('Shelter-Lincoln', 'Shelter', { x: -15, z: 25, name: 'Lincoln HS Shelter', capacity: 600, occupancy: 0.41 }, 'safe'),
  '-35,5': () => node('Staging-A', 'StagingArea', { x: -35, z: 5, name: 'Staging Area A · IC' }, 'safe'),
  '-5,15': () => node('Park-Center', 'StagingArea', { x: -5, z: 15, name: 'Civic Park', park: true }, 'normal'),
  '25,25': () => node('Park-East', 'StagingArea', { x: 25, z: 25, name: 'Eastside Green', park: true }, 'normal'),
}

export function buildSeed(): GraphNode[] {
  const out: GraphNode[] = []
  const r = rng(1406)
  let n = 1
  const nextId = () => {
    if (n === 14 || n === 15) n = 16
    return `Building-${n++}`
  }

  for (let bx = -HALF + GRID / 2; bx < HALF; bx += GRID) {
    for (let bz = -HALF + GRID / 2; bz < HALF; bz += GRID) {
      if (bz > RIVER.z0 && bz < RIVER.z1) continue
      const key = `${bx},${bz}`
      if (RESERVED[key]) {
        out.push(RESERVED[key]())
        continue
      }
      if (key === '5,-5') {
        out.push(node('Building-14', 'Building', { x: 5, z: -5, w: 6.4, d: 6.4, h: 11, addr: '140 Main St', occupancy: 'office · ~60' }))
        continue
      }
      if (key === '15,-5') {
        out.push(node('Building-15', 'Building', { x: 15, z: -5, w: 6, d: 5.5, h: 8, addr: '220 Mission St', occupancy: 'residential · ~35' }))
        continue
      }
      // Downtown core is taller; edges of the map are low-rise.
      const dist = Math.hypot(bx, bz) / HALF
      const tall = Math.max(0.15, 1 - dist)
      const split = r()
      const lots: [number, number, number, number][] =
        split < 0.45
          ? [[bx, bz, 7, 7]]
          : split < 0.75
            ? [[bx - 2, bz, 3.2, 7], [bx + 2, bz, 3.2, 7]]
            : [[bx - 2, bz - 2, 3.2, 3.2], [bx + 2, bz - 2, 3.2, 3.2], [bx - 2, bz + 2, 3.2, 3.2], [bx + 2, bz + 2, 3.2, 3.2]]
      for (const [x, z, w, d] of lots) {
        const h = 1.5 + r() * 3 + tall * tall * r() * 22
        out.push(node(nextId(), 'Building', { x, z, w: w * (0.8 + r() * 0.2), d: d * (0.8 + r() * 0.2), h }))
      }
    }
  }

  // Road segments: one node per street between two intersections.
  for (let i = 0; i < V_STREETS.length; i++) {
    const x = -HALF + i * GRID
    for (let j = 0; j < 10; j++) {
      const z0 = -HALF + j * GRID
      const z1 = z0 + GRID
      const crossesRiver = z0 === 30
      if (crossesRiver && !BRIDGE_X[x]) continue
      const id = crossesRiver ? BRIDGE_X[x] : `${slug(V_STREETS[i])}-${j}`
      out.push(
        node(id, crossesRiver ? 'Bridge' : 'Road', {
          a: [x, z0],
          b: [x, z1],
          name: crossesRiver ? `${V_STREETS[i]} bridge` : `${V_STREETS[i]} (${H_STREETS[j]}–${H_STREETS[j + 1]})`,
          width: 2.2,
        }),
      )
    }
  }
  for (let j = 0; j < H_STREETS.length; j++) {
    const z = -HALF + j * GRID
    for (let i = 0; i < 10; i++) {
      const x0 = -HALF + i * GRID
      out.push(
        node(`${slug(H_STREETS[j])}-${i}`, 'Road', {
          a: [x0, z],
          b: [x0 + GRID, z],
          name: `${H_STREETS[j]} (${V_STREETS[i]}–${V_STREETS[i + 1]})`,
          width: H_STREETS[j] === 'Main St' ? 3.2 : 2.2,
        }),
      )
    }
  }

  out.push(
    node('Sensor-Seismic-1', 'Sensor', { x: -6, z: -26, kind: 'seismic', reading: '0.02 g' }),
    node('Sensor-Gas-3', 'Sensor', { x: 11, z: -3, kind: 'gas', reading: 'CH4 12 ppm' }),
    node('Sensor-Water-2', 'Sensor', { x: 22, z: 29, kind: 'water', reading: '0.6 m' }),
  )

  out.push(
    node('Rescue-Team-4', 'Unit', { x: -35, z: -10, kind: 'rescue', callsign: 'Rescue Team 4', crew: 6 }, 'safe'),
    node('Ambulance-2', 'Unit', { x: -35, z: 0, kind: 'ambulance', callsign: 'Ambulance 2', crew: 2 }, 'safe'),
    node('Engine-7', 'Unit', { x: 45, z: -30, kind: 'engine', callsign: 'Engine 7', crew: 4 }, 'safe'),
    node('Medic-5', 'Unit', { x: -25, z: -30, kind: 'ambulance', callsign: 'Medic 5', crew: 2 }, 'safe'),
    node('Drone-1', 'Unit', { x: -35, z: 5, kind: 'drone', callsign: 'Drone 1' }, 'safe'),
    node('Drone-2', 'Unit', { x: -35, z: 5, kind: 'drone', callsign: 'Drone 2' }, 'safe'),
    node('Drone-3', 'Unit', { x: 35, z: -5, kind: 'drone', callsign: 'Drone 3' }, 'safe'),
  )

  return out
}
