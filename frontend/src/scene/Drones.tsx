// Drones: quad model, scan cone, ground reticle, and the projector that streams
// the drone's (placeholder) camera frame as coloured points down into the twin.
import { useMemo, useRef } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import { Html } from '@react-three/drei'
import * as THREE from 'three'
import { useStore } from '../store'
import { DRONE_ALT, DRONE_FOOTPRINT } from '../data/scenario'
import { paint } from './coverage'

const FW = 96
const FH = 72
const SPAN = DRONE_FOOTPRINT * 0.85

const projVert = /* glsl */ `
uniform vec3 uDrone;
uniform vec2 uCenter;
uniform sampler2D uFrame;
uniform float uTime;
uniform float uPixelRatio;
uniform float uSpan;
attribute vec2 aUv;
attribute float aPhase;
varying vec3 vColor;
varying float vAlpha;
void main() {
  float life = fract(uTime * 0.45 + aPhase);
  vec3 tex = texture2D(uFrame, vec2(aUv.x, 1.0 - aUv.y)).rgb;
  float lum = dot(tex, vec3(0.3, 0.59, 0.11));
  vec3 target = vec3(uCenter.x + (aUv.x - 0.5) * 2.0 * uSpan, lum * 3.0, uCenter.y + (aUv.y - 0.5) * 2.0 * uSpan * 0.75);
  vec3 start = uDrone + vec3((aUv.x - 0.5) * 1.4, -0.6, (aUv.y - 0.5) * 1.1);
  float e = 1.0 - pow(1.0 - life, 3.0);
  vec3 p = mix(start, target, e);
  vColor = tex * 2.2 + vec3(0.03, 0.07, 0.16);
  vAlpha = smoothstep(0.0, 0.08, life) * (1.0 - smoothstep(0.72, 1.0, life));
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_PointSize = (1.2 + (1.0 - e) * 1.6) * uPixelRatio * (60.0 / -mv.z);
  gl_Position = projectionMatrix * mv;
}
`
const projFrag = /* glsl */ `
varying vec3 vColor;
varying float vAlpha;
void main() {
  float d = length(gl_PointCoord - 0.5);
  if (d > 0.5) discard;
  float a = smoothstep(0.5, 0.1, d) * vAlpha;
  gl_FragColor = vec4(vColor * a, a);
}
`

const coneVert = /* glsl */ `
varying float vY;
varying vec3 vN;
varying vec3 vW;
void main() {
  vY = uv.y;
  vN = normalize(normalMatrix * normal);
  vec4 w = modelMatrix * vec4(position, 1.0);
  vW = w.xyz;
  gl_Position = projectionMatrix * viewMatrix * w;
}
`
const coneFrag = /* glsl */ `
uniform float uTime;
varying float vY;
varying vec3 vN;
varying vec3 vW;
void main() {
  float bands = smoothstep(0.85, 1.0, fract(vY * 9.0 + uTime * 1.4));
  float edge = pow(1.0 - abs(vN.z), 2.0);
  float a = (0.04 + bands * 0.18 + edge * 0.05) * (0.35 + 0.65 * (1.0 - vY));
  gl_FragColor = vec4(vec3(0.25, 0.6, 1.0) * a * 2.0, a);
}
`

function Drone({ id }: { id: string }) {
  const gl = useThree((s) => s.gl)
  const body = useRef<THREE.Group>(null)
  const cone = useRef<THREE.Mesh>(null)
  const reticle = useRef<THREE.Group>(null)
  const rotors = useRef<THREE.Group>(null)
  const pos = useRef(new THREE.Vector3(NaN, DRONE_ALT, 0))
  const acc = useRef(0)

  const { tex } = useMemo(() => {
    const canvas = document.createElement('canvas')
    canvas.width = FW
    canvas.height = FH
    const tex = new THREE.CanvasTexture(canvas)
    tex.colorSpace = THREE.SRGBColorSpace
    return { ctx: canvas.getContext('2d')!, tex }
  }, [])

  const { projMat, coneMat } = useMemo(() => {
    const uv: number[] = []
    const ph: number[] = []
    const pos: number[] = []
    for (let y = 0; y < FH; y++)
      for (let x = 0; x < FW; x++) {
        uv.push((x + 0.5) / FW, (y + 0.5) / FH)
        ph.push(Math.random())
        pos.push(0, 0, 0)
      }
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
    geo.setAttribute('aUv', new THREE.Float32BufferAttribute(uv, 2))
    geo.setAttribute('aPhase', new THREE.Float32BufferAttribute(ph, 1))
    const projMat = new THREE.ShaderMaterial({
      vertexShader: projVert,
      fragmentShader: projFrag,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      uniforms: {
        uDrone: { value: new THREE.Vector3() },
        uCenter: { value: new THREE.Vector2() },
        uFrame: { value: tex },
        uTime: { value: 0 },
        uPixelRatio: { value: Math.min(2, gl.getPixelRatio()) },
        uSpan: { value: SPAN },
      },
    })
    const coneMat = new THREE.ShaderMaterial({
      vertexShader: coneVert,
      fragmentShader: coneFrag,
      transparent: true,
      depthWrite: false,
      side: THREE.DoubleSide,
      blending: THREE.AdditiveBlending,
      uniforms: { uTime: { value: 0 } },
    })
    return { projMat, coneMat }
  }, [gl, tex])

  useFrame((state, dt) => {
    const n = useStore.getState().nodes[id]
    if (!n) return
    const tx = n.props.x as number
    const tz = n.props.z as number
    const p = pos.current
    if (Number.isNaN(p.x)) p.set(tx, DRONE_ALT, tz)
    const prevX = p.x
    const prevZ = p.z
    const k = 1 - Math.exp(-dt * 6)
    p.x += (tx - p.x) * k
    p.z += (tz - p.z) * k
    const t = state.clock.elapsedTime
    const y = DRONE_ALT + Math.sin(t * 1.3 + id.length) * 0.35
    if (body.current) {
      body.current.position.set(p.x, y, p.z)
      const vx = p.x - prevX
      const vz = p.z - prevZ
      if (vx * vx + vz * vz > 1e-6) {
        const target = Math.atan2(vx, vz)
        body.current.rotation.y += (target - body.current.rotation.y) * 0.1
        body.current.rotation.x = Math.min(0.25, Math.hypot(vx, vz) * 2)
      }
    }
    if (rotors.current) rotors.current.children.forEach((r) => (r.rotation.y += dt * 40))
    if (cone.current) cone.current.position.set(p.x, y / 2, p.z)
    if (reticle.current) {
      reticle.current.position.set(p.x, 0.08, p.z)
      reticle.current.rotation.y = t * 0.3
    }
    projMat.uniforms.uDrone.value.set(p.x, y, p.z)
    projMat.uniforms.uCenter.value.set(p.x, p.z)
    projMat.uniforms.uTime.value = t
    coneMat.uniforms.uTime.value = t

    // Camera frame at ~8 Hz; coverage painted from the drone's GPS fix.
    acc.current += dt
    if (acc.current > 0.12) {
      acc.current = 0
      // (the ground projector that streamed a synthetic frame down as coloured points was removed: it read as noise)
      const st = useStore.getState()
      if (st.mode === 'live' || st.playing) paint(p.x, p.z)
    }
  })

  const callsign = useStore((s) => (s.nodes[id]?.props.callsign as string) ?? id)
  // live mode: what the detector sees right now under this drone (people / hazard) from the latest tier-1 frame
  const seen = useStore((s) => {
    const cam = s.nodes[id]?.props.camera as string | undefined
    const f = cam ? s.cameras[cam] : undefined
    if (!f || s.mode !== 'live') return ''
    const people = (f.detections ?? []).filter((d) => /person|worker|firefighter/.test(d.label)).length
    const hz = f.hazards?.length ? f.hazards[0].replace(/_/g, ' ') : f.scene && f.scene.label !== 'normal' && f.scene.conf > 0.5 ? f.scene.label.replace(/_/g, ' ') : ''
    return [people ? `${people} PEOPLE` : '', hz ? hz.toUpperCase() : ''].filter(Boolean).join(' · ')
  })

  return (
    <>
      <group ref={body}>
        <mesh>
          <boxGeometry args={[0.9, 0.25, 0.9]} />
          <meshBasicMaterial color="#6fb6ff" />
        </mesh>
        <group ref={rotors}>
          {[[-0.9, -0.9], [0.9, -0.9], [-0.9, 0.9], [0.9, 0.9]].map(([x, z], i) => (
            <mesh key={i} position={[x, 0.18, z]} rotation-x={-Math.PI / 2}>
              <ringGeometry args={[0.35, 0.55, 24, 1, 0, Math.PI * 1.4]} />
              <meshBasicMaterial color="#9fd0ff" transparent opacity={0.8} side={THREE.DoubleSide} />
            </mesh>
          ))}
        </group>
        <mesh position={[0, -0.3, 0]}>
          <sphereGeometry args={[0.18, 12, 12]} />
          <meshBasicMaterial color={[3, 5, 8]} toneMapped={false} />
        </mesh>
        <Html center position={[0, 2.2, 0]} zIndexRange={[5, 0]}>
          <div className="tag tag-drone">
            <span className="dot" />
            {callsign.toUpperCase()}
            <em>REC</em>
            {seen && <i className="seen">{seen}</i>}
          </div>
        </Html>
      </group>
      <mesh ref={cone} material={coneMat}>
        <coneGeometry args={[SPAN * 1.25, DRONE_ALT, 48, 1, true]} />
      </mesh>
      <group ref={reticle}>
        <mesh rotation-x={-Math.PI / 2}>
          <ringGeometry args={[SPAN * 1.22, SPAN * 1.28, 64]} />
          <meshBasicMaterial color={[0.25, 0.55, 1.1]} transparent opacity={0.45} toneMapped={false} />
        </mesh>
        {[0, 1, 2, 3].map((i) => (
          <mesh key={i} rotation-x={-Math.PI / 2} rotation-z={(i * Math.PI) / 2}>
            <ringGeometry args={[SPAN * 1.34, SPAN * 1.46, 16, 1, -0.22, 0.44]} />
            <meshBasicMaterial color={[0.45, 0.9, 1.8]} transparent opacity={0.8} toneMapped={false} />
          </mesh>
        ))}
      </group>
    </>
  )
}

export function Drones() {
  const ids = useStore((s) => Object.keys(s.nodes).filter((k) => s.nodes[k].label === 'Unit' && s.nodes[k].props.kind === 'drone').join(','))
  return (
    <>
      {ids.split(',').filter(Boolean).map((id) => (
        <Drone key={id} id={id} />
      ))}
    </>
  )
}
