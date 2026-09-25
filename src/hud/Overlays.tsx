import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { nodePos, prettyId, useStore } from '../store'
import { fmtClock } from '../data/clock'
import { drawAerial } from '../data/frames'
import { ENV, STATUS_HEX, STATUS_LABEL } from '../config'
import { useAnalyser } from './Radio'
import type { GraphNode, LoggedEvent } from '../types'

// ─── Boot sequence ────────────────────────────────────────────────────────
const BOOT = [
  'ZTK LINK ............ ZGX NANO #07',
  'ZRT STATUS .......... vision · asr · llm  SERVING',
  'NEO4J ............... graph online · temporal + provenance',
  'EVENT BUS ........... 5 feeds subscribed',
  'SCENARIO ............ EQ-1 downtown · replay adapters armed',
  'TWIN ................ awaiting drone scan',
]
export function Boot() {
  const [n, setN] = useState(0)
  const [gone, setGone] = useState(false)
  useEffect(() => {
    const id = setInterval(() => setN((v) => v + 1), 230)
    const done = setTimeout(() => setGone(true), 230 * BOOT.length + 700)
    return () => { clearInterval(id); clearTimeout(done) }
  }, [])
  return (
    <AnimatePresence>
      {!gone && (
        <motion.div className="boot" exit={{ opacity: 0, scale: 1.08, filter: 'blur(8px)' }} transition={{ duration: 0.7 }}>
          <div className="boot-inner">
            <div className="boot-title">RESCUEGRID</div>
            <div className="boot-bar"><i style={{ width: `${Math.min(100, (n / BOOT.length) * 100)}%` }} /></div>
            {BOOT.slice(0, n).map((l) => (
              <div key={l} className="boot-line"><span>▸</span>{l}<b>OK</b></div>
            ))}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

// ─── Alerts + flash ─────────────────────────────────────────────────────────
export function Alerts() {
  const alerts = useStore((s) => s.alerts)
  useEffect(() => {
    if (!alerts.length) return
    const last = alerts[alerts.length - 1]
    const id = setTimeout(() => useStore.getState().dismissAlert(last.id), 6500)
    return () => clearTimeout(id)
  }, [alerts])
  return (
    <div className="alerts">
      <AnimatePresence>
        {alerts.map((a) => (
          <motion.div
            key={a.id}
            layout
            className={`alert s-${a.severity}`}
            initial={{ opacity: 0, y: -40, scaleX: 0.2 }}
            animate={{ opacity: 1, y: 0, scaleX: 1 }}
            exit={{ opacity: 0, x: 80 }}
            transition={{ type: 'spring', stiffness: 380, damping: 26 }}
            onClick={() => useStore.getState().dismissAlert(a.id)}
          >
            <span className="al-icon">{a.severity === 'safe' ? '◆' : '▲'}</span>
            <div>
              <div className="al-title glitch" data-text={a.title.toUpperCase()}>{a.title.toUpperCase()}</div>
              <div className="al-sub">{a.sub}</div>
            </div>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
}

export function Flash() {
  const shock = useStore((s) => s.shock)
  return (
    <AnimatePresence>
      {shock.nonce > 0 && (
        <motion.div
          key={shock.nonce}
          className={`flash s-${shock.severity}`}
          initial={{ opacity: 1 }}
          animate={{ opacity: 0 }}
          transition={{ duration: 1.4, ease: 'easeOut' }}
        />
      )}
    </AnimatePresence>
  )
}

// ─── Hover tooltip ──────────────────────────────────────────────────────────
export function HoverTip() {
  const id = useStore((s) => s.hoverId)
  const n = useStore((s) => (s.hoverId ? s.nodes[s.hoverId] : undefined))
  const [p, setP] = useState({ x: 0, y: 0 })
  useEffect(() => {
    const f = (e: PointerEvent) => setP({ x: e.clientX, y: e.clientY })
    window.addEventListener('pointermove', f)
    return () => window.removeEventListener('pointermove', f)
  }, [])
  if (!id || !n) return null
  const src = n.sources.filter((s) => s.source !== 'seed')
  return (
    <div className="hovertip" style={{ left: p.x + 16, top: p.y + 16 }}>
      <b>{n.id}</b>
      <span className={`st s-${n.status}`}>{STATUS_LABEL[n.status]}</span>
      <div className="mono">since {fmtClock(n.since)}</div>
      <div className="mono dim">{src.length ? src.map((s) => s.source).join(' + ') : 'seed · not yet reported'}</div>
    </div>
  )
}

// ─── Provenance drawer ──────────────────────────────────────────────────────
function FrameEvidence({ e }: { e: LoggedEvent }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const [real, setReal] = useState<string | null>(null)
  useEffect(() => {
    // Real frame if the vision adapter has published it; placeholder otherwise.
    const img = new Image()
    img.onload = () => setReal(img.src)
    img.src = `/evidence/${e.raw_evidence_ref}`
  }, [e.raw_evidence_ref])
  useEffect(() => {
    if (real) return
    const c = ref.current!
    const ctx = c.getContext('2d')!
    let raf = 0
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop)
      const { nodes } = useStore.getState()
      const pos = nodePos(nodes[e.entity]) ?? [0, 0]
      const dets = drawAerial(ctx, c.width, c.height, nodes, { cx: pos[0], cz: pos[1], span: 12, time: now / 1000, detections: true })
      const d = dets.find((x) => x.id === e.entity) ?? dets[0]
      if (d) {
        const [x, y, w, h] = d.box
        ctx.strokeStyle = d.color
        ctx.lineWidth = 2
        ctx.strokeRect(x, y, w, h)
        ctx.fillStyle = d.color
        ctx.font = '600 11px "JetBrains Mono", monospace'
        const label = `${e.claim} ${e.confidence.toFixed(2)}`
        ctx.fillRect(x, y - 15, ctx.measureText(label).width + 8, 14)
        ctx.fillStyle = '#000'
        ctx.fillText(label, x + 4, y - 4)
      }
      ctx.fillStyle = 'rgba(220,240,255,0.8)'
      ctx.font = '10px "JetBrains Mono", monospace'
      ctx.fillText(e.raw_evidence_ref, 8, c.height - 8)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [e, real])
  return real ? <img className="evidence-img" src={real} /> : <canvas ref={ref} className="evidence-img" width={420} height={240} />
}

/** Live mode: the actual recording behind a radio event, with the spectrum reacting to the voice. */
function RealAudio({ src }: { src: string }) {
  const [el, setEl] = useState<HTMLAudioElement | null>(null)
  const canvas = useAnalyser(el)
  const [playing, setPlaying] = useState(false)
  const [missing, setMissing] = useState(false)
  if (missing) return null
  return (
    <div className={`radio-player ${playing ? 'on' : ''}`}>
      <button onClick={() => { if (!el) return; if (el.paused) void el.play(); else el.pause() }}>{playing ? '◼' : '▶'}</button>
      <canvas ref={canvas} width={300} height={44} className="radio-wave" />
      <audio ref={setEl} src={src} preload="auto" crossOrigin="anonymous" onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onError={() => setMissing(true)} />
    </div>
  )
}

function AudioEvidence({ e }: { e: LoggedEvent }) {
  const live = useStore((s) => s.mode === 'live')
  const [playing, setPlaying] = useState(false)
  const bars = useRef(Array.from({ length: 64 }, (_, i) => 0.25 + Math.abs(Math.sin(i * 1.3 + e.t) * Math.cos(i * 0.37)) * 0.75))
  const play = () => {
    if (!('speechSynthesis' in window) || !e.detail) return
    speechSynthesis.cancel()
    const u = new SpeechSynthesisUtterance(e.detail)
    u.rate = 1.08
    u.pitch = 0.85
    u.onend = () => setPlaying(false)
    setPlaying(true)
    speechSynthesis.speak(u)
  }
  useEffect(() => () => speechSynthesis.cancel(), [])
  if (live && ENV.restUrl && e.raw_evidence_ref.startsWith('radio/')) {
    return (
      <div className="audio-ev">
        <RealAudio src={`${ENV.restUrl}/evidence/${e.raw_evidence_ref}`} />
        <div className="transcript"><label>ASR TRANSCRIPT · FASTER-WHISPER ON THE NANO</label>“{e.detail}”</div>
        <div className="mono dim">{e.raw_evidence_ref} · real recording</div>
      </div>
    )
  }
  return (
    <div className="audio-ev">
      <button onClick={play} className={playing ? 'on' : ''}>{playing ? '◼' : '▶'}</button>
      <div className={`wave ${playing ? 'playing' : ''}`}>
        {bars.current.map((h, i) => (
          <i key={i} style={{ height: `${h * 100}%`, animationDelay: `${(i % 16) * 0.04}s` }} />
        ))}
      </div>
      <div className="transcript">
        <label>ASR TRANSCRIPT</label>“{e.detail}”
      </div>
      <div className="mono dim">{e.raw_evidence_ref} · playback via local TTS</div>
    </div>
  )
}

function SensorEvidence({ e }: { e: LoggedEvent }) {
  const pts = Array.from({ length: 60 }, (_, i) => {
    const base = 0.12 + Math.sin(i * 0.6) * 0.03 + Math.sin(i * 2.1) * 0.02
    return i > 48 ? Math.min(0.95, base + (i - 48) * 0.09) : base
  })
  const d = pts.map((v, i) => `${i ? 'L' : 'M'}${(i / 59) * 400},${120 - v * 110}`).join(' ')
  const col = STATUS_HEX[e.severity]
  return (
    <div className="sensor-ev">
      <svg viewBox="0 0 400 124">
        {[30, 60, 90].map((y) => <line key={y} x1="0" x2="400" y1={y} y2={y} className="grid" />)}
        <path d={`${d} L400,124 L0,124Z`} fill={col} opacity={0.12} />
        <path d={d} fill="none" stroke={col} strokeWidth="2" className="draw" />
        <circle cx="400" cy={120 - pts[59] * 110} r="4" fill={col} />
      </svg>
      <div className="mono">{e.detail}</div>
      <div className="mono dim">{e.raw_evidence_ref}</div>
    </div>
  )
}

function Evidence({ e }: { e: LoggedEvent }) {
  if (e.source === 'drone_vision') return <FrameEvidence e={e} />
  if (e.source === 'radio_asr') return <AudioEvidence e={e} />
  if (e.source === 'sensor') return <SensorEvidence e={e} />
  return (
    <div className="text-ev">
      {e.detail && <p>“{e.detail}”</p>}
      <div className="mono dim">{e.raw_evidence_ref}</div>
    </div>
  )
}

function Ring({ v, color }: { v: number; color: string }) {
  return (
    <svg viewBox="0 0 60 60" className="ring">
      <circle cx="30" cy="30" r="25" className="ring-bg" />
      <motion.circle
        cx="30" cy="30" r="25" stroke={color} className="ring-fg"
        initial={{ strokeDasharray: '0 157' }}
        animate={{ strokeDasharray: `${v * 157} 157` }}
        transition={{ duration: 0.9, ease: 'easeOut' }}
      />
      <text x="30" y="34">{Math.round(v * 100)}</text>
    </svg>
  )
}

function EventView({ e }: { e: LoggedEvent }) {
  const nodes = useStore((s) => s.nodes)
  const writes = Object.values(nodes).filter((n) => n.sources.some((s) => s.eventId === e.id))
  const col = STATUS_HEX[e.severity]
  return (
    <>
      <div className="pv-head" style={{ borderColor: col }}>
        <span className={`st s-${e.severity}`}>{STATUS_LABEL[e.severity]}</span>
        <h2>{e.title}</h2>
      </div>
      <div className="pv-grid">
        <Ring v={e.confidence} color={col} />
        <dl>
          <div><dt>SOURCE</dt><dd className="mono">{e.source}</dd></div>
          <div><dt>TIMESTAMP</dt><dd className="mono">{fmtClock(e.timestamp)} · {e.timestamp}</dd></div>
          <div><dt>ENTITY</dt><dd className="mono">{e.entity}</dd></div>
          <div><dt>CLAIM</dt><dd className="mono">{e.claim}</dd></div>
          <div><dt>EVIDENCE REF</dt><dd className="mono">{e.raw_evidence_ref}</dd></div>
        </dl>
      </div>
      <div className="pv-sec">EVIDENCE</div>
      <Evidence e={e} />
      <div className="pv-sec">GRAPH WRITES</div>
      <ul className="writes">
        {writes.map((n) => (
          <li key={n.id} onClick={() => useStore.getState().selectNode(n.id)}>
            <span className={`dotc s-${n.status}`} />
            <b>{n.id}</b>
            <span className="mono dim">{n.label} · {n.status}</span>
          </li>
        ))}
        {!writes.length && <li className="dim">none (corroborating report only)</li>}
      </ul>
      <div className="pv-actions">
        <button onClick={() => useStore.getState().flyToSignal({ target: e.entity, highlight: [e.entity], answer: '' })}>⌖ FLY TO {e.entity.toUpperCase()}</button>
      </div>
    </>
  )
}

function NodeView({ n }: { n: GraphNode }) {
  const allEdges = useStore((s) => s.edges)
  const edges = Object.values(allEdges).filter((e) => e.from === n.id || e.to === n.id)
  const events = useStore((s) => s.events)
  const props = Object.entries(n.props).filter(([k]) => !['a', 'b', 'route', 'x', 'z'].includes(k))
  return (
    <>
      <div className="pv-head" style={{ borderColor: STATUS_HEX[n.status] }}>
        <span className={`st s-${n.status}`}>{STATUS_LABEL[n.status]}</span>
        <h2>{prettyId(n.id)}</h2>
      </div>
      <dl className="pv-dl">
        <div><dt>LABEL</dt><dd className="mono">:{n.label}</dd></div>
        <div><dt>SINCE</dt><dd className="mono">{fmtClock(n.since)}</dd></div>
        <div><dt>LAST CONFIRMED</dt><dd className="mono">{fmtClock(n.lastConfirmed)}</dd></div>
        {props.map(([k, v]) => (
          <div key={k}><dt>{k.toUpperCase()}</dt><dd className="mono">{Array.isArray(v) ? v.join(' · ') : typeof v === 'number' ? +v.toFixed(2) : String(v)}</dd></div>
        ))}
      </dl>
      <div className="pv-sec">PROVENANCE</div>
      <ul className="writes">
        {n.sources.map((s, i) => (
          <li key={i} onClick={() => s.eventId && events.some((e) => e.id === s.eventId) && useStore.getState().selectEvent(s.eventId)}>
            <span className="mono">{fmtClock(s.timestamp)}</span>
            <b>{s.source}</b>
            <span className="mono dim">{s.claim ?? 'seed'} · {Math.round(s.confidence * 100)}%</span>
          </li>
        ))}
      </ul>
      {edges.length > 0 && (
        <>
          <div className="pv-sec">RELATIONSHIPS</div>
          <ul className="writes">
            {edges.map((e) => (
              <li key={e.id} onClick={() => useStore.getState().selectNode(e.from === n.id ? e.to : e.from)}>
                <span className="mono">({prettyId(e.from)})</span>
                <b>-[:{e.type}]→</b>
                <span className="mono">({prettyId(e.to)})</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  )
}

export function Provenance() {
  const ev = useStore((s) => s.events.find((e) => e.id === s.selectedEventId))
  const node = useStore((s) => (s.selectedNodeId ? s.nodes[s.selectedNodeId] : undefined))
  const open = Boolean(ev || node)
  const close = () => {
    useStore.getState().selectEvent(null)
    useStore.getState().selectNode(null)
  }
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          key={ev?.id ?? node?.id}
          className="provenance"
          initial={{ opacity: 0, x: 60, rotateY: -18 }}
          animate={{ opacity: 1, x: 0, rotateY: 0 }}
          exit={{ opacity: 0, x: 60, rotateY: -18 }}
          transition={{ type: 'spring', stiffness: 260, damping: 28 }}
        >
          <i className="br tl" /><i className="br tr" /><i className="br bl" /><i className="br bottom-r" />
          <header>
            <span className="p-code">PV</span>
            <h3>{ev ? 'PROVENANCE · EVENT' : 'GRAPH NODE'}</h3>
            <button className="x" onClick={close}>✕</button>
          </header>
          <div className="pv-body">{ev ? <EventView e={ev} /> : node ? <NodeView n={node} /> : null}</div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
