// The earthquake, made visible. Everything here is driven by the graph mirror (store), never the other way round:
//  - Incident / seismic spike       -> a heavy first jolt, a dust burst at the epicentre, dust haze, aftershock tremors
//  - Building collapsed             -> dust and smoke settling over the rubble (no flames: a gas leak is not a fire)
//                                      with a fire hazard on it: flames, black smoke, fire light
//  - Building damaged (warning)     -> thin smoke wisps
//  - Hazard kind 'fire'             -> flames + smoke on the entity it affects
//  - Road blocked (danger)          -> rubble chunks scattered across the segment
//  - Units (ambulance / engine)     -> emergency beacons (Markers.tsx)
import { useEffect, useMemo, useRef } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
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
uniform float uFlame;  // 1 = flames (short, bright, additive); 0 = smoke (tall, grey, alpha); 2 = dust burst (wide, brown, fading)
attribute vec4 aRand;
varying float vA;
varying vec3 vC;
void main() {
  float life = fract(uTime * (0.08 + aRand.y * 0.06) + aRand.x);
  if (uFlame > 1.5) life = min(1.0, uGrow * (0.35 + aRand.x * 0.65));
  float h = life * uH * uGrow;
  float spread = (0.4 + life * 1.6) * uW;
  float th = aRand.z * 6.2832 + uTime * 0.2 * (aRand.w - 0.5);
  vec3 p = vec3(cos(th) * spread * aRand.w, h, sin(th) * spread * aRand.w);
  if (uFlame > 1.5) p = vec3(cos(th) * uW * life * (0.6 + aRand.w), uH * life * (0.3 + aRand.y * 0.7) * (1.0 - life * 0.5), sin(th) * uW * life * (0.6 + aRand.w));
  p += uWind * life * life * uH * 0.6;
  p.x += sin(uTime * 0.7 + aRand.x * 30.0) * 0.6 * life;
  if (uFlame > 1.5) {
    vC = vec3(0.55, 0.47, 0.38);
    vA = (1.0 - life) * 0.55 * (1.0 - smoothstep(0.85, 1.0, uGrow));
  } else if (uFlame > 0.5) {
    vC = mix(vec3(1.0, 0.85, 0.3), vec3(1.0, 0.25, 0.05), life);
    vA = (1.0 - life) * (1.0 - life) * 0.9;
  } else {
    float g = 0.12 + aRand.w * 0.18;
    vC = mix(vec3(0.35, 0.28, 0.22), vec3(g, g, g), life);
    vA = smoothstep(0.0, 0.1, life) * (1.0 - smoothstep(0.55, 1.0, life)) * 0.55;
  }
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  float size = uFlame > 1.5 ? (4.0 + aRand.y * 9.0 + life * 12.0) : uFlame > 0.5 ? (1.5 + aRand.y * 3.0) : (3.0 + aRand.y * 6.0 + life * 10.0);
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

function Plume({ x, z, y = 0, mode, h, w, n = 2500, rate = 0.35 }: { x: number; z: number; y?: number; mode: 'flame' | 'smoke' | 'burst'; h: number; w: number; n?: number; rate?: number }) {
  const { geo, mat } = useMemo(() => {
    const rnd = new Float32Array(n * 4).map(() => Math.random())
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(n * 3), 3))
    geo.setAttribute('aRand', new THREE.Float32BufferAttribute(rnd, 4))
    const mat = new THREE.ShaderMaterial({
      vertexShader: plumeVert, fragmentShader: plumeFrag, transparent: true, depthWrite: false,
      blending: mode === 'flame' ? THREE.AdditiveBlending : THREE.NormalBlending,
      uniforms: { uTime: { value: Math.random() * 100 }, uH: { value: h }, uW: { value: w }, uGrow: { value: 0 }, uWind: { value: new THREE.Vector3(0.35, 0, 0.15) }, uFlame: { value: mode === 'flame' ? 1 : mode === 'burst' ? 2 : 0 } },
    })
    return { geo, mat }
  }, [mode, h, w, n])
  useFrame((_, dt) => {
    mat.uniforms.uTime.value += dt
    mat.uniforms.uGrow.value = Math.min(1, (mat.uniforms.uGrow.value as number) + dt * rate)
  })
  return <points position={[x, y, z]} geometry={geo} material={mat} frustumCulled={false} />
}

/** Flickering fire light for the lit (mesh) twin. */
function FireLight({ x, z, y, k = 1 }: { x: number; z: number; y: number; k?: number }) {
  const l = useRef<THREE.PointLight>(null)
  useFrame((s) => {
    if (!l.current) return
    const t = s.clock.elapsedTime
    l.current.intensity = (60 + Math.sin(t * 17) * 18 + Math.sin(t * 5.3) * 14) * k
  })
  return <pointLight ref={l} position={[x, y, z]} color="#ff7a2a" distance={38 * k} decay={2} castShadow={false} />
}

function Fire({ x, z, w, big }: { x: number; z: number; w: number; big: boolean }) {
  return (
    <>
      <Plume x={x} z={z} y={0.4} mode="flame" h={big ? 8 : 4.5} w={w * (big ? 0.55 : 0.35)} n={big ? 3200 : 1600} />
      <Plume x={x} z={z} y={1.5} mode="smoke" h={big ? 44 : 28} w={w * (big ? 0.75 : 0.5)} n={big ? 4200 : 2400} />
      {MESH && <FireLight x={x} z={z} y={3} k={big ? 1.2 : 0.6} />}
    </>
  )
}

/** Thin smoke wisps from a damaged building. */
function Wisps({ x, z, w, h }: { x: number; z: number; w: number; h: number }) {
  return <Plume x={x} z={z} y={h * 0.6} mode="smoke" h={14} w={w * 0.35} n={700} />
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

// ─── the main shock and the aftershocks ──────────────────────────────────────
/** When the incident first appears: one heavy jolt and a dust burst at the epicentre. Then smaller tremors every 30-70 s. */
function Shocks({ active }: { active: boolean }) {
  useEffect(() => {
    if (!active) return
    const s = useStore.getState()
    useStore.setState({ shock: { nonce: s.shock.nonce + 1, severity: 'danger', amp: 3.2 } })
    let stop = false
    let timer = 0
    const next = () => {
      timer = window.setTimeout(() => {
        if (stop) return
        const st = useStore.getState()
        useStore.setState({ shock: { nonce: st.shock.nonce + 1, severity: 'warning', amp: 0.6 + Math.random() * 0.8 } })
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
  const damaged = list.filter((n) => n.label === 'Building' && !n.props.collapsed && n.status === 'warning' && typeof n.props.x === 'number')
  const blocked = list.filter((n) => (n.label === 'Road' || n.label === 'Bridge') && n.status === 'danger' && Array.isArray(n.props.a) && Array.isArray(n.props.b))
  const epi = incident ? nodePos(incident) : null
  return (
    <group>
      <Haze active={quake} />
      <Shocks active={quake} />
      {/* no fissure lines: they did not read well; the quake shows as the jolt, the dust burst and haze, aftershocks, and what falls */}
      {epi && <Plume x={epi[0]} z={epi[1]} y={0.2} mode="burst" h={16} w={22} n={3000} rate={0.12} />}
      {collapsed.map((b) => {
        const w = (b.props.w as number) ?? 6
        return fireAt.has(b.id)
          ? <Fire key={b.id} x={b.props.x as number} z={b.props.z as number} w={w} big />
          : <Plume key={b.id} x={b.props.x as number} z={b.props.z as number} y={0.3} mode="smoke" h={20} w={w * 0.9} n={2200} />
      })}
      {damaged.map((b) => <Wisps key={b.id} x={b.props.x as number} z={b.props.z as number} w={(b.props.w as number) ?? 6} h={(b.props.h as number) ?? 6} />)}
      {fires.filter((f) => !collapsed.some((b) => b.id === f.props.at)).map((f) => {
        const p = nodePos(f)
        return p ? <Fire key={f.id} x={p[0]} z={p[1]} w={5} big={false} /> : null
      })}
      {blocked.map((r) => <RoadRubble key={r.id} n={r} />)}
    </group>
  )
}
