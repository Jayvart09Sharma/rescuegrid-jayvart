// Generated placeholder "drone frames": a top-down aerial render of whatever the
// graph says is under the camera. Stand-in for Aditya's footage until real
// frames land at /evidence/<raw_evidence_ref>.
import type { GraphNode } from '../types'
import { RIVER } from './seed'

const hash = (s: string) => {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619)
  return (h >>> 0) / 4294967296
}

function rand(seed: number) {
  let s = Math.floor(seed * 4294967296) >>> 0
  return () => {
    s = (s + 0x6d2b79f5) >>> 0
    let t = Math.imul(s ^ (s >>> 15), s | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

let noise: HTMLCanvasElement | null = null
function noiseTile() {
  if (noise) return noise
  noise = document.createElement('canvas')
  noise.width = noise.height = 128
  const c = noise.getContext('2d')!
  const img = c.createImageData(128, 128)
  const r = rand(0.42)
  for (let i = 0; i < img.data.length; i += 4) {
    const v = 30 + r() * 26
    img.data[i] = v
    img.data[i + 1] = v + 3
    img.data[i + 2] = v + 2
    img.data[i + 3] = 255
  }
  c.putImageData(img, 0, 0)
  return noise
}

export interface AerialOpts {
  cx: number
  cz: number
  /** half-width of ground covered, world units */
  span: number
  time: number
  detections?: boolean
}

export interface Detection {
  id: string
  label: string
  conf: number
  box: [number, number, number, number]
  color: string
}

/** Draws the frame; returns detection boxes it drew (for HUD overlays). */
export function drawAerial(ctx: CanvasRenderingContext2D, w: number, h: number, nodes: Record<string, GraphNode>, o: AerialOpts): Detection[] {
  const scale = w / (o.span * 2)
  const X = (x: number) => (x - o.cx) * scale + w / 2
  const Z = (z: number) => (z - o.cz) * scale + h / 2
  const inView = (x: number, z: number, pad = 8) => Math.abs(x - o.cx) < o.span + pad && Math.abs(z - o.cz) < o.span * (h / w) + pad
  const dets: Detection[] = []

  ctx.save()
  ctx.fillStyle = ctx.createPattern(noiseTile(), 'repeat')!
  ctx.fillRect(0, 0, w, h)

  // River
  ctx.fillStyle = '#0d2a36'
  ctx.fillRect(0, Z(RIVER.z0), w, (RIVER.z1 - RIVER.z0) * scale)
  ctx.fillStyle = 'rgba(120,200,220,0.08)'
  for (let i = 0; i < 6; i++) {
    const yy = Z(RIVER.z0) + ((i * 7 + o.time * 3) % ((RIVER.z1 - RIVER.z0) * scale))
    ctx.fillRect(0, yy, w, 1)
  }

  const list = Object.values(nodes)
  // Roads
  for (const n of list) {
    if (n.label !== 'Road' && n.label !== 'Bridge') continue
    const a = n.props.a as number[]
    const b = n.props.b as number[]
    if (!inView((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 12)) continue
    const rw = (n.props.width as number) * scale
    const horiz = a[1] === b[1]
    ctx.fillStyle = n.label === 'Bridge' ? '#3a3f44' : '#1d2226'
    if (horiz) ctx.fillRect(X(a[0]), Z(a[1]) - rw / 2, (b[0] - a[0]) * scale, rw)
    else ctx.fillRect(X(a[0]) - rw / 2, Z(a[1]), rw, (b[1] - a[1]) * scale)
    ctx.strokeStyle = 'rgba(230,220,160,0.35)'
    ctx.setLineDash([3, 4])
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(X(a[0]), Z(a[1]))
    ctx.lineTo(X(b[0]), Z(b[1]))
    ctx.stroke()
    ctx.setLineDash([])
    if (n.status === 'danger' || n.status === 'conflict') {
      const r = rand(hash(n.id))
      const mx = (a[0] + b[0]) / 2
      const mz = (a[1] + b[1]) / 2
      for (let i = 0; i < 70; i++) {
        const dx = (r() - 0.5) * 7
        const dz = (r() - 0.5) * 3
        const [px, pz] = horiz ? [mx + dx, mz + dz * 0.8] : [mx + dz * 0.8, mz + dx]
        ctx.fillStyle = `hsl(${25 + r() * 20},${10 + r() * 20}%,${25 + r() * 35}%)`
        const sz = (0.2 + r() * 0.6) * scale
        ctx.fillRect(X(px), Z(pz), sz, sz * (0.5 + r()))
      }
      if (o.detections) {
        const bw = (horiz ? 8 : 4) * scale
        const bh = (horiz ? 4 : 8) * scale
        dets.push({
          id: n.id, label: n.status === 'conflict' ? 'debris?' : 'road_obstruction', conf: n.status === 'conflict' ? 0.87 : 0.92,
          box: [X(mx) - bw / 2, Z(mz) - bh / 2, bw, bh], color: n.status === 'conflict' ? '#ffcf3a' : '#ff2d4b',
        })
      }
    }
  }

  // Buildings, facilities
  for (const n of list) {
    if (n.label !== 'Building' && n.label !== 'Hospital') continue
    const { x, z, w: bw, d: bd, h: bh } = n.props as { x: number; z: number; w: number; d: number; h: number }
    if (!inView(x, z)) continue
    const r = rand(hash(n.id))
    const px = X(x - bw / 2)
    const pz = Z(z - bd / 2)
    if (n.props.collapsed) {
      ctx.fillStyle = 'rgba(160,140,110,0.25)'
      ctx.beginPath()
      ctx.arc(X(x), Z(z), bw * 0.9 * scale, 0, Math.PI * 2)
      ctx.fill()
      for (let i = 0; i < 260; i++) {
        const ang = r() * Math.PI * 2
        const rad = Math.sqrt(r()) * bw * 0.85
        ctx.fillStyle = `hsl(${20 + r() * 25},${8 + r() * 25}%,${22 + r() * 45}%)`
        const sz = (0.25 + r() * 0.9) * scale
        ctx.fillRect(X(x + Math.cos(ang) * rad), Z(z + Math.sin(ang) * rad), sz, sz * (0.4 + r()))
      }
      // flicker of fire
      for (let i = 0; i < 6; i++) {
        const f = 0.5 + 0.5 * Math.sin(o.time * 9 + i * 2.1)
        ctx.fillStyle = `rgba(255,${120 + f * 80},40,${0.25 + f * 0.4})`
        ctx.beginPath()
        ctx.arc(X(x + (r() - 0.5) * bw), Z(z + (r() - 0.5) * bd), (0.4 + f * 0.5) * scale, 0, Math.PI * 2)
        ctx.fill()
      }
      if (o.detections) {
        const s = bw * 1.7 * scale
        dets.push({ id: n.id, label: 'structure_collapse', conf: 0.94, box: [X(x) - s / 2, Z(z) - s / 2, s, s], color: '#ff2d4b' })
      }
      continue
    }
    // shadow
    ctx.fillStyle = 'rgba(0,0,0,0.45)'
    ctx.fillRect(px + bh * 0.18 * scale, pz + bh * 0.12 * scale, bw * scale, bd * scale)
    const hospital = n.label === 'Hospital'
    const l = 38 + r() * 30 + Math.min(20, bh)
    ctx.fillStyle = hospital ? '#c9d3dc' : `hsl(${190 + r() * 40},${6 + r() * 10}%,${l}%)`
    ctx.fillRect(px, pz, bw * scale, bd * scale)
    ctx.strokeStyle = 'rgba(255,255,255,0.18)'
    ctx.strokeRect(px + 0.5, pz + 0.5, bw * scale - 1, bd * scale - 1)
    for (let i = 0; i < 3; i++) {
      ctx.fillStyle = `rgba(20,25,30,${0.3 + r() * 0.3})`
      ctx.fillRect(px + r() * bw * scale * 0.7, pz + r() * bd * scale * 0.7, 0.9 * scale, 0.7 * scale)
    }
    if (hospital) {
      ctx.fillStyle = '#d02b3a'
      const cx = X(x)
      const cz = Z(z)
      ctx.fillRect(cx - 0.4 * scale, cz - 1.4 * scale, 0.8 * scale, 2.8 * scale)
      ctx.fillRect(cx - 1.4 * scale, cz - 0.4 * scale, 2.8 * scale, 0.8 * scale)
    }
    if (n.status === 'warning' && o.detections) {
      const s = bw * 1.2 * scale
      dets.push({ id: n.id, label: 'facade_damage', conf: 0.71, box: [X(x) - s / 2, Z(z) - s / 2, s, s], color: '#ffcf3a' })
    }
  }

  // Gas plume
  for (const n of list) {
    if (n.label !== 'Hazard') continue
    const { x, z, r } = n.props as { x: number; z: number; r: number }
    const g = ctx.createRadialGradient(X(x), Z(z), 0, X(x), Z(z), r * scale)
    const f = 0.12 + 0.05 * Math.sin(o.time * 2)
    g.addColorStop(0, `rgba(255,190,90,${f * 2})`)
    g.addColorStop(1, 'rgba(255,190,90,0)')
    ctx.fillStyle = g
    ctx.fillRect(0, 0, w, h)
  }

  // Ground units (vehicles)
  for (const n of list) {
    if (n.label !== 'Unit' || n.props.kind === 'drone') continue
    const { x, z } = n.props as { x: number; z: number }
    if (!inView(x, z)) continue
    ctx.fillStyle = n.props.kind === 'ambulance' ? '#f2f2f2' : '#c8322e'
    ctx.fillRect(X(x) - 0.9 * scale, Z(z) - 0.5 * scale, 1.8 * scale, 1 * scale)
    const blink = Math.sin(o.time * 12) > 0
    ctx.fillStyle = blink ? '#4aa3ff' : '#ff3b3b'
    ctx.fillRect(X(x) - 0.2 * scale, Z(z) - 0.2 * scale, 0.4 * scale, 0.4 * scale)
    if (o.detections) {
      const s = 3 * scale
      dets.push({ id: n.id, label: 'vehicle', conf: 0.97, box: [X(x) - s / 2, Z(z) - s / 2, s, s], color: '#2f8cff' })
    }
  }

  // Sensor grain + vignette so it reads as a camera, not a map
  ctx.globalAlpha = 0.07
  ctx.drawImage(noiseTile(), (o.time * 97) % 128 - 128, (o.time * 53) % 128 - 128, w + 256, h + 256)
  ctx.globalAlpha = 1
  const v = ctx.createRadialGradient(w / 2, h / 2, h * 0.3, w / 2, h / 2, w * 0.75)
  v.addColorStop(0, 'rgba(0,0,0,0)')
  v.addColorStop(1, 'rgba(0,0,0,0.55)')
  ctx.fillStyle = v
  ctx.fillRect(0, 0, w, h)
  ctx.restore()
  return dets
}
