import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useStore } from '../store'
import { engine } from '../data/engine'
import { fmtClock, fmtMission } from '../data/clock'
import { BEATS, SCENARIO_LENGTH } from '../data/scenario'
import { Panel } from './Panel'

const SRC_ABBR: Record<string, string> = {
  drone_vision: 'VIS', radio_asr: 'ASR', gps: 'GPS', sensor: 'SNS', field_report: 'RPT', fusion: 'FUS',
}

function Timeline() {
  const events = useStore((s) => s.events)
  const selected = useStore((s) => s.selectedEventId)
  const list = [...events].reverse()
  return (
    <Panel title="EVENT TIMELINE" code="07" delay={0.6} className="timeline" right={<span className="mono dim">{events.length} EVENTS</span>}>
      <ul>
        <AnimatePresence initial={false}>
          {list.map((e) => (
            <motion.li
              key={e.id}
              layout
              initial={{ opacity: 0, x: -30, backgroundColor: 'rgba(255,255,255,0.25)' }}
              animate={{ opacity: 1, x: 0, backgroundColor: 'rgba(255,255,255,0)' }}
              transition={{ duration: 0.5 }}
              className={`ev s-${e.severity} ${selected === e.id ? 'sel' : ''}`}
              onClick={() => useStore.getState().selectEvent(e.id)}
            >
              <span className="ev-bar" />
              <span className="mono ev-time">{fmtClock(e.timestamp)}</span>
              <span className="ev-src">{SRC_ABBR[e.source]}</span>
              <span className="ev-title">{e.title}</span>
              <span className="mono ev-conf">{e.source} {e.confidence.toFixed(2)}</span>
            </motion.li>
          ))}
        </AnimatePresence>
        {!events.length && <li className="ev empty">Awaiting first event…</li>}
      </ul>
    </Panel>
  )
}

// Autopilot: plays the scenario and asks the scripted questions on cue.
// It never approves suggestions; that stays a human click (Rule 3).
const CUES = [
  { t: 44, q: 'Can Ambulance 2 still reach the hospital?' },
  { t: 97, q: "Who's in the most danger?" },
  { t: 108, q: 'Any conflicting reports?' },
]

function SimDirector() {
  const t = useStore((s) => s.t)
  const playing = useStore((s) => s.playing)
  const speed = useStore((s) => s.speed)
  const isLive = useStore((s) => s.mode === 'live')
  const [auto, setAuto] = useState(false)
  const fired = useRef(new Set<number>())
  const bar = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!auto) return
    for (const c of CUES) {
      if (t >= c.t && t < c.t + 1 && !fired.current.has(c.t)) {
        fired.current.add(c.t)
        void engine.ask(c.q)
      }
    }
  }, [t, auto])

  const seek = (to: number, play = true) => {
    fired.current = new Set(CUES.filter((c) => c.t < to).map((c) => c.t))
    engine.seek(to)
    useStore.getState().setPlaying(play)
  }

  const scrub = (clientX: number) => {
    const r = bar.current!.getBoundingClientRect()
    seek(((clientX - r.left) / r.width) * SCENARIO_LENGTH, playing)
  }

  if (isLive) return null
  return (
    <Panel title="SIM DIRECTOR" code="08" delay={0.7} className="director" right={<span className="badge-replay">SIMULATION</span>}>
      <div className="transport">
        <button onClick={() => seek(-2)} title="Restart">⟲</button>
        <button className="play" onClick={() => useStore.getState().setPlaying(!playing)}>{playing ? '❚❚' : '▶'}</button>
        <span className="mono t-now">{fmtMission(t)}</span>
        <div className="speeds">
          {[0.5, 1, 2, 4, 8].map((s) => (
            <button key={s} className={speed === s ? 'on' : ''} onClick={() => useStore.getState().setSpeed(s)}>{s}×</button>
          ))}
        </div>
        <button className={`auto ${auto ? 'on' : ''}`} onClick={() => { setAuto(!auto); if (!auto) seek(-2) }}>
          {auto ? '● AUTOPILOT' : '○ AUTOPILOT'}
        </button>
      </div>
      <div
        className="scrub"
        ref={bar}
        onPointerDown={(e) => {
          scrub(e.clientX)
          const move = (ev: PointerEvent) => scrub(ev.clientX)
          const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
          window.addEventListener('pointermove', move)
          window.addEventListener('pointerup', up)
        }}
      >
        <div className="scrub-fill" style={{ width: `${(t / SCENARIO_LENGTH) * 100}%` }} />
        {BEATS.map((b) => (
          <span key={b.id} className={`tick ${t >= b.t ? 'past' : ''}`} style={{ left: `${(b.t / SCENARIO_LENGTH) * 100}%` }}>
            <em>{b.label}</em>
          </span>
        ))}
        <span className="head" style={{ left: `${(t / SCENARIO_LENGTH) * 100}%` }} />
      </div>
      <div className="beats">
        {BEATS.map((b) => (
          <button key={b.id} onClick={() => seek(b.t - 2.5)}>
            <i>▸</i>{b.label}
          </button>
        ))}
        <button
          className="flyto"
          onClick={() => {
            seek(96, true)
            setTimeout(() => void engine.ask("Who's in the most danger?"), 400)
          }}
        >
          <i>⌖</i>FLY-TO Q&A
        </button>
      </div>
    </Panel>
  )
}

export function Bottom() {
  return (
    <div className="bottom">
      <Timeline />
      <SimDirector />
    </div>
  )
}
