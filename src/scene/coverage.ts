// Scan-coverage map: a top-down texture where white = a drone camera has seen
// this ground. Built only from drone positions (graph facts), never from the
// scene. Particles sample it to decide whether they're in focus.
import * as THREE from 'three'
import { DRONE_FOOTPRINT, DRONE_MOTIONS } from '../data/scenario'
import { interp } from '../data/replaySource'

export const COVER_EXTENT = 60 // world units from centre to texture edge
const SIZE = 256

const canvas = document.createElement('canvas')
canvas.width = canvas.height = SIZE
const ctx = canvas.getContext('2d')!
ctx.fillStyle = '#000'
ctx.fillRect(0, 0, SIZE, SIZE)

export const coverageTexture = new THREE.CanvasTexture(canvas)
coverageTexture.minFilter = THREE.LinearFilter
coverageTexture.magFilter = THREE.LinearFilter
coverageTexture.generateMipmaps = false

const toPx = (w: number) => ((w + COVER_EXTENT) / (COVER_EXTENT * 2)) * SIZE

export function paint(x: number, z: number, radius = DRONE_FOOTPRINT) {
  const px = toPx(x)
  const pz = toPx(z)
  const r = (radius / (COVER_EXTENT * 2)) * SIZE
  const g = ctx.createRadialGradient(px, pz, 0, px, pz, r)
  g.addColorStop(0, 'rgba(255,255,255,1)')
  g.addColorStop(0.6, 'rgba(255,255,255,0.95)')
  g.addColorStop(1, 'rgba(255,255,255,0)')
  ctx.globalCompositeOperation = 'lighten'
  ctx.fillStyle = g
  ctx.beginPath()
  ctx.arc(px, pz, r, 0, Math.PI * 2)
  ctx.fill()
  ctx.globalCompositeOperation = 'source-over'
  coverageTexture.needsUpdate = true
}

/** Rebuild coverage as of scenario time t by re-flying the drone plans. */
export function resetCoverage(t: number) {
  ctx.fillStyle = '#000'
  ctx.fillRect(0, 0, SIZE, SIZE)
  for (let s = 0; s <= t; s += 0.35) {
    for (const m of DRONE_MOTIONS) {
      const [x, z] = interp(m.waypoints, s)
      paint(x, z)
    }
  }
  coverageTexture.needsUpdate = true
}

/** Live mode: the static city comes from the graph's seed (GIS data), so it starts fully known; drones then
 *  only add their lock-in flashes where they actually look. */
export function paintAll() {
  ctx.fillStyle = '#fff'
  ctx.fillRect(0, 0, SIZE, SIZE)
  coverageTexture.needsUpdate = true
}

/** Fraction of the city area scanned, 0..1. Cheap enough to call ~1 Hz. */
export function coverageFraction() {
  const inner = Math.floor(toPx(-50))
  const outer = Math.ceil(toPx(50))
  const data = ctx.getImageData(inner, inner, outer - inner, outer - inner).data
  let sum = 0
  for (let i = 0; i < data.length; i += 16) sum += data[i]
  return sum / (255 * (data.length / 16))
}
