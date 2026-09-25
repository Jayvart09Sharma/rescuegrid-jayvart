// The earthquake, made visible. Everything here is driven by the graph mirror (store), never the other way round:
//  - Incident / seismic spike       -> ground cracks radiating from the epicentre, dust haze, aftershock tremors
//  - Building collapsed             -> dust column + embers; with a fire hazard on it: flames, black smoke, flicker light
//  - Hazard kind 'fire'             -> flames + smoke on the entity it affects
//  - Road blocked (danger)          -> rubble chunks scattered across the segment
//  - Units (ambulance / engine)     -> emergency beacons (Markers.tsx)
import { useEffect, useMemo, useRef } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import { Line } from '@react-three/drei'
import * as THREE from 'three'
import { nodePos, useStore } from '../store'
import { TWIN } from '../config'
import type { GraphNode } from '../types'

const MESH = TWIN === 'mesh'

// ─── smoke / flame particle shader ──────────────────────────────────────────
const plumeVert = /* glsl */ `
uniform float uTime;
uniform float uH;      // plume height
uniform float uW;      // base width
uniform float uGrow;
uniform vec3 uWind;
uniform float uFlame;  // 1 = flames (short, bright, additive); 0 = smoke (tall, grey, alpha)
attribute vec4 aRand;
varying float vA;
varying vec3 vC;
void main() {
  float life = fract(uTime * (0.08 + aRand.y * 0.06) + aRand.x);
  float h = life * uH * uGrow;
  float spread = (0.4 + life * 1.6) * uW;
  float th = aRand.z * 6.2832 + uTime * 0.2 * (aRand.w - 0.5);
  vec3 p = vec3(cos(th) * spread * aRand.w, h, sin(th) * spread * aRand.w);
  p += uWind * life * life * uH * 0.6;
  p.x += sin(uTime * 0.7 + aRand.x * 30.0) * 0.6 * life;
  if (uFlame > 0.5) {
    vC = mix(vec3(1.0, 0.85, 0.3), vec3(1.0, 0.25, 0.05), life);
    vA = (1.0 - life) * (1.0 - life) * 0.9;
  } else {
    float g = 0.12 + aRand.w * 0.18;
    vC = mix(vec3(0.35, 0.28, 0.22), vec3(g, g, g), life);
    vA = smoothstep(0.0, 0.1, life) * (1.0 - smoothstep(0.55, 1.0, life)) * 0.55;
  }
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  float size = uFlame > 0.5 ? (1.5 + aRand.y * 3.0) : (3.0 + aRand.y * 6.0 + life * 10.0);
  gl_PointSize = size * (70.0 / -mv.z);
  gl_Position = projectionMatrix * mv;
}
`
const plumeFrag = /* glsl */ `
varying float vA;
varying vec3 vC;
void main() {
  float d = length(gl_PointCoord - 0.5);
  float a = smoothstep(0.5, 0.05, d) * vA;
  gl_FragColor = vec4(vC, a);
}
`

function Plume({ x, z, y = 0, flame, h, w, n = 2500 }: { x: number; z: number; y?: number; flame: boolean; h: number; w: number; n?: number }) {
  const { geo, mat } = useMemo(() => {
    const rnd = new Float32Array(n * 4).map(() => Math.random())
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(n * 3), 3))
    geo.setAttribute('aRand', new THREE.Float32BufferAttribute(rnd, 4))
    const mat = new THREE.ShaderMaterial({
      vertexShader: plumeVert, fragmentShader: plumeFrag, transparent: true, depthWrite: false,
      blending: flame ? THREE.AdditiveBlending : THREE.NormalBlending,
      uniforms: { uTime: { value: Math.random() * 100 }, uH: { value: h }, uW: { value: w }, uGrow: { value: 0 }, uWind: { value: new THREE.Vector3(0.35, 0, 0.15) }, uFlame: { value: flame ? 1 : 0 } },
    })
    return { geo, mat }
  }, [flame, h, w, n])
  useFrame((_, dt) => {
    mat.uniforms.uTime.value += dt
    mat.uniforms.uGrow.value = Math.min(1, (mat.uniforms.uGrow.value as number) + dt * 0.35)
  })
  return <points position={[x, y, z]} geometry={geo} material={mat} frustumCulled={false} />
}

/** Flickering fire light for the lit (mesh) twin. */
function FireLight({ x, z, y }: { x: number; z: number; y: number }) {
  const l = useRef<THREE.PointLight>(null)
  useFrame((s) => {
    if (!l.current) return
    const t = s.clock.elapsedTime
    l.current.intensity = 60 + Math.sin(t * 17) * 18 + Math.sin(t * 5.3) * 14
  })
  return <pointLight ref={l} position={[x, y, z]} color="#ff7a2a" distance={38} decay={2} castShadow={false} />
}

function Fire({ x, z, w, big }: { x: number; z: number; w: number; big: boolean }) {
  return (
    <>
      <Plume x={x} z={z} y={0.4} flame h={big ? 7 : 4} w={w * 0.5} n={big ? 3000 : 1600} />
      <Plume x={x} z={z} y={1.5} flame={false} h={big ? 40 : 26} w={w * 0.7} n={big ? 4000 : 2500} />
      {MESH && <FireLight x={x} z={z} y={3} />}
    </>
  )
}

/** A collapsed building without fire: a settling dust column and drifting grit. */
function Dust({ x, z, w }: { x: number; z: number; w: number }) {
  return <Plume x={x} z={z} y={0.3} flame={false} h={16} w={w * 0.9} n={1800} />
}

// ─── ground cracks from the epicentre ────────────────────────────────────────
function crackPath(cx: number, cz: number, angle: number, len: number, seed: number): [number, number, number][] {
  let s = seed >>> 0
  const rnd = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296 }
  const pts: [number, number, number][] = [[cx, 0.06, cz]]
  let a = angle
  let x = cx
  let z = cz
  const steps = 10 + Math.floor(rnd() * 8)
  for (let i = 0; i < steps; i++) {
    a += (rnd() - 0.5) * 0.9
    const d = (len / steps) * (0.5 + rnd())
    x += Math.cos(a) * d
    z += Math.sin(a) * d
    pts.push([x, 0.06, z])
  }
  return pts
}

function Cracks({ x, z }: { x: number; z: number }) {
  const paths = useMemo(() => {
    const out: [number, number, number][][] = []
    const N = 9
    for (let i = 0; i < N; i++) {
      const a = (i / N) * Math.PI * 2 + 0.3
      out.push(crackPath(x, z, a, 34 + (i % 3) * 14, 1000 + i))
      if (i % 2 === 0) out.push(crackPath(x + Math.cos(a) * 12, z + Math.sin(a) * 12, a + 0.9, 14, 2000 + i))
    }
    return out
  }, [x, z])
  const grow = useRef(0)
  const g = useRef<THREE.Group>(null)
  useFrame((_, dt) => {
    grow.current = Math.min(1, grow.current + dt * 0.25)
    if (g.current) g.current.visible = grow.current > 0.02
  })
  const col = MESH ? '#1a120e' : '#3a1611'
  const glow = MESH ? '#5a2a1a' : '#ff3a2a'
  return (
    <group ref={g}>
      {paths.map((p, i) => (
        <group key={i}>
          <Line points={p} color={col} lineWidth={MESH ? 2.6 : 1.6} transparent opacity={0.95} />
          <Line points={p} color={glow} lineWidth={MESH ? 0.8 : 0.6} transparent opacity={MESH ? 0.5 : 0.35} />
        </group>
      ))}
      {/* epicentre: a dark sunken ring */}
      <mesh position={[x, 0.05, z]} rotation-x={-Math.PI / 2}>
        <ringGeometry args={[2.5, 6, 48]} />
        <meshBasicMaterial color={col} transparent opacity={0.7} depthWrite={false} />
      </mesh>
    </group>
  )
}

// ─── rubble on blocked roads ──────────────────────────────────────────────────
function RoadRubble({ n }: { n: GraphNode }) {
  const a = n.props.a as number[]
  const b = n.props.b as number[]
  const width = ((n.props.width as number) ?? 2.2) * 1.2
  const mesh = useRef<THREE.InstancedMesh>(null)
  const N = 42
  useEffect(() => {
    const m = mesh.current
    if (!m) return
    let s = (n.id.length * 7919) >>> 0
    const rnd = () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296 }
    const dummy = new THREE.Object3D()
    for (let i = 0; i < N; i++) {
      const k = rnd()
      const px = a[0] + (b[0] - a[0]) * k + (rnd() - 0.5) * width
      const pz = a[1] + (b[1] - a[1]) * k + (rnd() - 0.5) * width
      const sz = 0.25 + rnd() * 0.9
      dummy.position.set(px, sz * 0.4, pz)
      dummy.rotation.set(rnd() * 3, rnd() * 3, rnd() * 3)
      dummy.scale.set(sz, sz * (0.5 + rnd() * 0.6), sz * (0.6 + rnd() * 0.8))
      dummy.updateMatrix()
      m.setMatrixAt(i, dummy.matrix)
    }
    m.instanceMatrix.needsUpdate = true
  }, [a, b, width, n.id])
  return (
    <instancedMesh ref={mesh} args={[undefined, undefined, N]} castShadow={MESH} receiveShadow={MESH}>
      <dodecahedronGeometry args={[0.6, 0]} />
      {MESH ? <meshStandardMaterial color="#6e655c" roughness={1} /> : <meshBasicMaterial color="#5a2e28" />}
    </instancedMesh>
  )
}

// ─── aftershocks ─────────────────────────────────────────────────────────────
/** After the quake, smaller tremors every 30-70 s: the Shaker in Scene.tsx reacts to store.shock. */
function Aftershocks({ active }: { active: boolean }) {
  useEffect(() => {
    if (!active) return
    let stop = false
    let timer = 0
    const next = () => {
      timer = window.setTimeout(() => {
        if (stop) return
        const s = useStore.getState()
        useStore.setState({ shock: { nonce: s.shock.nonce + 1, severity: 'warning' } })
        next()
      }, 30000 + Math.random() * 40000)
    }
    next()
    return () => { stop = true; clearTimeout(timer) }
  }, [active])
  return null
}

// ─── dust haze: fog tinted like airborne concrete dust ───────────────────────
function Haze({ active }: { active: boolean }) {
  const scene = useThree((s) => s.scene)
  useEffect(() => {
    const prev = scene.fog
    if (!active) return
    scene.fog = MESH ? new THREE.Fog('#b9ac9a', 90, 330) : new THREE.Fog('#120c0a', 110, 300)
    return () => { scene.fog = prev }
  }, [active, scene])
  return null
}

export function Disaster() {
  const nodes = useStore((s) => s.nodes)
  const list = Object.values(nodes)
  const incident = list.find((n) => n.label === 'Incident') ?? list.find((n) => n.label === 'Sensor' && (n.props.kind === 'seismic') && n.status === 'danger')
  const quake = Boolean(incident) || list.some((n) => n.label === 'Building' && n.props.collapsed)
  const fires = list.filter((n) => n.label === 'Hazard' && n.props.kind === 'fire' && n.status === 'danger')
  const fireAt = new Set(fires.map((f) => f.props.at as string | undefined).filter(Boolean))
  const collapsed = list.filter((n) => n.label === 'Building' && n.props.collapsed)
  const blocked = list.filter((n) => (n.label === 'Road' || n.label === 'Bridge') && n.status === 'danger' && Array.isArray(n.props.a))
  const epi = incident ? nodePos(incident) : null
  return (
    <group>
      <Haze active={quake} />
      <Aftershocks active={quake} />
      {epi && <Cracks x={epi[0]} z={epi[1]} />}
      {collapsed.map((b) => {
        const w = (b.props.w as number) ?? 6
        return fireAt.has(b.id) ? <Fire key={b.id} x={b.props.x as number} z={b.props.z as number} w={w} big /> : <Dust key={b.id} x={b.props.x as number} z={b.props.z as number} w={w} />
      })}
      {fires.filter((f) => !collapsed.some((b) => b.id === f.props.at)).map((f) => {
        const p = nodePos(f)
        return p ? <Fire key={f.id} x={p[0]} z={p[1]} w={5} big={false} /> : null
      })}
      {blocked.map((r) => <RoadRubble key={r.id} n={r} />)}
    </group>
  )
}
