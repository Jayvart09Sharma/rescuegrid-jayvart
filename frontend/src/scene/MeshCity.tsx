// The twin as a reconstructed 3D city. Same inputs as ParticleCity (seed geometry, drone coverage,
// graph status) but drawn as lit meshes. Everything starts as a blueprint grid; after the page loads a
// reveal wave sweeps out from the drones' launch points and rebuilds it into a solid, coloured model (buildings rise
// floor by floor behind a scan band). Drone coverage reveals ahead of the wave. Around the graph's city
// (±50) sits a suburban ring out to ±OUTER: scenery only, not graph entities. Nothing here decides what's
// true; it only draws the store.
// Switch back to the point-cloud twin with ?twin=particles; ?reveal=all shows the whole city built at once.
import { useEffect, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Sky } from '@react-three/drei'
import * as THREE from 'three'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import { useStore } from '../store'
import { BASE_MS } from '../data/clock'
import { buildSeed, RIVER } from '../data/seed'
import { REVEAL_ALL, STATUS_HEX } from '../config'
import { DRONE_MOTIONS } from '../data/scenario'
import { coverageTexture, COVER_EXTENT } from './coverage'
import type { GraphNode, Status } from '../types'

const STATUS_CODE: Record<Status, number> = { normal: 0, warning: 1, danger: 2, conflict: 3, safe: 4 }
const FORCE_REVEAL = REVEAL_ALL
/** Afternoon sun from the south-west; the sky, the light and the shadows all use it. */
export const SUN = new THREE.Vector3(-60, 72, 48)
/** The graph's city spans ±CORE; the scenery ring extends the street grid to ±OUTER. */
const CORE = 50
const OUTER = 110
/** Load reveal: starts after WAVE_DELAY s from every drone launch point, front moves WAVE_SPEED units/s,
 *  WAVE_BAND units wide. Launch points come from the drones' flight plans (Staging Area A, and Drone 3's pad). */
const WAVE_DELAY = 1.2
const WAVE_SPEED = 9
const WAVE_ORIGINS: [number, number][] = DRONE_MOTIONS.map((m) => [m.waypoints[0][1], m.waypoints[0][2]] as [number, number]) // waypoints are [t, x, z]
  .filter((p, i, all) => all.findIndex((q) => Math.hypot(q[0] - p[0], q[1] - p[1]) < 1) === i)
const glslVec2s = (ps: [number, number][]) => ps.map(([x, z]) => `vec2(${x.toFixed(1)}, ${z.toFixed(1)})`).join(', ')
const WAVE_BAND = 14
const BUILD_SEC = 2.4

function rng(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6d2b79f5) >>> 0
    let t = Math.imul(s ^ (s >>> 15), s | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

// ─── shared GLSL ────────────────────────────────────────────────────────────
type Uniforms = Record<string, THREE.IUniform>

const COMMON = /* glsl */ `
uniform float uTime;
uniform float uIntro;
uniform float uForce;
uniform sampler2D uCover;
uniform float uExtent;
uniform float uWave;
const vec2 WAVE_O[${WAVE_ORIGINS.length}] = vec2[${WAVE_ORIGINS.length}](${glslVec2s(WAVE_ORIGINS)});
float waveAt(vec2 xz) {
  float w = 0.0;
  for (int i = 0; i < ${WAVE_ORIGINS.length}; i++) w = max(w, smoothstep(uWave, uWave - ${WAVE_BAND.toFixed(1)}, distance(xz, WAVE_O[i])));
  return w;
}
float coverAt(vec2 xz) {
  float drone = all(lessThan(abs(xz), vec2(uExtent))) ? texture2D(uCover, (xz + uExtent) / (2.0 * uExtent)).r : 0.0;
  return max(uForce, max(drone, waveAt(xz)));
}
float hash12(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
float vnoise(vec2 p) {
  vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash12(i), hash12(i + vec2(1.0, 0.0)), u.x), mix(hash12(i + vec2(0.0, 1.0)), hash12(i + vec2(1.0, 1.0)), u.x), u.y);
}
`

/** Patch a built-in material. The cache key matters: every patched material shares one onBeforeCompile
 *  source, so without a distinct key three.js would reuse the first compiled program for all of them. */
function patch<M extends THREE.Material>(mat: M, key: string, uniforms: Uniforms, edit: (s: { vertexShader: string; fragmentShader: string }) => void): M {
  mat.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms)
    edit(shader)
  }
  mat.customProgramCacheKey = () => key
  return mat
}

// ─── buildings ──────────────────────────────────────────────────────────────
const B_VERT_HEAD = /* glsl */ `
${COMMON}
uniform sampler2D uStatus;
uniform sampler2D uReveal;
attribute float aIdx;
attribute vec4 aDim;      // w, h, d, seed
varying vec3 vLocal;      // undeformed local coords in world units, y = 0 at street level
varying vec3 vObjN;
varying vec4 vDim;
varying vec4 vSt;
varying float vBuild;     // 0 = blueprint, 1 = fully rebuilt
varying float vCollapse;
`
const B_VERT_BODY = /* glsl */ `
#include <begin_vertex>
vec4 st = texelFetch(uStatus, ivec2(int(aIdx), 0), 0);
vSt = st;
vDim = aDim;
vObjN = normal;
vLocal = vec3(position.x * aDim.x, position.y * aDim.y, position.z * aDim.z);
float rv = texelFetch(uReveal, ivec2(int(aIdx), 0), 0).r;
vBuild = uForce > 0.5 ? 1.0 : (rv < -0.5 ? 0.0 : clamp((uTime - rv) / ${BUILD_SEC.toFixed(1)}, 0.0, 1.0));
vCollapse = 0.0;
if (st.g > -0.5) {
  float tc = uTime - st.g;
  float k = smoothstep(0.0, 1.8, tc - 0.15);
  vCollapse = k;
  float lean = (aDim.w - 0.5) * 0.9;
  transformed.y *= mix(1.0, 0.16, k);
  transformed.x += transformed.y * lean * k;
  transformed.z += transformed.y * 0.3 * k;
  if (tc > 0.0 && tc < 0.35) transformed.xz += vec2(sin(uTime * 80.0), cos(uTime * 70.0)) * 0.012;
}
`
const B_FRAG_HEAD = /* glsl */ `
${COMMON}
uniform vec3 uCol[5];
varying vec3 vLocal;
varying vec3 vObjN;
varying vec4 vDim;
varying vec4 vSt;
varying float vBuild;
varying float vCollapse;
`
/** Where the build-up front is, and whether this fragment is already rebuilt. Shared with the shadow pass. */
const B_REVEAL = /* glsl */ `
bool bRoof = vObjN.y > 0.5;
float revealH = vBuild * (vDim.y + 0.8) - 0.4;
float solid = bRoof ? step(vDim.y - 0.05, revealH) : step(vLocal.y, revealH);
`
const B_FRAG_BODY = /* glsl */ `
#include <color_fragment>
float W = vDim.x, H = vDim.y, D = vDim.z, seed = vDim.w;
int code = int(vSt.r + 0.5);
bool flagged = code == 1 || code == 2 || code == 3;
vec3 sc = uCol[code];
${B_REVEAL}
bool sideX = abs(vObjN.x) > 0.5;
float u = sideX ? vLocal.z + D * 0.5 : vLocal.x + W * 0.5;
float faceW = sideX ? D : W;
float v = vLocal.y;
float fh = H > 9.0 ? 0.85 : 0.95;
float e = bRoof ? min(min(vLocal.x + W * 0.5, W * 0.5 - vLocal.x), min(vLocal.z + D * 0.5, D * 0.5 - vLocal.z))
                : min(min(u, faceW - u), min(v, H - v));
float edge = 1.0 - smoothstep(0.03, 0.09, e);
float band = bRoof ? 0.0 : (1.0 - smoothstep(0.0, 0.3, abs(v - revealH))) * step(0.001, vBuild) * (1.0 - step(0.999, vBuild));

// blueprint grid: floors, columns and edges only
float fl = 1.0 - smoothstep(0.012, 0.04, abs(v - fh * floor(v / fh + 0.5)));
float cl = 1.0 - smoothstep(0.012, 0.035, abs(u - 1.6 * floor(u / 1.6 + 0.5)));
if (bRoof) {
  vec2 rg = abs(vLocal.xz - 1.6 * floor(vLocal.xz / 1.6 + 0.5));
  cl = 1.0 - smoothstep(0.012, 0.035, min(rg.x, rg.y)); fl = 0.0;
}
float grid = max(edge, max(fl * 0.8, cl * 0.6));
if (solid < 0.5 && grid < 0.35 && band < 0.1) discard;

// facade
vec3 facade = seed < 0.18 ? vec3(0.66, 0.63, 0.57)       // concrete
            : seed < 0.40 ? vec3(0.60, 0.26, 0.16)       // brick
            : seed < 0.58 ? vec3(0.80, 0.66, 0.46)       // sandstone
            : seed < 0.70 ? vec3(0.52, 0.60, 0.68)       // blue-grey panel
            : seed < 0.80 ? vec3(0.78, 0.55, 0.40)       // terracotta
            :               vec3(0.88, 0.86, 0.80);      // white render
bool tower = H > 12.0;
if (tower) facade = vec3(0.24, 0.36, 0.46);
facade *= 0.86 + 0.24 * vnoise(vec2(u, v) * 0.8 + seed * 31.0);
float wu = fract(u / 1.05), wv = fract(v / fh);
float win = tower ? step(0.05, wu) * step(wu, 0.95) * step(0.1, wv) * step(wv, 0.94)
                  : step(0.2, wu) * step(wu, 0.8) * step(0.28, wv) * step(wv, 0.84);
win *= step(fh * 0.9, v) * step(v, H - 0.22) * step(0.22, min(u, faceW - u));
float shop = (1.0 - step(fh * 0.9, v)) * step(0.12, v) * step(0.12, fract(u / 2.1)) * step(0.3, min(u, faceW - u));
float slab = (1.0 - smoothstep(0.0, 0.05, abs(v - fh * floor(v / fh + 0.5)))) * step(fh * 0.5, v) * (tower ? 0.0 : 1.0);
vec3 glass = vec3(0.16, 0.25, 0.34) + vec3(0.16, 0.20, 0.24) * vnoise(vec2(u * 0.3, v * 0.5) + seed * 7.0);
float lit = step(0.83, hash12(floor(vec2(u / 1.05, v / fh)) + seed * 97.0)) * win * (1.0 - vCollapse);
vec3 col = mix(facade, glass, max(win, shop));
col *= 1.0 - slab * 0.18;
if (bRoof) {
  float parapet = 1.0 - smoothstep(0.1, 0.16, e);
  vec2 unitC = vec2(hash12(vec2(seed, 1.0)) - 0.5, hash12(vec2(seed, 2.0)) - 0.5) * vec2(W, D) * 0.5;
  vec2 ub = abs(vLocal.xz - unitC) - vec2(0.45, 0.35);
  float unitBox = step(max(ub.x, ub.y), 0.0);
  col = mix(vec3(0.30, 0.30, 0.31) * (0.8 + 0.4 * vnoise(vLocal.xz * 3.0)), vec3(0.55, 0.54, 0.52), parapet);
  col = mix(col, vec3(0.62, 0.63, 0.64), unitBox);
  win = 0.0; lit = 0.0;
}
col = mix(col, vec3(0.44, 0.42, 0.39) * (0.7 + 0.5 * vnoise(vLocal.xz * 4.0 + v)), vCollapse * 0.75);

// state: status edges (fixed palette), change flash, Q&A highlight, scan band
float pulse = code == 2 ? 0.65 + 0.35 * sin(uTime * 6.0 + seed * 6.0) : code == 3 ? step(0.0, sin(uTime * 9.0)) * 0.9 + 0.2 : 1.0;
float stateK = code == 0 ? 0.0 : code == 4 ? 0.9 : 3.4;
float flash = exp(-(uTime - vSt.a) * 1.8) * 3.0;
float hi = vSt.b > 0.5 ? 1.0 : 0.0;
vec3 emis = sc * edge * stateK * pulse + sc * flash * (0.25 + edge);
if (flagged) col = mix(col, sc * 0.75, 0.32);
emis += vec3(0.4, 0.9, 1.0) * (edge * 2.2 + 0.25) * hi * (0.7 + 0.3 * sin(uTime * 5.0));
emis += vec3(1.0, 0.72, 0.42) * lit * 0.55;
emis += vec3(0.55, 1.0, 1.0) * band * 3.0;

float rough = mix(0.88, 0.22, max(win, shop));
if (solid < 0.5) {
  col = vec3(0.0);
  vec3 bp = flagged ? sc * 1.6 : vec3(0.28, 0.85, 1.0);
  emis = bp * grid * (0.35 + 0.9 * edge) * uIntro * pulse + vec3(0.55, 1.0, 1.0) * band * 3.0;
  rough = 1.0;
}
diffuseColor.rgb = col;
`

function buildingMaterials(uniforms: Uniforms) {
  const vert = (s: { vertexShader: string }) => {
    s.vertexShader = s.vertexShader.replace('#include <common>', `#include <common>\n${B_VERT_HEAD}`).replace('#include <begin_vertex>', B_VERT_BODY)
  }
  const mat = patch(new THREE.MeshStandardMaterial({ roughness: 0.85, metalness: 0.0 }), 'rg-building', uniforms, (s) => {
    vert(s)
    s.fragmentShader = s.fragmentShader
      .replace('#include <common>', `#include <common>\n${B_FRAG_HEAD}`)
      .replace('#include <color_fragment>', B_FRAG_BODY)
      .replace('#include <roughnessmap_fragment>', '#include <roughnessmap_fragment>\nroughnessFactor = rough;')
      .replace('#include <emissivemap_fragment>', '#include <emissivemap_fragment>\ntotalEmissiveRadiance += emis;')
  })
  // Shadows must follow the build-up too, or the still-blueprint parts of a building cast solid shadows.
  const depth = patch(new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking }), 'rg-building-depth', uniforms, (s) => {
    vert(s)
    s.fragmentShader = s.fragmentShader
      .replace('#include <common>', `#include <common>\n${B_FRAG_HEAD}`)
      .replace('#include <clipping_planes_fragment>', `#include <clipping_planes_fragment>\n${B_REVEAL}\nif (solid < 0.5) discard;`)
  })
  return { mat, depth }
}

interface Box { id: string; x: number; z: number; w: number; d: number; h: number; seed: number }

function Buildings({ uniforms, boxes, statusTex, revealTex }: { uniforms: Uniforms; boxes: Box[]; statusTex: THREE.DataTexture; revealTex: THREE.DataTexture }) {
  const ref = useRef<THREE.InstancedMesh>(null)
  const { geometry, mat, depth } = useMemo(() => {
    const g = new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0)
    g.setAttribute('aIdx', new THREE.InstancedBufferAttribute(new Float32Array(boxes.map((_, i) => i)), 1))
    g.setAttribute('aDim', new THREE.InstancedBufferAttribute(new Float32Array(boxes.flatMap((b) => [b.w, b.h, b.d, b.seed])), 4))
    const u = { ...uniforms, uStatus: { value: statusTex }, uReveal: { value: revealTex } }
    return { geometry: g, ...buildingMaterials(u) }
  }, [boxes, uniforms, statusTex, revealTex])

  useEffect(() => {
    const m = ref.current
    if (!m) return
    const o = new THREE.Object3D()
    boxes.forEach((b, i) => {
      o.position.set(b.x, 0, b.z)
      o.scale.set(b.w, b.h, b.d)
      o.updateMatrix()
      m.setMatrixAt(i, o.matrix)
    })
    m.instanceMatrix.needsUpdate = true
    m.computeBoundingSphere()
  }, [boxes])

  return (
    <instancedMesh
      ref={ref}
      args={[geometry, mat, boxes.length]}
      customDepthMaterial={depth}
      castShadow
      receiveShadow
      onPointerMove={(e) => {
        e.stopPropagation()
        const id = e.instanceId !== undefined ? boxes[e.instanceId].id : null
        useStore.getState().setHover(id && !id.startsWith('ctx-') ? id : null)
      }}
      onPointerOut={() => useStore.getState().setHover(null)}
      onClick={(e) => {
        e.stopPropagation()
        const id = e.instanceId !== undefined ? boxes[e.instanceId].id : null
        useStore.getState().selectNode(id && !id.startsWith('ctx-') ? id : null)
      }}
    />
  )
}

// ─── ground: blueprint grid until scanned, then streets, sidewalks, lots, parks, fields ──────────
const G_HEAD = /* glsl */ `
${COMMON}
varying vec3 vW;
`
const G_FRAG = /* glsl */ `
#include <color_fragment>
vec2 p = vW.xz;
if (p.y > ${(RIVER.z0 - 0.4).toFixed(1)} && p.y < ${(RIVER.z1 + 0.4).toFixed(1)}) discard;   // river channel, drawn by Water
float c = coverAt(p);
float prog = smoothstep(0.05, 0.45, c) * uIntro;
const float OUT = ${OUTER.toFixed(1)};
float kx = clamp(floor((p.x + OUT) / 10.0 + 0.5), 0.0, 2.0 * OUT / 10.0);
float kz = clamp(floor((p.y + OUT) / 10.0 + 0.5), 0.0, 2.0 * OUT / 10.0);
float dx = p.x - (-OUT + kx * 10.0);
float dz = p.y - (-OUT + kz * 10.0);
float hwx = 1.1;
float hwz = abs(-OUT + kz * 10.0) < 0.1 ? 1.6 : 1.1;     // Main St is wider
bool inCity = abs(p.x) < OUT + 1.7 && abs(p.y) < OUT + 1.7;
bool onV = inCity && abs(dx) < hwx && abs(p.y) <= OUT + hwz;
bool onH = inCity && abs(dz) < hwz && abs(p.x) <= OUT + hwx;
bool road = onV || onH;
bool walk = !road && inCity && (abs(dx) < hwx + 0.45 || abs(dz) < hwz + 0.45);
vec2 blk = -OUT + 5.0 + 10.0 * floor((p + OUT) / 10.0);
ivec2 bi = ivec2(floor((p + OUT) / 10.0));
int ph = bi.x * 7 + bi.y * 13;
bool outerPark = (abs(blk.x) > ${CORE}.0 || abs(blk.y) > ${CORE}.0) && ph - 11 * (ph / 11) == 0;
bool park = inCity && (outerPark || distance(blk, vec2(-5.0, 15.0)) < 0.1 || distance(blk, vec2(25.0, 25.0)) < 0.1 || distance(blk, vec2(-15.0, 25.0)) < 0.1);
bool staging = inCity && distance(blk, vec2(-35.0, 5.0)) < 0.1;
bool bank = p.y > ${(RIVER.z0 - 1.0).toFixed(1)} && p.y < ${(RIVER.z1 + 1.0).toFixed(1)};

float n1 = vnoise(p * 1.7), n2 = vnoise(p * 0.21 + 7.0), n3 = vnoise(p * 9.0);
vec3 grass = mix(vec3(0.20, 0.38, 0.10), vec3(0.38, 0.44, 0.16), n2) * (0.85 + 0.3 * n1);
vec3 col;
float rough = 0.95;
if (road) {
  col = vec3(0.16, 0.165, 0.17) * (0.8 + 0.35 * n3) * (0.9 + 0.2 * n2);
  rough = 0.8;
  bool inter = onV && onH;
  float a = onH ? p.x : p.y;
  float q = onH ? dz : dx;
  float hw = onH ? hwz : hwx;
  float other = onH ? dx : dz;
  float hwo = onH ? hwx : hwz;
  if (!inter) {
    bool mainSt = onH && hwz > 1.5;
    float center = mainSt ? step(abs(abs(q) - 0.07), 0.03) : step(abs(q), 0.04);
    float dash = mainSt ? step(abs(abs(q) - 0.8), 0.035) * step(fract(a / 1.4), 0.5) : 0.0;
    float edgeLn = step(abs(abs(q) - (hw - 0.12)), 0.025);
    float xwalk = step(hwo, abs(other)) * step(abs(other), hwo + 0.65) * step(fract(q * 2.6), 0.5) * step(abs(q), hw - 0.1);
    col = mix(col, vec3(0.62, 0.48, 0.12), center);
    col = mix(col, vec3(0.62), max(dash, max(edgeLn * 0.8, xwalk * 0.85)));
  }
} else if (walk) {
  col = vec3(0.62, 0.61, 0.58) * (0.85 + 0.25 * n3);
  col *= 1.0 - 0.12 * step(0.94, fract((onV ? p.y : p.x) / 0.9));
} else if (bank) {
  col = vec3(0.34, 0.33, 0.30) * (0.8 + 0.3 * n3);
} else if (park) {
  col = grass * 1.1;
  float path = step(abs(abs(p.x - blk.x) - abs(p.y - blk.y)), 0.18);
  col = mix(col, vec3(0.52, 0.47, 0.38), path);
} else if (staging) {
  col = vec3(0.14, 0.145, 0.15) * (0.85 + 0.3 * n3);
  col = mix(col, vec3(0.65), step(0.93, fract(p.x / 1.1)) * step(abs(p.y - blk.y), 2.8));
} else if (inCity) {
  col = mix(vec3(0.50, 0.49, 0.46), grass, step(0.62, n2)) * (0.85 + 0.25 * n3);
} else {
  vec3 dry = vec3(0.50, 0.44, 0.26);
  col = mix(grass, dry, smoothstep(0.45, 0.75, vnoise(p * 0.05)));
  col *= 0.9 + 0.2 * step(0.5, fract(dot(p, vec2(0.6, 0.8)) / 3.0)) * step(0.55, vnoise(p * 0.03 + 3.0));
}

// blueprint (not yet scanned)
vec2 g = abs(fract(p / 2.0) - 0.5);
float gridL = smoothstep(0.47, 0.5, max(g.x, g.y));
vec2 G = abs(fract(p / 10.0) - 0.5);
float major = smoothstep(0.485, 0.5, max(G.x, G.y));
float roadOutline = (road ? 1.0 : 0.0) * (1.0 - smoothstep(0.0, 0.08, min(abs(abs(dx) - hwx), abs(abs(dz) - hwz))));
float r = length(p);
float fade = smoothstep(OUT + 90.0, OUT + 20.0, r);
vec3 bp = (vec3(0.02, 0.07, 0.08) * gridL + vec3(0.03, 0.10, 0.12) * major + vec3(0.1, 0.4, 0.45) * roadOutline) * fade * uIntro;
float front = smoothstep(0.02, 0.25, c) * (1.0 - smoothstep(0.25, 0.7, c)) * (1.0 - uForce);

diffuseColor.rgb = mix(vec3(0.004, 0.01, 0.016), col, prog);
vec3 emis = bp * (1.0 - prog) + vec3(0.3, 0.9, 1.0) * front * 0.35;
rough = mix(1.0, rough, prog);
`

function Ground({ uniforms }: { uniforms: Uniforms }) {
  const mat = useMemo(
    () =>
      patch(new THREE.MeshStandardMaterial({ roughness: 0.95 }), 'rg-ground', uniforms, (s) => {
        s.vertexShader = s.vertexShader
          .replace('#include <common>', `#include <common>\nvarying vec3 vW;`)
          .replace('#include <begin_vertex>', '#include <begin_vertex>\nvW = (modelMatrix * vec4(transformed, 1.0)).xyz;')
        s.fragmentShader = s.fragmentShader
          .replace('#include <common>', `#include <common>\n${G_HEAD}`)
          .replace('#include <color_fragment>', G_FRAG)
          .replace('#include <roughnessmap_fragment>', '#include <roughnessmap_fragment>\nroughnessFactor = rough;')
          .replace('#include <emissivemap_fragment>', '#include <emissivemap_fragment>\ntotalEmissiveRadiance += emis;')
      }),
    [uniforms],
  )
  return (
    <mesh rotation-x={-Math.PI / 2} material={mat} receiveShadow onClick={() => useStore.getState().selectNode(null)}>
      <planeGeometry args={[560, 560]} />
    </mesh>
  )
}

// ─── river ──────────────────────────────────────────────────────────────────
const W_FRAG = /* glsl */ `
#include <color_fragment>
vec2 p = vW.xz;
float c = coverAt(p);
float prog = smoothstep(0.05, 0.45, c) * uIntro;
float fade = smoothstep(200.0, 130.0, length(p));
float ripple = sin(p.x * 1.3 + uTime * 1.4 + sin(p.y * 2.0)) * 0.5 + 0.5;
diffuseColor.rgb = mix(vec3(0.003, 0.01, 0.015), vec3(0.05, 0.14, 0.16) * (0.85 + 0.3 * vnoise(p * 0.4 + uTime * 0.05)), prog);
vec3 emis = vec3(0.05, 0.25, 0.3) * step(0.93, ripple) * (1.0 - prog) * fade * uIntro;
`
const W_NORMAL = /* glsl */ `
#include <normal_fragment_maps>
{
  vec2 q = vW.xz;
  float t = uTime;
  vec2 d = vec2(sin(q.x * 1.7 + t * 1.3) + sin(q.x * 3.1 - q.y * 2.3 + t * 2.1) * 0.5,
                cos(q.y * 2.1 - t * 1.1) + cos(q.y * 4.3 + q.x * 1.7 + t * 1.7) * 0.5) * 0.07;
  normal = normalize(normal + (viewMatrix * vec4(d.x, 0.0, d.y, 0.0)).xyz);
}
`
function Water({ uniforms }: { uniforms: Uniforms }) {
  const mat = useMemo(
    () =>
      patch(new THREE.MeshStandardMaterial({ roughness: 0.12, metalness: 0.0 }), 'rg-water', uniforms, (s) => {
        s.vertexShader = s.vertexShader
          .replace('#include <common>', `#include <common>\nvarying vec3 vW;`)
          .replace('#include <begin_vertex>', '#include <begin_vertex>\nvW = (modelMatrix * vec4(transformed, 1.0)).xyz;')
        s.fragmentShader = s.fragmentShader
          .replace('#include <common>', `#include <common>\n${G_HEAD}`)
          .replace('#include <color_fragment>', W_FRAG)
          .replace('#include <normal_fragment_maps>', W_NORMAL)
          .replace('#include <emissivemap_fragment>', '#include <emissivemap_fragment>\ntotalEmissiveRadiance += emis;')
      }),
    [uniforms],
  )
  const zc = (RIVER.z0 + RIVER.z1) / 2
  const wide = RIVER.z1 - RIVER.z0 + 0.8
  return (
    <group>
      <mesh rotation-x={-Math.PI / 2} position={[0, -0.32, zc]} material={mat} receiveShadow>
        <planeGeometry args={[560, wide]} />
      </mesh>
      {/* embankment walls down to the water line */}
      {[RIVER.z0 - 0.4, RIVER.z1 + 0.4].map((z) => (
        <mesh key={z} position={[0, -0.17, z]}>
          <boxGeometry args={[560, 0.34, 0.06]} />
          <meshStandardMaterial color="#4a4843" roughness={0.95} />
        </mesh>
      ))}
    </group>
  )
}

// ─── bridges ────────────────────────────────────────────────────────────────
function sampleCover(img: Uint8ClampedArray, size: number, x: number, z: number) {
  if (Math.abs(x) >= COVER_EXTENT || Math.abs(z) >= COVER_EXTENT) return 0
  const px = Math.max(0, Math.min(size - 1, Math.floor(((x + COVER_EXTENT) / (COVER_EXTENT * 2)) * size)))
  const pz = Math.max(0, Math.min(size - 1, Math.floor(((z + COVER_EXTENT) / (COVER_EXTENT * 2)) * size)))
  return img[(pz * size + px) * 4] / 255
}

function Bridge({ x, z0, z1, width, reveal }: { x: number; z0: number; z1: number; width: number; reveal: { current: Map<string, number> } }) {
  const solid = useRef<THREE.Group>(null)
  const lines = useRef<THREE.LineSegments>(null)
  const len = z1 - z0
  const deckW = width + 0.5
  const edges = useMemo(() => new THREE.EdgesGeometry(new THREE.BoxGeometry(deckW, 0.3, len)), [deckW, len])
  const key = `${x},${z0}`
  useFrame(() => {
    const k = FORCE_REVEAL ? 1 : reveal.current.get(key) ?? 0
    if (solid.current) {
      solid.current.scale.y = Math.max(0.001, k)
      solid.current.visible = k > 0.01
    }
    if (lines.current) (lines.current.material as THREE.LineBasicMaterial).opacity = 0.8 * (1 - k)
  })
  const concrete = <meshStandardMaterial color="#8d8a84" roughness={0.9} />
  return (
    <group position={[x, 0, (z0 + z1) / 2]}>
      <group ref={solid}>
        <mesh position={[0, 0.3, 0]} castShadow receiveShadow>
          <boxGeometry args={[deckW, 0.3, len - 2.4]} />
          {concrete}
        </mesh>
        {[-1, 1].map((s) => (
          <mesh key={s} position={[0, 0.2, s * (len / 2 - 0.6)]} rotation-x={s * 0.2} castShadow receiveShadow>
            <boxGeometry args={[deckW, 0.3, 1.25]} />
            {concrete}
          </mesh>
        ))}
        {[-1, 1].map((s) => (
          <mesh key={`r${s}`} position={[s * (deckW / 2 - 0.06), 0.58, 0]} castShadow>
            <boxGeometry args={[0.1, 0.26, len - 2.4]} />
            <meshStandardMaterial color="#b5b1a8" roughness={0.7} />
          </mesh>
        ))}
        {[-2.2, 2.2].map((dz) => (
          <mesh key={`p${dz}`} position={[0, -0.2, dz]} castShadow>
            <boxGeometry args={[deckW * 0.5, 0.8, 0.5]} />
            {concrete}
          </mesh>
        ))}
      </group>
      <lineSegments ref={lines} geometry={edges} position={[0, 0.3, 0]}>
        <lineBasicMaterial color="#48d8ff" transparent opacity={0.8} toneMapped={false} />
      </lineSegments>
    </group>
  )
}

// ─── trees and parked cars: grow in where the ground has been scanned ──────────
const GROW_HEAD = /* glsl */ `
${COMMON}
`
const GROW_BODY = /* glsl */ `
#include <begin_vertex>
#ifdef USE_INSTANCING
vec2 org = (modelMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0)).xz;
float pg = smoothstep(0.25, 0.65, coverAt(org)) * uIntro;
transformed *= pg;
#endif
`
function growMaterial(uniforms: Uniforms, key: string, params: THREE.MeshStandardMaterialParameters) {
  return patch(new THREE.MeshStandardMaterial(params), key, uniforms, (s) => {
    s.vertexShader = s.vertexShader.replace('#include <common>', `#include <common>\n${GROW_HEAD}`).replace('#include <begin_vertex>', GROW_BODY)
  })
}

/** Scenery-ring blocks left as parks. Same rule as the ground shader's `outerPark`. */
function isOuterPark(bx: number, bz: number) {
  if (Math.abs(bx) < CORE && Math.abs(bz) < CORE) return false
  const ix = Math.floor((bx + OUTER) / 10)
  const iz = Math.floor((bz + OUTER) / 10)
  return (ix * 7 + iz * 13) % 11 === 0
}

/** Suburban ring around the graph's city: houses, low shops and parks out to ±OUTER. Scenery only. */
function contextBoxes(): Box[] {
  const r = rng(4242)
  const out: Box[] = []
  let n = 0
  const add = (x: number, z: number, w: number, d: number, h: number) => out.push({ id: `ctx-${n++}`, x, z, w, d, h, seed: r() })
  for (let bx = -OUTER + 5; bx < OUTER; bx += 10) {
    for (let bz = -OUTER + 5; bz < OUTER; bz += 10) {
      if (Math.abs(bx) < CORE && Math.abs(bz) < CORE) continue
      if (bz > RIVER.z0 - 5 && bz < RIVER.z1 + 5) continue
      if (isOuterPark(bx, bz)) continue
      const ring = Math.max(Math.abs(bx), Math.abs(bz))
      if (r() < (ring < 75 ? 0.3 : 0.12)) {
        // a strip of shops / small offices
        add(bx - 1.8, bz, 3.4, 6.4, 2.2 + r() * 4.5)
        add(bx + 2.2, bz, 2.8, 6.4, 1.8 + r() * 3)
      } else {
        // four houses with yards; one lot sometimes left empty
        for (const [ox, oz] of [[-2.2, -2.2], [2.2, -2.2], [-2.2, 2.2], [2.2, 2.2]]) {
          if (r() < 0.12) continue
          add(bx + ox + (r() - 0.5) * 0.5, bz + oz + (r() - 0.5) * 0.5, 2.2 + r() * 0.9, 2.0 + r() * 0.9, 0.9 + r() * 1.4)
        }
      }
    }
  }
  return out
}

function onRoadOrRiver(x: number, z: number, margin: number) {
  if (z > RIVER.z0 - 1.5 && z < RIVER.z1 + 1.5) return true
  if (Math.abs(x) > OUTER + 2 || Math.abs(z) > OUTER + 2) return false
  const dx = Math.abs(x - (-OUTER + Math.round((x + OUTER) / 10) * 10))
  const dz = Math.abs(z - (-OUTER + Math.round((z + OUTER) / 10) * 10))
  return dx < 1.1 + margin || dz < 1.6 + margin
}

function Trees({ uniforms, boxes }: { uniforms: Uniforms; boxes: Box[] }) {
  const trunks = useRef<THREE.InstancedMesh>(null)
  const crowns = useRef<THREE.InstancedMesh>(null)
  const spots = useMemo(() => {
    const r = rng(9)
    const out: { x: number; z: number; s: number }[] = []
    const clear = (x: number, z: number) =>
      !onRoadOrRiver(x, z, 0.5) && !boxes.some((b) => Math.abs(x - b.x) < b.w / 2 + 0.4 && Math.abs(z - b.z) < b.d / 2 + 0.4)
    // parks, the school field and the scenery ring's parks
    const parks: number[][] = [[-5, 15], [25, 25], [-15, 25]]
    for (let bx = -OUTER + 5; bx < OUTER; bx += 10) for (let bz = -OUTER + 5; bz < OUTER; bz += 10) if (isOuterPark(bx, bz)) parks.push([bx, bz])
    for (const [cx, cz] of parks) {
      for (let k = 0; k < 26; k++) {
        const x = cx + (r() - 0.5) * 6.4
        const z = cz + (r() - 0.5) * 6.4
        if (Math.abs(Math.abs(x - cx) - Math.abs(z - cz)) > 0.5 && clear(x, z)) out.push({ x, z, s: 0.8 + r() * 0.5 })
      }
    }
    // odd trees in lots and yards, woods and hedgerows beyond the ring
    for (let k = 0; k < 9000 && out.length < 3200; k++) {
      const x = (r() - 0.5) * 420
      const z = (r() - 0.5) * 420
      const inTown = Math.abs(x) < OUTER + 2 && Math.abs(z) < OUTER + 2
      if (inTown ? r() > 0.18 : r() > 0.5) continue
      if (clear(x, z)) out.push({ x, z, s: 0.7 + r() * 0.8 })
    }
    return out
  }, [boxes])

  const { trunkGeo, crownGeo, trunkMat, crownMat } = useMemo(
    () => ({
      trunkGeo: new THREE.CylinderGeometry(0.06, 0.09, 0.7, 5).translate(0, 0.35, 0),
      crownGeo: new THREE.IcosahedronGeometry(0.55, 1).scale(1, 1.15, 1).translate(0, 1.15, 0),
      trunkMat: growMaterial(uniforms, 'rg-trunk', { color: '#4a3526', roughness: 0.95 }),
      crownMat: growMaterial(uniforms, 'rg-crown', { color: '#ffffff', roughness: 0.9, flatShading: true }),
    }),
    [uniforms],
  )

  useEffect(() => {
    const o = new THREE.Object3D()
    const r = rng(21)
    const c = new THREE.Color()
    spots.forEach((t, i) => {
      o.position.set(t.x, 0, t.z)
      o.rotation.y = r() * 6.28
      o.scale.setScalar(t.s)
      o.updateMatrix()
      trunks.current?.setMatrixAt(i, o.matrix)
      crowns.current?.setMatrixAt(i, o.matrix)
      c.setHSL(0.22 + r() * 0.08, 0.45 + r() * 0.2, 0.16 + r() * 0.1)
      crowns.current?.setColorAt(i, c)
    })
    for (const m of [trunks.current, crowns.current]) {
      if (!m) continue
      m.instanceMatrix.needsUpdate = true
      if (m.instanceColor) m.instanceColor.needsUpdate = true
      m.computeBoundingSphere()
    }
  }, [spots])

  return (
    <group>
      <instancedMesh ref={trunks} args={[trunkGeo, trunkMat, spots.length]} castShadow />
      <instancedMesh ref={crowns} args={[crownGeo, crownMat, spots.length]} castShadow receiveShadow>
        {/* created up front so the shader compiles with per-instance colour */}
        <instancedBufferAttribute attach="instanceColor" args={[new Float32Array(spots.length * 3).fill(1), 3]} />
      </instancedMesh>
    </group>
  )
}

function Cars({ uniforms }: { uniforms: Uniforms }) {
  const ref = useRef<THREE.InstancedMesh>(null)
  const { geo, mat } = useMemo(() => {
    const body = new THREE.BoxGeometry(1.15, 0.26, 0.5).translate(0, 0.2, 0)
    const cabin = new THREE.BoxGeometry(0.62, 0.22, 0.44).translate(-0.05, 0.43, 0)
    return { geo: mergeGeometries([body, cabin])!, mat: growMaterial(uniforms, 'rg-car', { color: '#ffffff', roughness: 0.35, metalness: 0.3 }) }
  }, [uniforms])
  const cars = useMemo(() => {
    const r = rng(33)
    const out: { x: number; z: number; ry: number }[] = []
    const lines = (2 * OUTER) / 10 + 1
    for (let k = 0; k < 4000 && out.length < 700; k++) {
      const vertical = r() < 0.5
      const line = -OUTER + Math.floor(r() * lines) * 10
      const along = -OUTER + 1 + r() * (2 * OUTER - 2)
      const hw = !vertical && line === 0 ? 1.6 : 1.1
      const side = r() < 0.5 ? -1 : 1
      const off = line + side * (hw - 0.4)
      const [x, z] = vertical ? [off, along] : [along, off]
      const cross = vertical ? Math.abs(z - (-OUTER + Math.round((z + OUTER) / 10) * 10)) : Math.abs(x - (-OUTER + Math.round((x + OUTER) / 10) * 10))
      if (cross < 2.6) continue
      if (z > RIVER.z0 - 1.5 && z < RIVER.z1 + 1.5) continue
      if (out.some((c) => Math.hypot(c.x - x, c.z - z) < 1.4)) continue
      out.push({ x, z, ry: (vertical ? Math.PI / 2 : 0) + (side < 0 ? Math.PI : 0) })
    }
    return out
  }, [])
  useEffect(() => {
    const m = ref.current
    if (!m) return
    const o = new THREE.Object3D()
    const c = new THREE.Color()
    const palette = ['#d9d9d6', '#1d1f22', '#8c9196', '#6b1b1b', '#1f3552', '#e0dccf', '#3c4a3a', '#a8a39a']
    const r = rng(5)
    cars.forEach((car, i) => {
      o.position.set(car.x, 0, car.z)
      o.rotation.y = car.ry
      o.updateMatrix()
      m.setMatrixAt(i, o.matrix)
      m.setColorAt(i, c.set(palette[Math.floor(r() * palette.length)]))
    })
    m.instanceMatrix.needsUpdate = true
    if (m.instanceColor) m.instanceColor.needsUpdate = true
    m.computeBoundingSphere()
  }, [cars])
  return (
    <instancedMesh ref={ref} args={[geo, mat, cars.length]} castShadow receiveShadow>
      <instancedBufferAttribute attach="instanceColor" args={[new Float32Array(cars.length * 3).fill(1), 3]} />
    </instancedMesh>
  )
}

// ─── collapse: falling chunks and a dust cloud ───────────────────────────────
function Rubble({ box, index, statusTex, uniforms }: { box: Box; index: number; statusTex: THREE.DataTexture; uniforms: Uniforms }) {
  const ref = useRef<THREE.InstancedMesh>(null)
  const N = 90
  const chunks = useMemo(() => {
    const r = rng(index * 13 + 1)
    return Array.from({ length: N }, () => {
      const a = r() * Math.PI * 2
      const rad = Math.sqrt(r()) * Math.max(box.w, box.d) * 0.85
      const heap = Math.max(0.05, (1 - rad / (Math.max(box.w, box.d) * 0.9)) * box.h * 0.22)
      return {
        from: new THREE.Vector3(box.x + (r() - 0.5) * box.w * 0.8, box.h * (0.3 + r() * 0.7), box.z + (r() - 0.5) * box.d * 0.8),
        to: new THREE.Vector3(box.x + Math.cos(a) * rad, heap * r(), box.z + Math.sin(a) * rad),
        s: 0.18 + r() * 0.45,
        delay: r() * 0.5,
        spin: new THREE.Vector3(r() * 6, r() * 6, r() * 6),
      }
    })
  }, [box, index])
  const geo = useMemo(() => new THREE.DodecahedronGeometry(1, 0), [])
  const dust = useMemo(() => {
    const r = rng(index * 7 + 3)
    const g = new THREE.BufferGeometry()
    const n = 700
    const pos = new Float32Array(n * 3)
    const rnd = new Float32Array(n * 4)
    for (let i = 0; i < n; i++) rnd.set([r(), r(), r(), r()], i * 4)
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3))
    g.setAttribute('aRand', new THREE.BufferAttribute(rnd, 4))
    g.boundingSphere = new THREE.Sphere(new THREE.Vector3(box.x, 4, box.z), 30)
    const m = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      uniforms: { uT: { value: 0 }, uC: { value: new THREE.Vector3(box.x, 0, box.z) }, uR: { value: Math.max(box.w, box.d) } },
      vertexShader: /* glsl */ `
        uniform float uT; uniform vec3 uC; uniform float uR; attribute vec4 aRand; varying float vA;
        void main() {
          float t = max(0.0, uT - aRand.w * 0.6);
          float a = aRand.x * 6.2832;
          float spread = uR * (0.4 + 1.6 * (1.0 - exp(-t * 0.5))) * (0.3 + aRand.y);
          vec3 p = uC + vec3(cos(a) * spread, 0.3 + aRand.z * 3.5 * (1.0 - exp(-t * 0.35)) + t * 0.18, sin(a) * spread);
          vA = smoothstep(0.0, 0.6, t) * (1.0 - smoothstep(4.0, 14.0, t)) * 0.28;
          vec4 mv = modelViewMatrix * vec4(p, 1.0);
          gl_PointSize = (40.0 + aRand.y * 70.0) * (1.0 + t * 0.15) / -mv.z;
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: /* glsl */ `
        varying float vA;
        void main() { float d = length(gl_PointCoord - 0.5); gl_FragColor = vec4(vec3(0.62, 0.58, 0.52), smoothstep(0.5, 0.0, d) * vA); }`,
    })
    return { g, m }
  }, [box, index])

  useFrame(() => {
    const m = ref.current
    const start = (statusTex.image.data as Float32Array)[index * 4 + 1]
    if (!m || start < -0.5) return
    const tc = (uniforms.uTime.value as number) - start
    dust.m.uniforms.uT.value = tc
    const o = new THREE.Object3D()
    chunks.forEach((c, i) => {
      const tt = Math.max(0, tc - 0.15 - c.delay)
      const k = Math.min(1, tt / 1.1)
      o.position.lerpVectors(c.from, c.to, k * k * (3 - 2 * k))
      o.position.y = Math.max(c.to.y, c.from.y - 8 * tt * tt)
      o.rotation.set(c.spin.x * k, c.spin.y * k, c.spin.z * k)
      o.scale.setScalar(tt > 0 ? c.s : 0.0001)
      o.updateMatrix()
      m.setMatrixAt(i, o.matrix)
    })
    m.instanceMatrix.needsUpdate = true
  })

  return (
    <group>
      <instancedMesh ref={ref} args={[geo, undefined, N]} castShadow receiveShadow frustumCulled={false}>
        <meshStandardMaterial color="#8a857c" roughness={0.95} flatShading />
      </instancedMesh>
      <points geometry={dust.g} material={dust.m} frustumCulled={false} />
    </group>
  )
}

// ─── lighting: golden hour, matches the drone footage ────────────────────────
function Lighting() {
  const sun = useRef<THREE.DirectionalLight>(null)
  useEffect(() => {
    const l = sun.current
    if (!l) return
    const cam = l.shadow.camera as THREE.OrthographicCamera
    cam.left = -OUTER - 20; cam.right = OUTER + 20; cam.top = OUTER + 20; cam.bottom = -OUTER - 20; cam.near = 1; cam.far = 520
    cam.updateProjectionMatrix()
  }, [])
  return (
    <>
      <Sky distance={450} sunPosition={SUN.toArray()} turbidity={3.5} rayleigh={0.9} mieCoefficient={0.004} mieDirectionalG={0.8} />
      <hemisphereLight args={['#d6e6ff', '#6b5a45', 1.7]} />
      <ambientLight intensity={0.35} />
      <directionalLight
        ref={sun}
        position={SUN.clone().multiplyScalar(2.2).toArray()}
        color="#fff1dc"
        intensity={3.8}
        castShadow
        shadow-mapSize={[4096, 4096]}
        shadow-bias={-0.0004}
        shadow-normalBias={0.03}
      />
    </>
  )
}

// ─── the city ───────────────────────────────────────────────────────────────
export function MeshCity() {
  const seed = useMemo(() => buildSeed(), [])
  const boxes = useMemo<Box[]>(() => {
    const r = rng(77)
    const graph = (seed as GraphNode[])
      .filter((n) => n.label === 'Building' || n.label === 'Hospital')
      .map((n) => {
        const p = n.props as { x: number; z: number; w: number; d: number; h: number }
        return { id: n.id, x: p.x, z: p.z, w: p.w, d: p.d, h: p.h, seed: r() }
      })
    return [...graph, ...contextBoxes()]
  }, [seed])
  const bridges = useMemo(
    () => (seed as GraphNode[]).filter((n) => n.label === 'Bridge').map((n) => ({ id: n.id, a: n.props.a as number[], b: n.props.b as number[], width: n.props.width as number })),
    [seed],
  )

  const uniforms = useMemo<Uniforms>(
    () => ({
      uTime: { value: 0 },
      uIntro: { value: 0 },
      uForce: { value: FORCE_REVEAL ? 1 : 0 },
      uCover: { value: coverageTexture },
      uExtent: { value: COVER_EXTENT },
      uWave: { value: FORCE_REVEAL ? 1e4 : -WAVE_BAND },
      uCol: { value: (['normal', 'warning', 'danger', 'conflict', 'safe'] as Status[]).map((s) => new THREE.Color(STATUS_HEX[s])) },
    }),
    [],
  )
  const [statusTex, revealTex] = useMemo(() => {
    const mk = (fill: number[]) => {
      const data = new Float32Array(boxes.length * 4)
      for (let i = 0; i < boxes.length; i++) data.set(fill, i * 4)
      const t = new THREE.DataTexture(data, boxes.length, 1, THREE.RGBAFormat, THREE.FloatType)
      t.needsUpdate = true
      return t
    }
    return [mk([0, -1, 0, -100]), mk([-1, 0, 0, 0])]
  }, [boxes])

  // Mirror graph status → data texture, exactly as ParticleCity does: collapse/flash times are
  // back-dated by the event's age so a seek lands on the settled state, not a replay of it.
  const index = useMemo(() => new Map(boxes.map((b, i) => [b.id, i])), [boxes])
  const seen = useRef(new Map<string, string>())
  useEffect(() => {
    const sync = () => {
      const { nodes, flyTo, t } = useStore.getState()
      const data = statusTex.image.data as Float32Array
      const now = uniforms.uTime.value as number
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
  }, [index, uniforms, statusTex])

  // Rebuild trigger: sample the drone-coverage map at each building (4 Hz). A building starts rising
  // the moment a camera has covered it; after a backwards seek the coverage is cleared and so is it.
  const bridgeReveal = useRef(new Map<string, number>())
  const lastSample = useRef(0)
  const primed = useRef(false)
  useFrame((_, dt) => {
    uniforms.uTime.value += dt
    uniforms.uIntro.value = Math.min(1, (uniforms.uIntro.value as number) + dt * 0.5)
    const now = uniforms.uTime.value as number
    if (!FORCE_REVEAL) uniforms.uWave.value = (now - WAVE_DELAY) * WAVE_SPEED - WAVE_BAND
    const wave = uniforms.uWave.value as number
    const waveAt = (x: number, z: number) => {
      const d = Math.min(...WAVE_ORIGINS.map(([ox, oz]) => Math.hypot(x - ox, z - oz)))
      const k = Math.min(1, Math.max(0, (wave - d) / WAVE_BAND))
      return k * k * (3 - 2 * k)
    }
    for (const [k, v] of bridgeReveal.current) if (v > 0 && v < 1) bridgeReveal.current.set(k, Math.min(1, v + dt / 1.5))
    if (now - lastSample.current < 0.25) return
    lastSample.current = now
    const cv = coverageTexture.image as HTMLCanvasElement
    const size = cv.width
    const img = cv.getContext('2d')!.getImageData(0, 0, size, size).data
    const data = revealTex.image.data as Float32Array
    let dirty = false
    boxes.forEach((b, i) => {
      const c = Math.max(
        waveAt(b.x, b.z),
        sampleCover(img, size, b.x, b.z),
        sampleCover(img, size, b.x - b.w * 0.35, b.z - b.d * 0.35),
        sampleCover(img, size, b.x + b.w * 0.35, b.z + b.d * 0.35),
      )
      const thr = 0.35 + b.seed * 0.3
      if (c >= thr && data[i * 4] < 0) {
        data[i * 4] = primed.current ? now : now - 10 // already covered when the twin opened: show it built
        dirty = true
      } else if (c < 0.08 && data[i * 4] >= 0) {
        data[i * 4] = -1
        dirty = true
      }
    })
    if (dirty) revealTex.needsUpdate = true
    for (const br of bridges) {
      const key = `${br.a[0]},${br.a[1]}`
      const c = Math.max(waveAt(br.a[0], (br.a[1] + br.b[1]) / 2), sampleCover(img, size, br.a[0], (br.a[1] + br.b[1]) / 2))
      const cur = bridgeReveal.current.get(key) ?? 0
      if (c >= 0.4 && cur === 0) bridgeReveal.current.set(key, primed.current ? 0.001 : 1)
      else if (c < 0.08 && cur > 0) bridgeReveal.current.set(key, 0)
    }
    primed.current = true
  })

  const collapsedKey = useStore((s) => boxes.filter((b) => s.nodes[b.id]?.props.collapsed).map((b) => b.id).join(','))
  const collapsed = collapsedKey ? collapsedKey.split(',') : []

  return (
    <group>
      <Lighting />
      <Ground uniforms={uniforms} />
      <Water uniforms={uniforms} />
      <Buildings uniforms={uniforms} boxes={boxes} statusTex={statusTex} revealTex={revealTex} />
      {bridges.map((br) => (
        <Bridge key={br.id} x={br.a[0]} z0={br.a[1]} z1={br.b[1]} width={br.width} reveal={bridgeReveal} />
      ))}
      <Trees uniforms={uniforms} boxes={boxes} />
      <Cars uniforms={uniforms} />
      {collapsed.map((id) => {
        const i = index.get(id)!
        return <Rubble key={id} box={boxes[i]} index={i} statusTex={statusTex} uniforms={uniforms} />
      })}
    </group>
  )
}
