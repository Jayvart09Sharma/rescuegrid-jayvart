// Frontend mirror of the master scenario timeline (owner: Shresth).
// Each entry is: the contract event a feed adapter emits, plus the graph writes
// the fusion agent makes from it. The replay source applies both off one clock.
import type { GraphPatch, LoggedEvent, Suggestion } from '../types'

export type ScenarioEvent = Omit<LoggedEvent, 'id' | 't' | 'timestamp'>

export interface ScenarioEntry {
  t: number
  event?: ScenarioEvent
  patches?: GraphPatch[]
  suggestion?: Omit<Suggestion, 'state' | 'createdAt'>
}

/** Waypoints [t, x, z] a GPS replay adapter would publish along. */
export interface Motion {
  unit: string
  waypoints: [number, number, number][]
}

export const SCENARIO_LENGTH = 130

export const MOTIONS: Motion[] = [
  { unit: 'Rescue-Team-4', waypoints: [[4, -35, -10], [60, 4, -10]] },
  { unit: 'Ambulance-2', waypoints: [[3, -35, 0], [33, -7, 0]] },
  { unit: 'Engine-7', waypoints: [[10, 45, -30], [30, 20, -30], [45, 20, -12], [52, 17, -10]] },
]

/** Build [t,x,z] waypoints along a polyline at constant speed. */
function flight(points: [number, number][], t0: number, speed: number): [number, number, number][] {
  const out: [number, number, number][] = [[t0, points[0][0], points[0][1]]]
  let t = t0
  for (let i = 1; i < points.length; i++) {
    t += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]) / speed
    out.push([t, points[i][0], points[i][1]])
  }
  return out
}

/** Circle a point at radius r, starting at angle 0, until tEnd. */
function orbit(cx: number, cz: number, r: number, t0: number, tEnd: number, period: number): [number, number, number][] {
  const out: [number, number, number][] = []
  for (let t = t0; t <= tEnd + 1; t += 0.5) {
    const a = ((t - t0) / period) * Math.PI * 2
    out.push([t, cx + Math.cos(a) * r, cz + Math.sin(a) * r])
  }
  return out
}

function survey(points: [number, number][], t0: number, speed: number, hold: [number, number, number]) {
  const legs = flight(points, t0, speed)
  const tEnd = legs[legs.length - 1][0]
  const [hx, hz, hr] = hold
  const approach = flight([points[points.length - 1], [hx + hr, hz]], tEnd, speed)
  return [...legs, ...approach.slice(1), ...orbit(hx, hz, hr, approach[approach.length - 1][0] + 0.5, 140, 26)]
}

/** Drone flight plans. Drone 1 goes straight to the incident core, 2 and 3 survey the city. */
export const DRONE_MOTIONS: Motion[] = [
  {
    unit: 'Drone-1',
    waypoints: [...flight([[-35, 5], [16, -5]], 0, 6.5), ...orbit(5, -5, 11, 8.3, 140, 24)],
  },
  {
    unit: 'Drone-2',
    waypoints: survey(
      [[-35, 5], [-46, -42], [-4, -42], [-4, -26], [-46, -26], [-46, -10], [-4, -10], [-4, 6], [-46, 6], [-46, 22], [-4, 22], [-4, 45], [-46, 45]],
      0, 6.5, [-20, 35, 8],
    ),
  },
  {
    unit: 'Drone-3',
    waypoints: survey(
      [[35, -5], [4, -46], [46, -46], [46, -30], [4, -30], [4, -14], [46, -14], [46, 2], [4, 2], [4, 18], [46, 18], [46, 45], [4, 45]],
      0, 6.5, [20, 35, 8],
    ),
  },
]

/** Radius of each drone camera's ground footprint (world units). */
export const DRONE_FOOTPRINT = 10
export const DRONE_ALT = 22

/** Detour the correlation agent proposes once Main St is blocked. */
export const REROUTE_PATH: [number, number][] = [
  [-7, 0], [-10, 0], [-10, 10], [20, 10], [20, 0], [35, 0],
]
export const ORIGINAL_ROUTE: [number, number][] = [[-35, 0], [35, 0]]

export const BEATS = [
  { id: 'quake', label: 'QUAKE', t: 0 },
  { id: 'collapse', label: 'COLLAPSE', t: 15 },
  { id: 'block', label: 'ROAD BLOCK', t: 32 },
  { id: 'reroute', label: 'REROUTE', t: 38 },
  { id: 'hazard', label: 'GAS HAZARD', t: 90 },
  { id: 'conflict', label: 'CONFLICT', t: 104 },
] as const

export const SCENARIO: ScenarioEntry[] = [
  {
    t: 0,
    event: {
      source: 'sensor', entity: 'Sensor-Seismic-1', claim: 'seismic_event M5.8', confidence: 0.99,
      raw_evidence_ref: 'sensor/seismic-1/reading-000000.json', severity: 'danger',
      title: 'M5.8 seismic event, downtown sector', detail: 'PGA 0.41 g · epicenter 6.2 km NW · depth 9 km',
    },
    patches: [
      { op: 'upsertNode', node: { id: 'Incident-EQ-1', label: 'Incident', status: 'danger', props: { x: -30, z: -62, mag: 5.8 } } },
      { op: 'setStatus', id: 'Sensor-Seismic-1', status: 'danger' },
      { op: 'setProps', id: 'Sensor-Seismic-1', props: { reading: '0.41 g' } },
    ],
  },
  {
    t: 4,
    event: {
      source: 'field_report', entity: 'Staging-A', claim: 'ic_established', confidence: 1,
      raw_evidence_ref: 'reports/fr-0004.txt', severity: 'safe',
      title: 'IC established at Staging Area A', detail: 'IC established at Staging Area A. Rescue Team 4 and Ambulance 2 rolling.',
    },
    patches: [{ op: 'setProps', id: 'Ambulance-2', props: { route: [[-35, 0], [35, 0]], dest: 'Hospital-Valley' } }],
  },
  {
    t: 9,
    event: {
      source: 'field_report', entity: 'Building-15', claim: 'structural_damage_reported', confidence: 0.6,
      raw_evidence_ref: 'reports/fr-0009.txt', severity: 'warning',
      title: 'Facade cracks reported, Building-15', detail: 'Caller reports large cracks in the facade at 220 Mission St, residents self-evacuating.',
    },
    patches: [{ op: 'setStatus', id: 'Building-15', status: 'warning' }],
  },
  {
    t: 15,
    event: {
      source: 'drone_vision', entity: 'Building-14', claim: 'collapsed', confidence: 0.94,
      raw_evidence_ref: 'frames/drone-1/f000450.jpg', severity: 'danger',
      title: 'Building-14 collapse', detail: 'Drone 1 · structure_collapse bbox [412,188,731,502]',
    },
    patches: [
      { op: 'setStatus', id: 'Building-14', status: 'danger' },
      { op: 'setProps', id: 'Building-14', props: { collapsed: true } },
    ],
  },
  {
    t: 22,
    event: {
      source: 'radio_asr', entity: 'Rescue-Team-4', claim: 'en_route Building-14', confidence: 0.91,
      raw_evidence_ref: 'audio/radio/ch3-0022.wav', severity: 'safe',
      title: 'Rescue Team 4 en route to Building-14', detail: 'Dispatch, Rescue Four, en route to Building Fourteen, E-T-A two minutes.',
    },
    patches: [{ op: 'addEdge', edge: { id: 'e-rt4-b14', from: 'Rescue-Team-4', to: 'Building-14', type: 'ASSIGNED_TO' } }],
  },
  {
    t: 32,
    event: {
      source: 'radio_asr', entity: 'Main-St-5', claim: 'blocked', confidence: 0.88,
      raw_evidence_ref: 'audio/radio/ch3-0032.wav', severity: 'danger',
      title: 'Main St blocked at Center–Maple', detail: 'Large debris blocking Main Street near Building Fourteen, no through traffic.',
    },
    patches: [
      { op: 'setStatus', id: 'Main-St-5', status: 'danger' },
      { op: 'addEdge', edge: { id: 'e-b14-main5', from: 'Building-14', to: 'Main-St-5', type: 'BLOCKS' } },
    ],
  },
  {
    t: 36,
    event: {
      source: 'drone_vision', entity: 'Main-St-5', claim: 'blocked_debris', confidence: 0.92,
      raw_evidence_ref: 'frames/drone-1/f000540.jpg', severity: 'danger',
      title: 'Drone confirms Main St debris field', detail: 'Drone 1 · road_obstruction bbox [120,340,880,610]',
    },
    patches: [{ op: 'setStatus', id: 'Main-St-5', status: 'danger' }],
  },
  {
    t: 38,
    suggestion: {
      id: 'sg-amb2-reroute', kind: 'reroute', unit: 'Ambulance-2',
      reason: 'Main St blocked at Center–Maple (radio 0.88 + drone 0.92). Detour via 1st St → Walnut Ave keeps Valley Medical reachable, +40 s ETA.',
      path: [
        [-7, 0], [-10, 0], [-10, 10], [20, 10], [20, 0], [35, 0],
      ],
    },
  },
  {
    t: 45,
    event: {
      source: 'drone_vision', entity: 'Building-15', claim: 'partial_damage', confidence: 0.71,
      raw_evidence_ref: 'frames/drone-1/f000780.jpg', severity: 'warning',
      title: 'Building-15 partial damage confirmed', detail: 'Drone 1 · facade_damage bbox [300,120,540,470]',
    },
    patches: [{ op: 'setStatus', id: 'Building-15', status: 'warning' }],
  },
  {
    t: 52,
    event: {
      source: 'gps', entity: 'Engine-7', claim: 'on_scene Building-15', confidence: 0.99,
      raw_evidence_ref: 'gps/engine-7/fix-0052.json', severity: 'safe',
      title: 'Engine 7 on scene, Building-15',
    },
    patches: [{ op: 'addEdge', edge: { id: 'e-e7-b15', from: 'Engine-7', to: 'Building-15', type: 'NEAR' } }],
  },
  {
    t: 56,
    event: {
      source: 'sensor', entity: 'Sensor-Water-2', claim: 'water_level_rising 1.8m', confidence: 0.97,
      raw_evidence_ref: 'sensor/water-2/reading-000056.json', severity: 'warning',
      title: 'River level rising, 1.8 m', detail: 'Gauge +1.2 m in 50 s · watch Walnut Ave bridge',
    },
    patches: [
      { op: 'setStatus', id: 'Sensor-Water-2', status: 'warning' },
      { op: 'setProps', id: 'Sensor-Water-2', props: { reading: '1.8 m' } },
      { op: 'setStatus', id: 'Walnut-Bridge', status: 'warning' },
    ],
  },
  {
    t: 62,
    event: {
      source: 'gps', entity: 'Rescue-Team-4', claim: 'on_scene Building-14', confidence: 0.99,
      raw_evidence_ref: 'gps/rescue-team-4/fix-0062.json', severity: 'safe',
      title: 'Rescue Team 4 on scene, Building-14',
    },
    patches: [{ op: 'addEdge', edge: { id: 'e-rt4-near-b14', from: 'Rescue-Team-4', to: 'Building-14', type: 'NEAR' } }],
  },
  {
    t: 70,
    event: {
      source: 'field_report', entity: 'Shelter-Lincoln', claim: 'capacity 82%', confidence: 0.9,
      raw_evidence_ref: 'reports/fr-0070.txt', severity: 'warning',
      title: 'Lincoln HS shelter at 82% capacity', detail: 'Shelter manager: 492 of 600 cots in use, requesting overflow site.',
    },
    patches: [
      { op: 'setStatus', id: 'Shelter-Lincoln', status: 'warning' },
      { op: 'setProps', id: 'Shelter-Lincoln', props: { occupancy: 0.82 } },
    ],
  },
  {
    t: 90,
    event: {
      source: 'sensor', entity: 'Sensor-Gas-3', claim: 'gas_leak CH4 4200ppm', confidence: 0.96,
      raw_evidence_ref: 'sensor/gas-3/reading-000090.json', severity: 'danger',
      title: 'Gas leak, CH4 4200 ppm near Building-14', detail: 'CH4 12 → 4200 ppm in 6 s · LEL 8.4%',
    },
    patches: [
      { op: 'setStatus', id: 'Sensor-Gas-3', status: 'danger' },
      { op: 'setProps', id: 'Sensor-Gas-3', props: { reading: 'CH4 4200 ppm' } },
      { op: 'upsertNode', node: { id: 'Hazard-Gas-1', label: 'Hazard', status: 'danger', props: { x: 11, z: -4, r: 9, kind: 'gas', ppm: 4200 } } },
    ],
  },
  {
    t: 94,
    event: {
      source: 'fusion', entity: 'Rescue-Team-4', claim: 'AT_RISK Hazard-Gas-1', confidence: 0.93,
      raw_evidence_ref: 'graph/fusion/at-risk-0094.json', severity: 'danger',
      title: 'Rescue Team 4 inside gas hazard radius', detail: 'Correlated: gps fix-0062 (unit at 4,-10) + sensor gas-3 (radius 9 m)',
    },
    patches: [
      { op: 'addEdge', edge: { id: 'e-rt4-risk-gas', from: 'Rescue-Team-4', to: 'Hazard-Gas-1', type: 'AT_RISK' } },
      { op: 'setStatus', id: 'Rescue-Team-4', status: 'danger' },
    ],
  },
  {
    t: 100,
    event: {
      source: 'radio_asr', entity: 'Oak-Bridge', claim: 'open', confidence: 0.84,
      raw_evidence_ref: 'audio/radio/ch3-0100.wav', severity: 'normal',
      title: 'Radio: Oak Ave bridge open', detail: 'Oak Avenue bridge is open, traffic moving both ways.',
    },
  },
  {
    t: 104,
    event: {
      source: 'drone_vision', entity: 'Oak-Bridge', claim: 'debris_blocking', confidence: 0.87,
      raw_evidence_ref: 'frames/drone-2/f003120.jpg', severity: 'conflict',
      title: 'Conflicting reports, Oak Ave bridge', detail: 'Drone 2 sees debris on the deck; radio (t+100) says open. Not auto-resolved.',
    },
    patches: [
      { op: 'setStatus', id: 'Oak-Bridge', status: 'conflict' },
      { op: 'setProps', id: 'Oak-Bridge', props: { conflict: ['radio_asr: open (0.84)', 'drone_vision: debris_blocking (0.87)'] } },
    ],
  },
  {
    t: 116,
    event: {
      source: 'field_report', entity: 'Sensor-Gas-3', claim: 'utility_requested', confidence: 1,
      raw_evidence_ref: 'reports/fr-0116.txt', severity: 'warning',
      title: 'PG&E crew requested for Mission St gas main', detail: 'IC requests utility shutoff at Mission St valve 3.',
    },
  },
]
