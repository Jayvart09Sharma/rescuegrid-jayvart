import { useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../store'
import { drawAerial } from '../data/frames'
import { DRONE_ALT, DRONE_FOOTPRINT } from '../data/scenario'
import { coverageFraction } from '../scene/coverage'
import { ENV, STATUS_HEX } from '../config'
import { fmtClock } from '../data/clock'
import { Panel } from './Panel'
import { RadioPanel } from './Radio'
import { PLAYBACK } from './Starter'
import type { CameraFrame, LoggedEvent } from '../types'

const FEEDS = [
  { key: 'drone_vision', name: 'DRONE VISION', sub: 'D1 · D2 · D3 → vision model', model: 'VISION' },
  { key: 'radio_asr', name: 'RADIO CH3', sub: 'audio → local ASR', model: 'ASR' },
  { key: 'gps', name: 'UNIT GPS', sub: '4 units · 5 Hz', model: 'FIX' },
  { key: 'sensor', name: 'SENSORS', sub: 'seismic · gas · water', model: 'MQTT' },
  { key: 'field_report', name: 'FIELD REPORTS', sub: 'text ingest', model: 'TEXT' },
] as const

function Spark({ events, source, t }: { events: LoggedEvent[]; source: string; t: number }) {
  const bins = 28
  const win = 56
  const counts = new Array(bins).fill(0)
  for (const e of events) {
    if (e.source !== source) continue
    const age = t - e.t
    if (age < 0 || age >= win) continue
    counts[bins - 1 - Math.floor((age / win) * bins)]++
  }
  if (source === 'gps' && t > 3) counts.forEach((_, i) => (counts[i] = 0.6 + 0.3 * Math.sin(i * 1.7 + t)))
  const max = Math.max(1, ...counts)
  const d = counts.map((c, i) => `${i === 0 ? 'M' : 'L'}${(i / (bins - 1)) * 100},${22 - (c / max) * 18}`).join(' ')
  return (
    <svg viewBox="0 0 100 24" preserveAspectRatio="none" className="spark">
      <path d={`${d} L100,24 L0,24 Z`} className="spark-fill" />
      <path d={d} className="spark-line" />
    </svg>
  )
}

function Feeds() {
  const isLive = useStore((s) => s.mode === 'live')
  const events = useStore((s) => s.events)
  const t = useStore((s) => Math.floor(s.t * 2) / 2)
  const gps = useStore((s) => s.feedPulse.gps ?? 0)
  return (
    <Panel title="FEEDS" code="01" delay={0.2} right={<span className="badge-replay">{isLive ? 'LIVE' : 'REPLAY'}</span>}>
      <ul className="feeds">
        {FEEDS.map((f) => {
          const mine = events.filter((e) => e.source === f.key)
          const last = mine[mine.length - 1]
          const hot = (last && t - last.t < 3) || (f.key === 'gps' && t > 3)
          const count = f.key === 'gps' ? gps : mine.length
          return (
            <li key={f.key} className={hot ? 'hot' : ''}>
              <div className="f-head">
                <span className={`pulse ${hot ? 'on' : ''}`} />
                <b>{f.name}</b>
                <span className="f-model">{f.model}</span>
                <span className="f-count mono">{String(count).padStart(3, '0')}</span>
              </div>
              <div className="f-sub">{last ? `${last.entity} · ${last.claim}` : f.sub}</div>
              <Spark events={events} source={f.key} t={t} />
            </li>
          )
        })}
      </ul>
    </Panel>
  )
}

const PIP_W = 440
const PIP_H = 250

/** Live mode: the real frame the vision service just analysed for this camera, with its tier-1 boxes. */
function LiveFrame({ cam }: { cam: CameraFrame }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const img = useRef<HTMLImageElement | null>(null)
  useEffect(() => {
    if (!cam.frame) return
    const im = new Image()
    im.onload = () => {
      img.current = im
      const c = ref.current
      if (!c) return
      const ctx = c.getContext('2d')!
      const k = Math.min(PIP_W / im.naturalWidth, PIP_H / im.naturalHeight)
      const w = im.naturalWidth * k
      const h = im.naturalHeight * k
      const ox = (PIP_W - w) / 2
      const oy = (PIP_H - h) / 2
      ctx.fillStyle = '#04121a'
      ctx.fillRect(0, 0, PIP_W, PIP_H)
      ctx.drawImage(im, ox, oy, w, h)
      ctx.font = '600 9px "JetBrains Mono", monospace'
      for (const d of cam.detections ?? []) {
        const [x1, y1, x2, y2] = d.bbox_xyxy
        const x = ox + x1 * k
        const y = oy + y1 * k
        const bw = (x2 - x1) * k
        const bh = (y2 - y1) * k
        const hazard = !/^(car|truck|bus|person|ambulance|fire truck|police car|boat|helicopter)$/.test(d.label)
        ctx.strokeStyle = hazard ? STATUS_HEX.danger : STATUS_HEX.safe
        ctx.lineWidth = 1.5
        ctx.strokeRect(x, y, bw, bh)
        const label = `${d.label} ${d.conf.toFixed(2)}`
        ctx.fillStyle = ctx.strokeStyle
        ctx.fillRect(x, y - 11, ctx.measureText(label).width + 6, 11)
        ctx.fillStyle = '#000'
        ctx.fillText(label, x + 3, y - 2)
      }
      ctx.fillStyle = 'rgba(210,240,255,0.9)'
      const scene = cam.scene ? `${cam.scene.label.toUpperCase()} ${cam.scene.conf.toFixed(2)}` : ''
      ctx.fillText(`${cam.callsign.toUpperCase()}  ${scene}  ${cam.latency_ms ?? ''}ms`, 8, 14)
      ctx.fillText(`${cam.gate ?? ''}`, 8, PIP_H - 8)
    }
    im.src = cam.frame
  }, [cam])
  return <canvas ref={ref} width={PIP_W} height={PIP_H} />
}

/** Live mode with an upload running: the video itself loops, with the detector's boxes for the latest analysed frame
 *  drawn over it (boxes are in source-video pixels, so they scale with the element). */
function LiveVideo({ src, cam }: { src: string; cam?: CameraFrame }) {
  const vid = useRef<HTMLVideoElement>(null)
  const ovl = useRef<HTMLCanvasElement>(null)
  const latest = useRef(cam)
  latest.current = cam
  useEffect(() => {
    let raf = 0
    const loop = () => {
      raf = requestAnimationFrame(loop)
      const v = vid.current
      const c = ovl.current
      if (!v || !c) return
      const ctx = c.getContext('2d')!
      ctx.clearRect(0, 0, PIP_W, PIP_H)
      const f = latest.current
      if (!v.videoWidth) return
      const k = Math.min(PIP_W / v.videoWidth, PIP_H / v.videoHeight)
      const ox = (PIP_W - v.videoWidth * k) / 2
      const oy = (PIP_H - v.videoHeight * k) / 2
      ctx.font = '600 9px "JetBrains Mono", monospace'
      for (const d of f?.detections ?? []) {
        const [x1, y1, x2, y2] = d.bbox_xyxy
        const hazard = !/^(car|truck|bus|person|ambulance|fire truck|police car|boat|helicopter)$/.test(d.label)
        ctx.strokeStyle = hazard ? STATUS_HEX.danger : STATUS_HEX.safe
        ctx.lineWidth = 1.5
        ctx.strokeRect(ox + x1 * k, oy + y1 * k, (x2 - x1) * k, (y2 - y1) * k)
        const label = `${d.label} ${d.conf.toFixed(2)}`
        ctx.fillStyle = ctx.strokeStyle
        ctx.fillRect(ox + x1 * k, oy + y1 * k - 11, ctx.measureText(label).width + 6, 11)
        ctx.fillStyle = '#000'
        ctx.fillText(label, ox + x1 * k + 3, oy + y1 * k - 2)
      }
      ctx.fillStyle = 'rgba(210,240,255,0.9)'
      const scene = f?.scene ? `${f.scene.label.toUpperCase()} ${f.scene.conf.toFixed(2)}` : ''
      ctx.fillText(`${(f?.callsign ?? '').toUpperCase()}  ${scene}  ${f?.latency_ms ?? ''}ms`, 8, 14)
      if (f?.motion) ctx.fillText(`FLOW dx ${f.motion.dx.toFixed(1)} dy ${f.motion.dy.toFixed(1)}  ${f.gate ?? ''}`, 8, PIP_H - 8)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [])
  return (
    <>
      <video ref={vid} src={src} autoPlay muted loop playsInline width={PIP_W} height={PIP_H} style={{ objectFit: 'contain', background: '#04121a', display: 'block' }}
        onLoadedMetadata={(e) => { e.currentTarget.playbackRate = PLAYBACK }} onPlay={(e) => { e.currentTarget.playbackRate = PLAYBACK }} />
      <canvas ref={ovl} width={PIP_W} height={PIP_H} style={{ position: 'absolute', left: 0, top: 0, pointerEvents: 'none' }} />
    </>
  )
}

function DroneFeed() {
  const isLive = useStore((s) => s.mode === 'live')
  const droneIds = useStore((s) => Object.keys(s.nodes).filter((k) => s.nodes[k].label === 'Unit' && s.nodes[k].props.kind === 'drone').sort().join(','))
  const list = droneIds ? droneIds.split(',') : []
  const [pick, setDrone] = useState('Drone-1')
  const drone = list.includes(pick) ? pick : list[0] ?? pick
  const camId = useStore((s) => s.nodes[drone]?.props.camera as string | undefined)
  const cam = useStore((s) => (camId ? s.cameras[camId] : undefined))
  const video = useStore((s) => [...s.streams].reverse().find((st) => st.camera === camId && st.video && (st.state === 'running' || st.state === 'finished'))?.video)
  const liveFrame = isLive && cam && cam.frame ? cam : undefined
  const stale = liveFrame ? liveFrame.replayed || performance.now() - liveFrame.receivedAt > 5000 : false
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current
    if (!c) return
    const ctx = c.getContext('2d')!
    let raf = 0
    let last = 0
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop)
      if (now - last < 90) return
      last = now
      const { nodes } = useStore.getState()
      const d = nodes[drone]
      if (!d) return
      const cx = d.props.x as number
      const cz = d.props.z as number
      const time = now / 1000
      const dets = drawAerial(ctx, PIP_W, PIP_H, nodes, { cx, cz, span: DRONE_FOOTPRINT * 1.3, time, detections: true })
      ctx.save()
      ctx.font = '600 9px "JetBrains Mono", monospace'
      for (const det of dets) {
        const [x, y, w, h] = det.box
        ctx.strokeStyle = det.color
        ctx.lineWidth = 1.5
        const k = 7
        ctx.beginPath()
        ctx.moveTo(x, y + k); ctx.lineTo(x, y); ctx.lineTo(x + k, y)
        ctx.moveTo(x + w - k, y); ctx.lineTo(x + w, y); ctx.lineTo(x + w, y + k)
        ctx.moveTo(x + w, y + h - k); ctx.lineTo(x + w, y + h); ctx.lineTo(x + w - k, y + h)
        ctx.moveTo(x + k, y + h); ctx.lineTo(x, y + h); ctx.lineTo(x, y + h - k)
        ctx.stroke()
        ctx.globalAlpha = 0.14
        ctx.fillStyle = det.color
        ctx.fillRect(x, y, w, h)
        ctx.globalAlpha = 1
        const label = `${det.label} ${det.conf.toFixed(2)}`
        ctx.fillStyle = det.color
        ctx.fillRect(x, y - 12, ctx.measureText(label).width + 6, 11)
        ctx.fillStyle = '#000'
        ctx.fillText(label, x + 3, y - 3)
      }
      // crosshair + telemetry
      ctx.strokeStyle = 'rgba(200,235,255,0.55)'
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(PIP_W / 2 - 14, PIP_H / 2); ctx.lineTo(PIP_W / 2 - 4, PIP_H / 2)
      ctx.moveTo(PIP_W / 2 + 4, PIP_H / 2); ctx.lineTo(PIP_W / 2 + 14, PIP_H / 2)
      ctx.moveTo(PIP_W / 2, PIP_H / 2 - 14); ctx.lineTo(PIP_W / 2, PIP_H / 2 - 4)
      ctx.moveTo(PIP_W / 2, PIP_H / 2 + 4); ctx.lineTo(PIP_W / 2, PIP_H / 2 + 14)
      ctx.stroke()
      ctx.fillStyle = 'rgba(210,240,255,0.85)'
      ctx.fillText(`${drone.toUpperCase()}  ALT ${DRONE_ALT}m  HDG ${Math.round(((time * 20) % 360))}°`, 8, 14)
      ctx.fillText(`${(37.3352 - cz * 0.00009).toFixed(5)}N ${(121.8811 - cx * 0.00011).toFixed(5)}W`, 8, PIP_H - 8)
      ctx.fillStyle = 'rgba(255,60,80,0.95)'
      if (Math.floor(time * 2) % 2) {
        ctx.beginPath(); ctx.arc(PIP_W - 44, 11, 3.5, 0, Math.PI * 2); ctx.fill()
      }
      ctx.fillText('REC', PIP_W - 36, 14)
      ctx.restore()
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [drone])

  return (
    <Panel
      title="DRONE CAMERA"
      code="02"
      delay={0.35}
      right={
        <span className="tabs">
          {(isLive ? list : ['Drone-1', 'Drone-2', 'Drone-3']).map((d) => (
            <button key={d} className={d === drone ? 'on' : ''} onClick={() => setDrone(d)}>{d.replace('Drone-', 'D')}</button>
          ))}
        </span>
      }
    >
      <div className="pip">
        {isLive && !list.length ? (
          <div className="pip-empty" onClick={() => useStore.setState({ starter: true })}>NO DRONE STREAMS<br /><small>open STREAMS and upload a video per drone</small></div>
        ) : video ? (
          <LiveVideo src={`${ENV.restUrl}${video}`} cam={cam} />
        ) : liveFrame ? <LiveFrame cam={liveFrame} /> : <canvas ref={ref} width={PIP_W} height={PIP_H} />}
        <div className="pip-scan" />
        <span className="pip-badge">{video ? (cam && !cam.replayed && performance.now() - cam.receivedAt < 5000 ? `ANALYSING · ${camId?.toUpperCase()} · ${PLAYBACK}x · YOLO-WORLD + CLIP` : `REPLAY · ${camId?.toUpperCase()} · ANALYSED ONCE`) : liveFrame ? (stale ? `LAST ANALYSED FRAME ${fmtClock(liveFrame.ts)} · ${liveFrame.camera.toUpperCase()}` : `LIVE FRAME · ${liveFrame.camera.toUpperCase()} · YOLO-WORLD + CLIP`) : isLive ? 'NO STREAM' : 'PLACEHOLDER FRAME · REPLAY'}</span>
      </div>
    </Panel>
  )
}

function Stats() {
  const nodes = useStore((s) => Object.keys(s.nodes).length)
  const edges = useStore((s) => Object.keys(s.edges).length)
  const events = useStore((s) => s.events.length)
  const cloud = useStore((s) => s.cloudStats)
  const [cov, setCov] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setCov(coverageFraction()), 700)
    return () => clearInterval(id)
  }, [])
  const rows = useMemo(
    () => [
      ['GRAPH NODES', nodes],
      ['RELATIONSHIPS', edges],
      ['EVENTS FUSED', events],
      ['ON-DEVICE', '100%'],
      ['CLOUD OK / SLOW / FAIL', `${cloud.ok} / ${cloud.slow} / ${cloud.failed}`],
    ],
    [nodes, edges, events, cloud],
  )
  return (
    <Panel title="NEO4J · ON-DEVICE" code="03" delay={0.5}>
      <div className="cov">
        <svg viewBox="0 0 80 80">
          <circle cx="40" cy="40" r="34" className="cov-bg" />
          <circle cx="40" cy="40" r="34" className="cov-fg" strokeDasharray={`${cov * 213.6} 213.6`} />
        </svg>
        <div>
          <span className="mono cov-num">{(cov * 100).toFixed(0)}%</span>
          <label>AREA SCANNED</label>
        </div>
      </div>
      <dl className="stats">
        {rows.map(([k, v]) => (
          <div key={k as string}>
            <dt>{k}</dt>
            <dd className="mono">{v}</dd>
          </div>
        ))}
      </dl>
    </Panel>
  )
}

export function LeftRail() {
  return (
    <aside className="rail left">
      <Feeds />
      <DroneFeed />
      <RadioPanel />
      <Stats />
    </aside>
  )
}
