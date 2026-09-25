// Fact overlays that sit on top of the particle twin: hazards, blocked roads,
// routes, event pings, Q&A highlights, and the scanned-ground shader.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useFrame } from '@react-three/fiber'
import { Html, Line } from '@react-three/drei'
import * as THREE from 'three'
import type { Line2 } from 'three-stdlib'
import { nodePos, prettyId, useStore } from '../store'
import { BASE_MS } from '../data/clock'
import { STATUS_HEX } from '../config'
import { coverageTexture, COVER_EXTENT } from './coverage'
import type { GraphNode, LoggedEvent } from '../types'

const hdr = (hex: string, k: number) => new THREE.Color(hex).multiplyScalar(k)
const ageOf = (n: GraphNode, t: number) => Math.max(0, t - (Date.parse(n.since) - BASE_MS) / 1000)

// ─── Ground ────────────────────────────────────────────────────────────────
const groundFrag = /* glsl */ `
uniform sampler2D uCover;
uniform float uExtent;
uniform float uTime;
varying vec3 vW;
void main() {
  vec2 uv = (vW.xz + uExtent) / (2.0 * uExtent);
  float c = texture2D(uCover, uv).r;
  float front = smoothstep(0.02, 0.25, c) * (1.0 - smoothstep(0.25, 0.7, c));
  vec2 g = abs(fract(vW.xz / 2.0) - 0.5);
  float grid = smoothstep(0.47, 0.5, max(g.x, g.y));
  vec2 G = abs(fract(vW.xz / 10.0) - 0.5);
  float major = smoothstep(0.485, 0.5, max(G.x, G.y));
  float r = length(vW.xz);
  float sweep = pow(max(0.0, cos(atan(vW.z, vW.x) - uTime * 0.6)), 60.0) * smoothstep(90.0, 10.0, r);
  vec3 col = vec3(0.004, 0.012, 0.02);
  col += vec3(0.012, 0.04, 0.05) * c;
  col += vec3(0.03, 0.12, 0.14) * grid * c * 0.35;
  col += vec3(0.04, 0.12, 0.15) * major * (0.1 + c * 0.35);
  col += vec3(0.3, 0.9, 1.0) * front * 0.22;
  col += vec3(0.05, 0.18, 0.2) * sweep * 0.5;
  float fade = smoothstep(95.0, 55.0, r);
  gl_FragColor = vec4(col * fade, 1.0);
}
`
export function Ground() {
  const mat = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader: 'varying vec3 vW; void main(){ vec4 w = modelMatrix*vec4(position,1.0); vW=w.xyz; gl_Position=projectionMatrix*viewMatrix*w; }',
        fragmentShader: groundFrag,
        uniforms: { uCover: { value: coverageTexture }, uExtent: { value: COVER_EXTENT }, uTime: { value: 0 } },
      }),
    [],
  )
  useFrame((_, dt) => (mat.uniforms.uTime.value += dt))
  return (
    <mesh rotation-x={-Math.PI / 2} position-y={-0.05} material={mat} onClick={() => useStore.getState().selectNode(null)}>
      <planeGeometry args={[200, 200]} />
    </mesh>
  )
}

// ─── Gas hazard ────────────────────────────────────────────────────────────
const gasVert = /* glsl */ `
uniform float uTime;
uniform float uR;
uniform float uGrow;
attribute vec4 aRand;
varying float vA;
varying vec3 vC;
void main() {
  float th = aRand.x * 6.2832 + uTime * (0.15 + aRand.y * 0.35);
  float ph = acos(2.0 * aRand.z - 1.0);
  float rad = pow(aRand.w, 0.5) * uR * uGrow;
  float rise = fract(uTime * 0.05 + aRand.y) ;
  vec3 p = vec3(sin(ph) * cos(th), abs(cos(ph)) * 0.55 + rise * 0.25, sin(ph) * sin(th)) * rad;
  p.y += sin(uTime + aRand.x * 20.0) * 0.3;
  vC = mix(vec3(1.0, 0.15, 0.2), vec3(1.0, 0.55, 0.15), aRand.w);
  vA = (1.0 - rise * 0.6) * 0.5;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_PointSize = (2.0 + aRand.y * 6.0) * (60.0 / -mv.z);
  gl_Position = projectionMatrix * mv;
}
`
const softFrag = /* glsl */ `
varying float vA;
varying vec3 vC;
void main() {
  float d = length(gl_PointCoord - 0.5);
  float a = smoothstep(0.5, 0.0, d) * vA;
  gl_FragColor = vec4(vC * a * 1.6, a);
}
`
function GasHazard({ n }: { n: GraphNode }) {
  const { x, z, r, ppm } = n.props as { x: number; z: number; r: number; ppm: number }
  const born = useRef<number | null>(null)
  const ring = useRef<THREE.Mesh>(null)
  const dome = useRef<THREE.Mesh>(null)
  const { geo, mat } = useMemo(() => {
    const N = 7000
    const rnd = new Float32Array(N * 4).map(() => Math.random())
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(N * 3), 3))
    geo.setAttribute('aRand', new THREE.Float32BufferAttribute(rnd, 4))
    const mat = new THREE.ShaderMaterial({
      vertexShader: gasVert, fragmentShader: softFrag, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      uniforms: { uTime: { value: 0 }, uR: { value: r }, uGrow: { value: 0 } },
    })
    return { geo, mat }
  }, [r])
  useFrame((s, dt) => {
    if (born.current === null) born.current = s.clock.elapsedTime - ageOf(n, useStore.getState().t)
    const age = s.clock.elapsedTime - born.current
    const grow = 1 - Math.exp(-age * 0.9)
    mat.uniforms.uTime.value += dt
    mat.uniforms.uGrow.value = grow
    if (dome.current) {
      dome.current.scale.setScalar(Math.max(0.01, r * grow))
      ;(dome.current.material as THREE.MeshBasicMaterial).opacity = 0.06 + 0.04 * Math.sin(age * 4)
    }
    if (ring.current) {
      const f = (age * 0.6) % 1
      ring.current.scale.setScalar(Math.max(0.01, r * grow * (0.3 + f * 0.8)))
      ;(ring.current.material as THREE.MeshBasicMaterial).opacity = 1 - f
    }
  })
  return (
    <group position={[x, 0, z]}>
      <points geometry={geo} material={mat} frustumCulled={false} />
      <mesh ref={dome}>
        <sphereGeometry args={[1, 40, 20, 0, Math.PI * 2, 0, Math.PI / 2]} />
        <meshBasicMaterial color={hdr(STATUS_HEX.danger, 1.5)} transparent wireframe depthWrite={false} toneMapped={false} />
      </mesh>
      <mesh ref={ring} rotation-x={-Math.PI / 2} position-y={0.1}>
        <ringGeometry args={[0.96, 1, 64]} />
        <meshBasicMaterial color={hdr(STATUS_HEX.danger, 3)} transparent toneMapped={false} />
      </mesh>
      <Html center position={[0, r * 0.75 + 2, 0]} zIndexRange={[7, 0]}>
        <div className="tag tag-hazard">
          <b>GAS HAZARD</b> CH4 {ppm} ppm · r {r} m
        </div>
      </Html>
    </group>
  )
}

// ─── Roads with a non-normal state ─────────────────────────────────────────
function RoadAlert({ n }: { n: GraphNode }) {
  const m = useRef<THREE.Mesh>(null)
  const a = n.props.a as number[]
  const b = n.props.b as number[]
  const horiz = a[1] === b[1]
  const len = horiz ? b[0] - a[0] : b[1] - a[1]
  const w = (n.props.width as number) + 0.8
  const hex = STATUS_HEX[n.status]
  useFrame((s) => {
    if (!m.current) return
    const mat = m.current.material as THREE.MeshBasicMaterial
    const t = s.clock.elapsedTime
    mat.opacity = n.status === 'conflict' ? (Math.sin(t * 9) > 0 ? 0.9 : 0.2) : 0.55 + 0.35 * Math.sin(t * 5)
  })
  const cx = (a[0] + b[0]) / 2
  const cz = (a[1] + b[1]) / 2
  const y = n.label === 'Bridge' ? 0.7 : 0.1
  const barriers = n.status === 'danger' ? [-0.3, 0, 0.3] : []
  return (
    <group position={[cx, y, cz]} rotation-y={horiz ? 0 : Math.PI / 2}>
      <mesh ref={m}>
        <boxGeometry args={[len, 0.12, w]} />
        <meshBasicMaterial color={hdr(hex, 2.4)} transparent toneMapped={false} depthWrite={false} />
      </mesh>
      {barriers.map((k) => (
        <group key={k} position={[k * len, 0.8, 0]}>
          <mesh rotation-x={0.7}>
            <boxGeometry args={[0.18, 0.18, w * 1.1]} />
            <meshBasicMaterial color={hdr(hex, 3)} toneMapped={false} />
          </mesh>
          <mesh rotation-x={-0.7}>
            <boxGeometry args={[0.18, 0.18, w * 1.1]} />
            <meshBasicMaterial color={hdr(hex, 3)} toneMapped={false} />
          </mesh>
        </group>
      ))}
      <Html center position={[0, 3.2, 0]} zIndexRange={[7, 0]}>
        <div className={`tag tag-road s-${n.status}`}>
          <b>{n.status === 'conflict' ? 'CONFLICTING REPORTS' : n.status === 'danger' ? 'BLOCKED' : 'WATCH'}</b>
          {String(n.props.name)}
        </div>
      </Html>
    </group>
  )
}

// ─── Routes and reroute suggestion (never auto-applied) ───────────────────
function FlowLine({ points, color, dash, width, speed, opacity = 1 }: { points: [number, number][]; color: THREE.Color; dash: number; width: number; speed: number; opacity?: number }) {
  const ref = useRef<Line2>(null)
  useFrame((_, dt) => {
    const m = ref.current?.material as unknown as { dashOffset: number } | undefined
    if (m) m.dashOffset -= dt * speed
  })
  return (
    <Line
      ref={ref}
      points={points.map(([x, z]) => [x, 0.45, z] as [number, number, number])}
      color={color}
      lineWidth={width}
      dashed
      dashSize={dash}
      gapSize={dash * 0.7}
      transparent
      opacity={opacity}
      toneMapped={false}
    />
  )
}

function Routes() {
  const amb = useStore((s) => s.nodes['Ambulance-2'])
  const suggestions = useStore((s) => s.suggestions)
  const pending = suggestions.find((s) => s.state === 'pending' && s.path.length > 1)
  const approved = suggestions.find((s) => s.state === 'approved' && s.path.length > 1)
  const route = amb?.props.route as [number, number][] | undefined
  const mid = pending ? pending.path[Math.floor(pending.path.length / 2)] : null
  return (
    <>
      {route && !approved && <FlowLine points={route} color={hdr(STATUS_HEX.safe, 1.2)} dash={0.8} width={1.5} speed={0.6} opacity={0.35} />}
      {pending && (
        <>
          <FlowLine points={pending.path} color={hdr(STATUS_HEX.safe, 3)} dash={1.2} width={3.5} speed={4} />
          <Html center position={[mid![0], 3, mid![1]]} zIndexRange={[8, 0]}>
            <div className="tag tag-suggest">
              <b>SUGGESTED DETOUR · {prettyId(pending.unit).toUpperCase()}</b>
              COMMANDER APPROVAL REQUIRED
            </div>
          </Html>
        </>
      )}
      {approved && <FlowLine points={approved.path} color={hdr(STATUS_HEX.safe, 4)} dash={3} width={4} speed={8} />}
    </>
  )
}

// ─── Event pings ────────────────────────────────────────────────────────────
interface Ping { id: string; x: number; z: number; color: THREE.Color; kind: 'seismic' | 'radio' | 'vision' | 'beam'; born: number }

function PingFx({ p }: { p: Ping }) {
  const g = useRef<THREE.Group>(null)
  useFrame((s) => {
    const age = s.clock.elapsedTime - p.born
    const grp = g.current
    if (!grp) return
    grp.children.forEach((c, i) => {
      const m = (c as THREE.Mesh).material as THREE.MeshBasicMaterial
      if (p.kind === 'seismic') {
        const a = Math.max(0, age - i * 0.45)
        c.scale.setScalar(Math.max(0.01, a * 45))
        m.opacity = Math.max(0, 1 - a / 3.5)
      } else if (p.kind === 'radio') {
        const a = (age * 0.9 + i * 0.33) % 1
        c.scale.setScalar(0.5 + a * 7)
        m.opacity = age > 3.5 ? 0 : 1 - a
      } else if (p.kind === 'vision') {
        const k = Math.max(0, 1 - age * 1.3)
        c.scale.setScalar(4 + k * 10)
        c.rotation.z = (i * Math.PI) / 2 + k * 1.5
        m.opacity = age < 3 ? 1 : Math.max(0, 1 - (age - 3))
      } else {
        m.opacity = Math.max(0, 1 - age / 3)
        if (i === 1) c.scale.setScalar(1 + age * 4)
      }
    })
  })
  const mat = <meshBasicMaterial color={p.color} transparent toneMapped={false} depthWrite={false} side={THREE.DoubleSide} blending={THREE.AdditiveBlending} />
  return (
    <group ref={g} position={[p.x, 0.2, p.z]}>
      {p.kind === 'seismic' &&
        [0, 1, 2, 3].map((i) => (
          <mesh key={i} rotation-x={-Math.PI / 2}>
            <ringGeometry args={[0.97, 1, 128]} />
            {mat}
          </mesh>
        ))}
      {p.kind === 'radio' &&
        [0, 1, 2].map((i) => (
          <mesh key={i} rotation-x={-Math.PI / 2} position-y={1.5}>
            <ringGeometry args={[0.9, 1, 48]} />
            {mat}
          </mesh>
        ))}
      {p.kind === 'vision' &&
        [0, 1, 2, 3].map((i) => (
          <mesh key={i} rotation-x={-Math.PI / 2} position-y={0.3}>
            <ringGeometry args={[0.92, 1, 4, 1, 0, Math.PI / 5]} />
            {mat}
          </mesh>
        ))}
      {p.kind === 'beam' && (
        <>
          <mesh position-y={15}>
            <cylinderGeometry args={[0.07, 0.07, 30, 8, 1, true]} />
            {mat}
          </mesh>
          <mesh rotation-x={-Math.PI / 2}>
            <ringGeometry args={[0.8, 1, 48]} />
            {mat}
          </mesh>
        </>
      )}
    </group>
  )
}

function Pings() {
  const [pings, setPings] = useState<Ping[]>([])
  const clock = useRef(0)
  useFrame((s) => (clock.current = s.clock.elapsedTime))
  useEffect(
    () =>
      useStore.subscribe((s, prev) => {
        if (s.events === prev.events || s.events.length <= prev.events.length) return
        const fresh = s.events.slice(prev.events.length).filter((e: LoggedEvent) => s.t - e.t < 2)
        if (!fresh.length) return
        const add: Ping[] = []
        for (const e of fresh) {
          // live mode: the quake arrives as the seismic sensor's `spike` (contract claim), not a 'seismic_event' claim
          const quake = e.claim.startsWith('seismic') || (e.entity.startsWith('Sensor-Seismic') && e.severity === 'danger')
          const n = quake ? s.nodes['Incident-EQ-1'] ?? s.nodes[e.entity] : s.nodes[e.entity]
          const pos = nodePos(n)
          if (!pos) continue
          const color = hdr(STATUS_HEX[e.severity], 3)
          const kind: Ping['kind'] = quake ? 'seismic' : e.source === 'radio_asr' ? 'radio' : e.source === 'drone_vision' ? 'vision' : 'beam'
          add.push({ id: e.id, x: pos[0], z: pos[1], color, kind, born: clock.current })
          if (kind !== 'beam') add.push({ id: `${e.id}-b`, x: pos[0], z: pos[1], color, kind: 'beam', born: clock.current })
        }
        setPings((p) => {
          const fresh = new Set(add.map((q) => q.id))
          return [...p.filter((q) => clock.current - q.born < 6 && !fresh.has(q.id)), ...add]
        })
      }),
    [],
  )
  return <>{pings.map((p) => <PingFx key={p.id} p={p} />)}</>
}

// ─── Q&A highlight beacons ────────────────────────────────────────────────
function Beacon({ x, z, primary }: { x: number; z: number; primary: boolean }) {
  const g = useRef<THREE.Group>(null)
  useFrame((s) => {
    if (!g.current) return
    const t = s.clock.elapsedTime
    g.current.children[0].rotation.z = t * 1.2
    g.current.children[1].rotation.z = -t * 0.8
    g.current.children[2].scale.y = 0.8 + Math.sin(t * 5) * 0.2
  })
  const c = new THREE.Color('#cfe8ff').multiplyScalar(primary ? 3 : 1.8)
  return (
    <group ref={g} position={[x, 0.25, z]}>
      <mesh rotation-x={-Math.PI / 2}>
        <ringGeometry args={[primary ? 5 : 3.4, primary ? 5.3 : 3.6, 64, 1, 0, Math.PI * 1.6]} />
        <meshBasicMaterial color={c} toneMapped={false} side={THREE.DoubleSide} />
      </mesh>
      <mesh rotation-x={-Math.PI / 2}>
        <ringGeometry args={[primary ? 6.2 : 4.2, primary ? 6.35 : 4.3, 6]} />
        <meshBasicMaterial color={c} toneMapped={false} side={THREE.DoubleSide} />
      </mesh>
      <mesh position-y={20} visible={primary}>
        <cylinderGeometry args={[0.03, 0.18, 40, 8, 1, true]} />
        <meshBasicMaterial color={c} toneMapped={false} transparent opacity={0.22} blending={THREE.AdditiveBlending} depthWrite={false} />
      </mesh>
    </group>
  )
}

function Highlights() {
  const flyTo = useStore((s) => s.flyTo)
  const selected = useStore((s) => s.selectedNodeId)
  const nodes = useStore((s) => s.nodes)
  const ids = new Set<string>([...(flyTo?.highlight ?? []), ...(flyTo ? [flyTo.target] : []), ...(selected ? [selected] : [])])
  return (
    <>
      {[...ids].map((id) => {
        const p = nodePos(nodes[id])
        return p ? <Beacon key={id} x={p[0]} z={p[1]} primary={id === flyTo?.target || id === selected} /> : null
      })}
    </>
  )
}

/** `ground` draws the dark scanned-ground grid; the mesh twin brings its own ground and turns it off. */
export function Overlays({ ground = true }: { ground?: boolean }) {
  const nodes = useStore((s) => s.nodes)
  const list = Object.values(nodes)
  const hazards = list.filter((n) => n.label === 'Hazard')
  const roads = list.filter((n) => (n.label === 'Road' || n.label === 'Bridge') && n.status !== 'normal')
  return (
    <>
      {ground && <Ground />}
      {hazards.map((n) => <GasHazard key={n.id} n={n} />)}
      {roads.map((n) => <RoadAlert key={n.id} n={n} />)}
      <Routes />
      <Pings />
      <Highlights />
    </>
  )
}
