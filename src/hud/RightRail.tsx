import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { prettyId, useStore, type CloudService } from '../store'
import { engine } from '../data/engine'
import { PRESETS } from '../data/qa'
import { fmtClock, isoAt } from '../data/clock'
import { Panel, Typewriter } from './Panel'
import type { NetworkState } from '../types'

function QA() {
  const qa = useStore((s) => s.qa)
  const [q, setQ] = useState('')
  const [showCypher, setShowCypher] = useState(false)
  const ask = (text: string) => {
    if (!text.trim()) return
    setQ('')
    void engine.ask(text)
  }
  return (
    <Panel title="COMMANDER Q&A" code="04" delay={0.25} right={<span className="chip-mini">LOCAL LLM → CYPHER</span>}>
      <form
        className="qa-input"
        onSubmit={(e) => {
          e.preventDefault()
          ask(q)
        }}
      >
        <span className="prompt">&gt;</span>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask the graph…" spellCheck={false} />
        <button type="submit">QUERY</button>
      </form>
      <div className="presets">
        {PRESETS.map((p) => (
          <button key={p} onClick={() => ask(p)}>{p}</button>
        ))}
      </div>
      <AnimatePresence mode="wait">
        {qa.status === 'thinking' && (
          <motion.div key="think" className="qa-think" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <div className="q">“{qa.question}”</div>
            <div className="think-bars">
              {Array.from({ length: 18 }).map((_, i) => <i key={i} style={{ animationDelay: `${i * 0.05}s` }} />)}
            </div>
            <span className="mono dim">TRANSLATING → CYPHER · QUERYING NEO4J</span>
          </motion.div>
        )}
        {qa.status === 'done' && qa.signal && (
          <motion.div key={qa.signal.answer} className="qa-answer" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
            <div className="q">“{qa.question}”</div>
            <Typewriter text={qa.signal.answer} className="a" />
            <div className="qa-meta">
              <span className="fly">⌖ FLY-TO {prettyId(qa.signal.target).toUpperCase()}</span>
              {qa.signal.cypher && (
                <button onClick={() => setShowCypher((v) => !v)}>{showCypher ? 'HIDE' : 'SHOW'} CYPHER</button>
              )}
            </div>
            {showCypher && qa.signal.cypher && <pre className="cypher">{qa.signal.cypher}</pre>}
          </motion.div>
        )}
      </AnimatePresence>
    </Panel>
  )
}

function Suggestions() {
  const suggestions = useStore((s) => s.suggestions)
  const approvals = useStore((s) => s.approvals)
  if (!suggestions.length) return null
  return (
    <Panel title="SUGGESTIONS" code="05" delay={0} className="suggest-panel">
      {suggestions.map((s) => (
        <motion.div
          key={s.id}
          className={`suggestion st-${s.state}`}
          initial={{ opacity: 0, x: 40 }}
          animate={{ opacity: 1, x: 0 }}
        >
          <div className="sg-head">
            <span className="sg-kind">{String(s.kind).toUpperCase()}</span>
            <b>{prettyId(s.unit).toUpperCase()}</b>
            <span className="sg-time mono">{fmtClock(s.createdAt)}</span>
          </div>
          <p>{s.reason}</p>
          {s.state === 'pending' ? (
            <>
              <div className="sg-warn">SUGGESTED · COMMANDER APPROVAL REQUIRED</div>
              <div className="sg-actions">
                <button className="approve" onClick={() => engine.decide(s.id, 'approved')}>APPROVE</button>
                <button className="dismiss" onClick={() => engine.decide(s.id, 'dismissed')}>DISMISS</button>
              </div>
            </>
          ) : (
            <div className={`sg-done ${s.state}`}>
              {s.state === 'approved' ? `✓ APPROVED BY IC · ${fmtClock(isoAt(approvals[s.id] ?? 0))}` : '✕ DISMISSED BY IC'}
            </div>
          )}
        </motion.div>
      ))}
    </Panel>
  )
}

const SERVICES: { key: CloudService; name: string; sub: string }[] = [
  { key: 'satellite', name: 'SATELLITE', sub: 'Sentinel pass · 10 m' },
  { key: 'weather', name: 'WEATHER', sub: 'NWS · wind 14 mph NW' },
  { key: 'gis', name: 'GIS', sub: 'county parcels · utilities' },
]

function TileArt({ kind, status }: { kind: CloudService; status: string }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current!
    const ctx = c.getContext('2d')!
    let raf = 0
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop)
      const t = now / 1000
      const w = c.width
      const h = c.height
      if (status === 'down') {
        const img = ctx.createImageData(w, h)
        for (let i = 0; i < img.data.length; i += 4) {
          const v = Math.random() * 70
          img.data[i] = img.data[i + 1] = img.data[i + 2] = v
          img.data[i + 3] = 255
        }
        ctx.putImageData(img, 0, 0)
        return
      }
      ctx.fillStyle = '#04121a'
      ctx.fillRect(0, 0, w, h)
      if (kind === 'satellite') {
        for (let i = 0; i < 40; i++) {
          ctx.fillStyle = `rgba(${60 + (i * 37) % 80},${90 + (i * 53) % 90},${100 + (i * 29) % 60},0.35)`
          ctx.fillRect((i * 23) % w, (i * 41) % h, 10 + (i % 5) * 4, 6 + (i % 3) * 5)
        }
        ctx.fillStyle = 'rgba(120,220,255,0.35)'
        ctx.fillRect(0, (t * 18) % h, w, 2)
      } else if (kind === 'weather') {
        ctx.strokeStyle = 'rgba(120,200,255,0.55)'
        for (let i = 0; i < 14; i++) {
          const y = (i * 9 + t * 12) % h
          ctx.beginPath()
          ctx.moveTo(((i * 31) % w) - 20, y)
          ctx.lineTo(((i * 31) % w) + 10, y + 6)
          ctx.stroke()
        }
      } else {
        ctx.strokeStyle = 'rgba(120,255,200,0.35)'
        for (let x = 0; x < w; x += 12) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x + 8, h); ctx.stroke() }
        for (let y = 0; y < h; y += 10) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y + 3); ctx.stroke() }
      }
      if (status === 'slow') {
        ctx.fillStyle = 'rgba(0,0,0,0.55)'
        ctx.fillRect(0, 0, w, h)
      }
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [kind, status])
  return <canvas ref={ref} width={110} height={52} />
}

function Network() {
  const net = useStore((s) => s.network)
  const cloud = useStore((s) => s.cloud)
  const [kbps, setKbps] = useState(256)
  const set = (state: NetworkState) => engine.setNetwork(state, state === 'throttled' ? kbps : state === 'normal' ? null : 0)
  return (
    <Panel
      title="CLOUD CONTEXT · OPTIONAL"
      code="06"
      delay={0.45}
      right={<span className={`net-pill ${net.state}`}>{net.latencyMs !== null ? `${net.latencyMs} ms` : 'NO LINK'}</span>}
    >
      <div className="seg">
        {(['normal', 'throttled', 'disconnected'] as NetworkState[]).map((s) => (
          <button key={s} className={`${net.state === s ? 'on' : ''} ${s}`} onClick={() => set(s)}>
            {s === 'normal' ? 'NORMAL' : s === 'throttled' ? 'THROTTLED' : 'ZERO WAN'}
          </button>
        ))}
      </div>
      {net.state === 'throttled' && (
        <div className="kbps">
          <input
            type="range" min={64} max={1024} step={32} value={kbps}
            onChange={(e) => {
              const v = Number(e.target.value)
              setKbps(v)
              engine.setNetwork('throttled', v)
            }}
          />
          <span className="mono">{kbps} kbps</span>
        </div>
      )}
      <div className="tiles">
        {SERVICES.map((s) => {
          const tile = cloud[s.key]
          return (
            <div key={s.key} className={`tile t-${tile.status}`}>
              <TileArt kind={s.key} status={tile.status} />
              <div className="tile-meta">
                <b>{s.name}</b>
                <span className="mono">
                  {tile.status === 'down' ? 'TIMEOUT' : tile.status === 'loading' ? '···' : `${tile.latencyMs} ms`}
                </span>
              </div>
              {tile.status === 'down' && <div className="tile-down">NO SIGNAL</div>}
              {tile.status === 'loading' && <div className="tile-spin" />}
            </div>
          )
        })}
      </div>
      <div className="local-path">
        <span>ON-DEVICE PATH</span>
        {['VISION', 'ASR', 'LLM', 'FUSION', 'NEO4J'].map((k) => (
          <em key={k}><i />{k}</em>
        ))}
      </div>
      <AnimatePresence>
        {net.state !== 'normal' && (
          <motion.div className={`wan-banner ${net.state}`} initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }}>
            {net.state === 'disconnected'
              ? 'WAN LOST · Cloud context dark · all inference + graph continue on ZGX Nano'
              : 'DEGRADED LINK · Cloud context lagging · Nano path unaffected'}
          </motion.div>
        )}
      </AnimatePresence>
    </Panel>
  )
}

export function RightRail() {
  return (
    <aside className="rail right">
      <QA />
      <Suggestions />
      <Network />
    </aside>
  )
}
