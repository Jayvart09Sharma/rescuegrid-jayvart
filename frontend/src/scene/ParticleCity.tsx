// The twin as a point cloud. Every building/road/ground patch becomes particles
// that sit blurred and scattered until a drone footprint covers them, then snap
// into focus. Status + collapse come from a per-entity data texture that mirrors
// the graph. Nothing here decides what's true; it only draws the store.
import { useEffect, useMemo, useRef } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import * as THREE from 'three'
import { useStore } from '../store'
import { BASE_MS } from '../data/clock'
import { buildSeed, HALF, RIVER } from '../data/seed'
import { STATUS_HEX } from '../config'
import { coverageTexture, COVER_EXTENT } from './coverage'
import type { GraphNode, Status } from '../types'

const STATUS_CODE: Record<Status, number> = { normal: 0, warning: 1, danger: 2, conflict: 3, safe: 4 }
const KIND = { ground: 0, wall: 1, edge: 2, road: 3, roof: 4, water: 5 }

function rng(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6d2b79f5) >>> 0
    let t = Math.imul(s ^ (s >>> 15), s | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

interface Built {
  geometry: THREE.BufferGeometry
  entities: string[]
  boxes: { id: string; x: number; z: number; w: number; d: number; h: number }[]
}

function build(): Built {
  const seed = buildSeed()
  const r = rng(77)
  const pos: number[] = []
  const idx: number[] = []
  const kind: number[] = []
  const hN: number[] = []
  const rnd: number[] = []
  const center: number[] = []
  const entities: string[] = []
  const boxes: Built['boxes'] = []

  const push = (x: number, y: number, z: number, i: number, k: number, hn: number, c: [number, number, number, number]) => {
    pos.push(x, y, z)
    idx.push(i)
    kind.push(k)
    hN.push(hn)
    rnd.push(r(), r(), r(), r())
    center.push(...c)
  }

  for (const n of seed as GraphNode[]) {
    if (n.label === 'Building' || n.label === 'Hospital') {
      const { x, z, w, d, h } = n.props as { x: number; z: number; w: number; d: number; h: number }
      const i = entities.push(n.id) - 1
      boxes.push({ id: n.id, x, z, w, d, h })
      const c: [number, number, number, number] = [x, z, h, Math.max(w, d)]
      const x0 = x - w / 2
      const z0 = z - d / 2
      const floor = 0.55
      const col = 0.62
      // walls: scan-line rows, like a LiDAR sweep
      for (let y = 0.2; y < h; y += floor) {
        for (let s = 0; s < w; s += col) {
          push(x0 + s + r() * 0.1, y, z0, i, KIND.wall, y / h, c)
          push(x0 + s + r() * 0.1, y, z0 + d, i, KIND.wall, y / h, c)
        }
        for (let s = 0; s < d; s += col) {
          push(x0, y, z0 + s + r() * 0.1, i, KIND.wall, y / h, c)
          push(x0 + w, y, z0 + s + r() * 0.1, i, KIND.wall, y / h, c)
        }
      }
      // roof
      for (let a = 0.3; a < w; a += 0.75) for (let b = 0.3; b < d; b += 0.75) push(x0 + a, h, z0 + b, i, KIND.roof, 1, c)
      // bright edges
      const e = 0.22
      for (let y = 0; y < h; y += e) {
        push(x0, y, z0, i, KIND.edge, y / h, c)
        push(x0 + w, y, z0, i, KIND.edge, y / h, c)
        push(x0, y, z0 + d, i, KIND.edge, y / h, c)
        push(x0 + w, y, z0 + d, i, KIND.edge, y / h, c)
      }
      for (let s = 0; s < w; s += e) {
        push(x0 + s, h, z0, i, KIND.edge, 1, c)
        push(x0 + s, h, z0 + d, i, KIND.edge, 1, c)
        push(x0 + s, 0.05, z0, i, KIND.edge, 0, c)
        push(x0 + s, 0.05, z0 + d, i, KIND.edge, 0, c)
      }
      for (let s = 0; s < d; s += e) {
        push(x0, h, z0 + s, i, KIND.edge, 1, c)
        push(x0 + w, h, z0 + s, i, KIND.edge, 1, c)
        push(x0, 0.05, z0 + s, i, KIND.edge, 0, c)
        push(x0 + w, 0.05, z0 + s, i, KIND.edge, 0, c)
      }
    } else if (n.label === 'Road' || n.label === 'Bridge') {
      const a = n.props.a as number[]
      const b = n.props.b as number[]
      const rw = n.props.width as number
      const i = entities.push(n.id) - 1
      const horiz = a[1] === b[1]
      const len = horiz ? b[0] - a[0] : b[1] - a[1]
      const lift = n.label === 'Bridge' ? 0.6 : 0.02
      for (let s = 0; s < len; s += 0.42) {
        for (let q = -rw / 2; q <= rw / 2; q += rw / 4) {
          const [px, pz] = horiz ? [a[0] + s, a[1] + q] : [a[0] + q, a[1] + s]
          push(px + (r() - 0.5) * 0.12, lift, pz + (r() - 0.5) * 0.12, i, KIND.road, 0, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 0.6, len])
        }
      }
    }
  }

  // ground dust + river surface
  for (let k = 0; k < 16000; k++) {
    const x = (r() - 0.5) * HALF * 2.3
    const z = (r() - 0.5) * HALF * 2.3
    const water = z > RIVER.z0 && z < RIVER.z1 && Math.abs(x) < HALF * 1.15
    push(x, water ? -0.15 : 0, z, -1, water ? KIND.water : KIND.ground, 0, [x, z, 0, 1])
  }
  for (let k = 0; k < 7000; k++) {
    const x = (r() - 0.5) * HALF * 2.3
    const z = RIVER.z0 + r() * (RIVER.z1 - RIVER.z0)
    push(x, -0.15, z, -1, KIND.water, 0, [x, z, 0, 1])
  }

  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
  g.setAttribute('aIdx', new THREE.Float32BufferAttribute(idx, 1))
  g.setAttribute('aKind', new THREE.Float32BufferAttribute(kind, 1))
  g.setAttribute('aH', new THREE.Float32BufferAttribute(hN, 1))
  g.setAttribute('aRand', new THREE.Float32BufferAttribute(rnd, 4))
  g.setAttribute('aCenter', new THREE.Float32BufferAttribute(center, 4))
  g.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 120)
  return { geometry: g, entities, boxes }
}

const vert = /* glsl */ `
uniform float uTime;
uniform float uPixelRatio;
uniform sampler2D uCover;
uniform sampler2D uStatus;
uniform float uCoverExtent;
uniform vec3 uCol[5];
uniform float uIntro;
attribute float aIdx;
attribute float aKind;
attribute float aH;
attribute vec4 aRand;
attribute vec4 aCenter;
varying vec3 vColor;
varying float vAlpha;
varying float vSharp;

void main() {
  vec3 p = position;
  float cover = texture2D(uCover, (p.xz + uCoverExtent) / (2.0 * uCoverExtent)).r;

  vec4 st = vec4(0.0, -1.0, 0.0, -100.0);
  if (aIdx >= 0.0) st = texelFetch(uStatus, ivec2(int(aIdx), 0), 0);
  int code = int(st.r + 0.5);
  bool flagged = code != 0 && code != 4;

  // Focus: each particle locks in at its own threshold as the footprint passes.
  float thr = aRand.x * 0.55;
  float prog = smoothstep(thr, thr + 0.35, cover);
  if (flagged) prog = max(prog, 0.55);   // radio/sensor facts show even unscanned
  prog *= uIntro;
  float lockFlash = 4.0 * prog * (1.0 - prog);

  // Out of focus: scattered, drifting, lifted.
  vec3 drift = (aRand.yzw - 0.5) * vec3(3.5, 7.0, 3.5);
  drift.y = abs(drift.y) + 1.5;
  drift += vec3(sin(uTime * 0.4 + aRand.x * 40.0), sin(uTime * 0.3 + aRand.y * 30.0) * 0.6, cos(uTime * 0.35 + aRand.z * 40.0)) * 0.8;
  if (aKind == 0.0 || aKind == 5.0) drift *= 0.25;
  p += drift * (1.0 - prog);

  // Collapse: particles fall, bulge outward and settle into a rubble heap.
  if (st.g > -0.5) {
    float tc = uTime - st.g;
    float delay = aRand.x * 0.35 + (1.0 - aH) * 0.15;
    float tt = max(0.0, tc - delay);
    vec2 off = (aRand.yz - 0.5) * aCenter.w * 1.7;
    vec3 rubble = vec3(aCenter.x + off.x, 0.0, aCenter.y + off.y);
    float rr = length(off) / (aCenter.w * 0.85);
    rubble.y = max(0.04, (1.0 - rr) * aCenter.z * 0.3 * (0.4 + aRand.w * 0.6));
    float k = smoothstep(0.0, 1.8, tt);
    vec2 dir = normalize(p.xz - aCenter.xy + 0.0001);
    p.xz = mix(p.xz, rubble.xz, k) + dir * sin(k * 3.1416) * 2.2 * aRand.z;
    p.y = max(rubble.y, p.y - 0.5 * 16.0 * tt * tt);
    // pre-collapse tremor
    if (tc < 0.3) p.xz += vec2(sin(uTime * 80.0 + aRand.x * 9.0), cos(uTime * 70.0)) * 0.12;
  }

  vec3 col;
  float bright;
  if (aKind == 0.0) { col = vec3(0.10, 0.22, 0.26); bright = 0.35; }
  else if (aKind == 5.0) { col = vec3(0.08, 0.28, 0.42); bright = 0.5 + 0.3 * sin(uTime * 1.5 + p.x * 0.3); }
  else if (aKind == 3.0) { col = code == 0 ? vec3(0.28, 0.36, 0.40) : uCol[code]; bright = code == 0 ? 0.45 : 1.6; }
  else {
    col = uCol[code];
    bright = aKind == 2.0 ? 2.2 : (aKind == 4.0 ? 0.9 : 0.6 + aH * 0.6);
    if (code == 0) bright *= 0.38;
  }
  if (code == 2) bright *= 1.0 + 0.55 * sin(uTime * 6.0 + aRand.x);
  if (code == 3) bright *= step(0.0, sin(uTime * 9.0)) * 1.2 + 0.3;
  float since = uTime - st.a;
  float changeFlash = exp(-since * 1.8) * 3.0;
  if (st.b > 0.5) { bright *= 1.6; col = mix(col, vec3(0.6, 0.85, 1.0), 0.35); }

  // Scan sweep: brief white-hot flash at the moment of lock-in.
  vec3 focused = col * (bright + changeFlash) + vec3(0.7, 1.0, 0.95) * lockFlash * 1.4;
  vec3 blurred = vec3(0.10, 0.16, 0.22) * (0.5 + 0.5 * aRand.w);
  vColor = mix(blurred, focused, prog);

  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  float fade = smoothstep(260.0, 80.0, -mv.z);
  vAlpha = mix(0.09, aKind == 0.0 ? 0.55 : 0.95, prog) * fade;
  vSharp = prog;
  float size = mix(5.5, aKind == 2.0 ? 2.3 : 1.7, prog) + lockFlash * 2.5 + changeFlash * 0.8;
  gl_PointSize = size * uPixelRatio * (60.0 / -mv.z);
  gl_Position = projectionMatrix * mv;
}
`

const frag = /* glsl */ `
varying vec3 vColor;
varying float vAlpha;
varying float vSharp;
void main() {
  float d = length(gl_PointCoord - 0.5);
  if (d > 0.5) discard;
  float sharp = smoothstep(0.5, 0.15, d);
  float soft = smoothstep(0.5, 0.0, d) * 0.6;
  float a = mix(soft, sharp, vSharp) * vAlpha;
  gl_FragColor = vec4(vColor * a, a);
}
`

export function ParticleCity() {
  const { geometry, entities, boxes } = useMemo(build, [])
  const gl = useThree((s) => s.gl)
  const pick = useRef<THREE.InstancedMesh>(null)

  const statusTex = useMemo(() => {
    const data = new Float32Array(entities.length * 4)
    for (let i = 0; i < entities.length; i++) data.set([0, -1, 0, -100], i * 4)
    const t = new THREE.DataTexture(data, entities.length, 1, THREE.RGBAFormat, THREE.FloatType)
    t.needsUpdate = true
    return t
  }, [entities])

  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader: vert,
        fragmentShader: frag,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        uniforms: {
          uTime: { value: 0 },
          uPixelRatio: { value: Math.min(2, gl.getPixelRatio()) },
          uCover: { value: coverageTexture },
          uStatus: { value: statusTex },
          uCoverExtent: { value: COVER_EXTENT },
          uIntro: { value: 0 },
          uCol: {
            value: (['normal', 'warning', 'danger', 'conflict', 'safe'] as Status[]).map((s) => new THREE.Color(STATUS_HEX[s])),
          },
        },
      }),
    [gl, statusTex],
  )

  // Mirror graph status → data texture. Collapse/flash times are back-dated by
  // the event's age so a seek lands on the settled state, not a replay of it.
  const index = useMemo(() => new Map(entities.map((id, i) => [id, i])), [entities])
  const seen = useRef(new Map<string, string>())
  useEffect(() => {
    const sync = () => {
      const { nodes, flyTo, t } = useStore.getState()
      const data = statusTex.image.data as Float32Array
      const now = material.uniforms.uTime.value as number
      const hi = new Set(flyTo?.highlight ?? [])
      let dirty = false
      for (const [id, i] of index) {
        const n = nodes[id]
        if (!n) continue
        const key = `${n.status}|${n.props.collapsed ? 1 : 0}|${hi.has(id) ? 1 : 0}`
        if (seen.current.get(id) === key) continue
        const prev = seen.current.get(id)
        seen.current.set(id, key)
        const age = Math.max(0, t - (Date.parse(n.since) - BASE_MS) / 1000)
        data[i * 4] = STATUS_CODE[n.status]
        data[i * 4 + 1] = n.props.collapsed ? now - age : -1
        data[i * 4 + 2] = hi.has(id) ? 1 : 0
        if (prev && prev.split('|')[0] !== n.status) data[i * 4 + 3] = now - age
        dirty = true
      }
      if (dirty) statusTex.needsUpdate = true
    }
    sync()
    return useStore.subscribe((s, p) => {
      if (s.nodes !== p.nodes || s.flyTo !== p.flyTo) sync()
    })
  }, [index, material, statusTex])

  // Invisible boxes for hover/click picking.
  useEffect(() => {
    const m = pick.current
    if (!m) return
    const o = new THREE.Object3D()
    boxes.forEach((b, i) => {
      o.position.set(b.x, b.h / 2, b.z)
      o.scale.set(b.w, b.h, b.d)
      o.updateMatrix()
      m.setMatrixAt(i, o.matrix)
    })
    m.instanceMatrix.needsUpdate = true
    m.computeBoundingSphere()
  }, [boxes])

  useFrame((_, dt) => {
    material.uniforms.uTime.value += dt
    const intro = material.uniforms.uIntro
    intro.value = Math.min(1, intro.value + dt * 0.5)
  })

  return (
    <group>
      <points geometry={geometry} material={material} frustumCulled={false} />
      <instancedMesh
        ref={pick}
        args={[undefined, undefined, boxes.length]}
        onPointerMove={(e) => {
          e.stopPropagation()
          if (e.instanceId !== undefined) useStore.getState().setHover(boxes[e.instanceId].id)
        }}
        onPointerOut={() => useStore.getState().setHover(null)}
        onClick={(e) => {
          e.stopPropagation()
          if (e.instanceId !== undefined) useStore.getState().selectNode(boxes[e.instanceId].id)
        }}
      >
        <boxGeometry />
        <meshBasicMaterial colorWrite={false} depthWrite={false} />
      </instancedMesh>
    </group>
  )
}
