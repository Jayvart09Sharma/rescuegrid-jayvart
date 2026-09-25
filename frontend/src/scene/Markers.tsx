// Ground units, facilities and sensors: everything GPS/seed-positioned, so it's
// visible regardless of drone coverage.
import { useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Html } from '@react-three/drei'
import * as THREE from 'three'
import { useStore } from '../store'
import { STATUS_HEX } from '../config'
import type { GraphNode } from '../types'

const BLUE = new THREE.Color(STATUS_HEX.safe).multiplyScalar(2.2)
const RED = new THREE.Color(STATUS_HEX.danger).multiplyScalar(2.5)
const TRAIL = 60

function Unit({ id }: { id: string }) {
  const g = useRef<THREE.Group>(null)
  const gem = useRef<THREE.Mesh>(null)
  const ring = useRef<THREE.Mesh>(null)
  const risk = useRef<THREE.Mesh>(null)
  const pos = useRef(new THREE.Vector3(NaN, 0, 0))
  const acc = useRef(0)
  const node = useStore((s) => s.nodes[id])
  const atRisk = useStore((s) => Object.values(s.edges).some((e) => e.type === 'AT_RISK' && e.from === id))
  const hovered = useStore((s) => s.hoverId === id)

  const trail = useMemo(() => {
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(TRAIL * 3), 3))
    const col = new Float32Array(TRAIL * 3)
    for (let i = 0; i < TRAIL; i++) {
      const a = (i / TRAIL) ** 2
      col.set([0.2 * a * 2, 0.55 * a * 2, 1.0 * a * 2], i * 3)
    }
    geo.setAttribute('color', new THREE.Float32BufferAttribute(col, 3))
    geo.setDrawRange(0, 0)
    const line = new THREE.Line(geo, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, blending: THREE.AdditiveBlending, toneMapped: false }))
    line.frustumCulled = false
    return { line, pts: [] as THREE.Vector3[] }
  }, [])

  useFrame((state, dt) => {
    const n = useStore.getState().nodes[id]
    if (!n || !g.current) return
    const p = pos.current
    const tx = n.props.x as number
    const tz = n.props.z as number
    if (Number.isNaN(p.x)) p.set(tx, 0, tz)
    const k = 1 - Math.exp(-dt * 5)
    p.x += (tx - p.x) * k
    p.z += (tz - p.z) * k
    g.current.position.copy(p)
    const t = state.clock.elapsedTime
    if (gem.current) {
      gem.current.rotation.y = t * 1.5
      gem.current.position.y = 2.4 + Math.sin(t * 2 + id.length) * 0.25
    }
    if (ring.current) {
      const s = 1 + ((t * 0.8) % 1) * 2.2
      ring.current.scale.setScalar(s)
      ;(ring.current.material as THREE.MeshBasicMaterial).opacity = 1 - ((t * 0.8) % 1)
    }
    if (risk.current) {
      const s = 1.8 + Math.sin(t * 8) * 0.25
      risk.current.scale.setScalar(s)
    }
    acc.current += dt
    if (acc.current > 0.15) {
      acc.current = 0
      const last = trail.pts[trail.pts.length - 1]
      if (!last || last.distanceToSquared(p) > 0.04) {
        trail.pts.push(new THREE.Vector3(p.x, 0.25, p.z))
        if (trail.pts.length > TRAIL) trail.pts.shift()
        const arr = trail.line.geometry.attributes.position.array as Float32Array
        const off = TRAIL - trail.pts.length
        trail.pts.forEach((v, i) => arr.set([v.x, v.y, v.z], (i + off) * 3))
        trail.line.geometry.attributes.position.needsUpdate = true
        trail.line.geometry.setDrawRange(off, trail.pts.length)
      }
    }
  })

  if (!node) return null
  const state = node.props.state as string | undefined
  return (
    <>
      <primitive object={trail.line} />
      <group
        ref={g}
        onPointerOver={(e) => { e.stopPropagation(); useStore.getState().setHover(id) }}
        onPointerOut={() => useStore.getState().setHover(null)}
        onClick={(e) => { e.stopPropagation(); useStore.getState().selectNode(id) }}
      >
        <mesh ref={gem} scale={hovered ? 1.4 : 1}>
          <octahedronGeometry args={[0.8, 0]} />
          <meshBasicMaterial color={BLUE} toneMapped={false} wireframe />
        </mesh>
        {/^(ambulance|engine|police|rescue|fire)$/.test(String(node.props.kind)) && <EmergencyLights kind={String(node.props.kind)} />}
        <mesh position={[0, 1.2, 0]}>
          <cylinderGeometry args={[0.04, 0.04, 2.4, 6]} />
          <meshBasicMaterial color={BLUE} toneMapped={false} transparent opacity={0.6} />
        </mesh>
        <mesh ref={ring} rotation-x={-Math.PI / 2} position-y={0.1}>
          <ringGeometry args={[0.8, 0.95, 40]} />
          <meshBasicMaterial color={BLUE} toneMapped={false} transparent />
        </mesh>
        {atRisk && (
          <mesh ref={risk} rotation-x={-Math.PI / 2} position-y={0.12}>
            <ringGeometry args={[1.1, 1.35, 6]} />
            <meshBasicMaterial color={RED} toneMapped={false} />
          </mesh>
        )}
        <Html center position={[0, 4.4, 0]} zIndexRange={[6, 0]}>
          <div className={`tag tag-unit ${atRisk ? 'tag-risk' : ''}`}>
            <b>{String(node.props.callsign).toUpperCase()}</b>
            {atRisk && <span className="risk">AT RISK</span>}
            {state && <i>{state}</i>}
          </div>
        </Html>
      </group>
    </>
  )
}

/** Flashing red / blue emergency beacons on responder units (lit twin gets point lights too). */
function EmergencyLights({ kind }: { kind: string }) {
  const r = useRef<THREE.Mesh>(null)
  const b = useRef<THREE.Mesh>(null)
  const lr = useRef<THREE.PointLight>(null)
  const lb = useRef<THREE.PointLight>(null)
  const blue = kind === 'police' || kind === 'ambulance'
  useFrame((s) => {
    const t = s.clock.elapsedTime * 6
    const on1 = Math.floor(t) % 2 === 0
    const on2 = !on1
    if (r.current) (r.current.material as THREE.MeshBasicMaterial).opacity = on1 ? 1 : 0.15
    if (b.current) (b.current.material as THREE.MeshBasicMaterial).opacity = on2 ? 1 : 0.15
    if (lr.current) lr.current.intensity = on1 ? 25 : 0
    if (lb.current) lb.current.intensity = on2 ? 25 : 0
  })
  return (
    <group position={[0, 1.6, 0]}>
      <mesh ref={r} position={[-0.45, 0, 0]}>
        <sphereGeometry args={[0.22, 10, 10]} />
        <meshBasicMaterial color={[4, 0.3, 0.3]} toneMapped={false} transparent />
      </mesh>
      <mesh ref={b} position={[0.45, 0, 0]}>
        <sphereGeometry args={[0.22, 10, 10]} />
        <meshBasicMaterial color={blue ? [0.4, 0.8, 4] : [4, 3, 1]} toneMapped={false} transparent />
      </mesh>
      <pointLight ref={lr} position={[-0.6, 0.3, 0]} color="#ff2a2a" distance={12} decay={2} />
      <pointLight ref={lb} position={[0.6, 0.3, 0]} color={blue ? '#3a7cff' : '#ffb347'} distance={12} decay={2} />
    </group>
  )
}

function Facility({ n }: { n: GraphNode }) {
  const beam = useRef<THREE.Mesh>(null)
  useFrame((s) => {
    if (beam.current) (beam.current.material as THREE.MeshBasicMaterial).opacity = 0.25 + 0.15 * Math.sin(s.clock.elapsedTime * 2)
  })
  if (n.props.park) return null
  const { x, z, name } = n.props as { x: number; z: number; name: string }
  const h = (n.props.h as number | undefined) ?? 0
  const color = new THREE.Color(STATUS_HEX[n.status]).multiplyScalar(2)
  const tag = n.label === 'Hospital' ? 'H' : n.label === 'Shelter' ? 'S' : 'IC'
  const occ = n.props.occupancy as number | undefined
  return (
    <group position={[x, 0, z]}>
      <mesh ref={beam} position-y={h + 6}>
        <cylinderGeometry args={[0.08, 0.5, 12, 12, 1, true]} />
        <meshBasicMaterial color={color} transparent toneMapped={false} blending={THREE.AdditiveBlending} depthWrite={false} />
      </mesh>
      {n.label !== 'Hospital' && (
        <mesh rotation-x={-Math.PI / 2} position-y={0.06}>
          <ringGeometry args={[3.2, 3.5, 4, 1]} />
          <meshBasicMaterial color={color} toneMapped={false} />
        </mesh>
      )}
      <Html center position={[0, h + 13, 0]} zIndexRange={[4, 0]}>
        <div className={`tag tag-fac s-${n.status}`}>
          <span className="glyph">{tag}</span>
          {name}
          {occ !== undefined && (
            <span className="occ"><span style={{ width: `${occ * 100}%` }} /></span>
          )}
        </div>
      </Html>
    </group>
  )
}

function Sensor({ n }: { n: GraphNode }) {
  const ring = useRef<THREE.Mesh>(null)
  const { x, z, kind, reading } = n.props as { x: number; z: number; kind: string; reading: string }
  const color = new THREE.Color(n.status === 'normal' ? STATUS_HEX.normal : STATUS_HEX[n.status]).multiplyScalar(n.status === 'normal' ? 1.2 : 3)
  useFrame((s) => {
    if (!ring.current) return
    const level = kind === 'seismic' ? Math.min(1, parseFloat(String(reading ?? '0')) / 1.0 || 0) : 0
    const speed = n.status === 'normal' ? 0.4 + level * 2.4 : 1.6
    const f = (s.clock.elapsedTime * speed) % 1
    ring.current.scale.setScalar(1 + f * (n.status === 'normal' ? 1.5 : 5))
    ;(ring.current.material as THREE.MeshBasicMaterial).opacity = 1 - f
  })
  return (
    <group position={[x, 0, z]}>
      <mesh position-y={0.9}>
        <cylinderGeometry args={[0.12, 0.3, 1.8, 6]} />
        <meshBasicMaterial color={color} toneMapped={false} />
      </mesh>
      <mesh ref={ring} rotation-x={-Math.PI / 2} position-y={0.08}>
        <ringGeometry args={[0.5, 0.62, 32]} />
        <meshBasicMaterial color={color} toneMapped={false} transparent />
      </mesh>
      {(n.status !== 'normal' || (kind === 'seismic' && reading)) && (
        <Html center position={[0, 3, 0]} zIndexRange={[4, 0]}>
          <div className={`tag tag-sensor s-${n.status}`}>
            {kind.toUpperCase()} · {reading}
          </div>
        </Html>
      )}
    </group>
  )
}

export function Markers() {
  const unitIds = useStore((s) =>
    Object.values(s.nodes).filter((n) => n.label === 'Unit' && n.props.kind !== 'drone').map((n) => n.id).join(','),
  )
  const facilities = useStore((s) => s.nodes)
  const fac = Object.values(facilities).filter((n) => n.label === 'Hospital' || n.label === 'Shelter' || n.label === 'StagingArea')
  const sensors = Object.values(facilities).filter((n) => n.label === 'Sensor')
  return (
    <>
      {unitIds.split(',').filter(Boolean).map((id) => <Unit key={id} id={id} />)}
      {fac.map((n) => <Facility key={n.id} n={n} />)}
      {sensors.map((n) => <Sensor key={n.id} n={n} />)}
    </>
  )
}
