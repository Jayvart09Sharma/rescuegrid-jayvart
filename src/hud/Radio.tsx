// Radio channel panel (live mode): plays each transmission the gateway relays (real audio through the Nano's ASR),
// with a live spectrum that reacts to the voice, the transcript from faster-whisper, and the claim the LLM extracted.
import { useEffect, useRef, useState } from 'react'
import { ENV } from '../config'
import { useStore } from '../store'
import { fmtClock } from '../data/clock'
import { Panel } from './Panel'
import type { RadioMsg } from '../types'

const BARS = 48

/** Bars driven by an AnalyserNode: frequency bins of the playing audio, so pitch and loudness move the wave. */
export function useAnalyser(audio: HTMLAudioElement | null) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    if (!audio) return
    const AC = window.AudioContext
    if (!AC) return
    const ctx = new AC()
    const src = ctx.createMediaElementSource(audio)
    const an = ctx.createAnalyser()
    an.fftSize = 256
    an.smoothingTimeConstant = 0.75
    src.connect(an)
    an.connect(ctx.destination)
    const data = new Uint8Array(an.frequencyBinCount)
    let raf = 0
    const draw = () => {
      raf = requestAnimationFrame(draw)
      const c = ref.current
      if (!c) return
      const g = c.getContext('2d')!
      an.getByteFrequencyData(data)
      g.clearRect(0, 0, c.width, c.height)
      const w = c.width / BARS
      for (let i = 0; i < BARS; i++) {
        // voice band emphasised: bins 2..60 of 128 at 22 kHz ≈ 170 Hz..5 kHz
        const v = data[2 + Math.floor((i / BARS) * 58)] / 255
        const h = Math.max(2, v * c.height)
        g.fillStyle = `rgba(${120 + v * 135}, ${200 + v * 55}, 255, ${0.35 + v * 0.65})`
        g.fillRect(i * w + 1, (c.height - h) / 2, w - 2, h)
      }
    }
    draw()
    const resume = () => { if (ctx.state === 'suspended') void ctx.resume() }
    audio.addEventListener('play', resume)
    return () => { cancelAnimationFrame(raf); audio.removeEventListener('play', resume); void ctx.close() }
  }, [audio])
  return ref
}

function Player({ r, autoplay, onDone }: { r: RadioMsg; autoplay: boolean; onDone: () => void }) {
  const [el, setEl] = useState<HTMLAudioElement | null>(null)
  const canvas = useAnalyser(el)
  const [playing, setPlaying] = useState(false)
  useEffect(() => {
    if (!el) return
    if (autoplay) el.play().catch(() => undefined)
  }, [el, autoplay])
  return (
    <div className={`radio-player ${playing ? 'on' : ''}`}>
      <button onClick={() => { if (!el) return; if (el.paused) void el.play(); else el.pause() }}>{playing ? '◼' : '▶'}</button>
      <canvas ref={canvas} width={300} height={44} className="radio-wave" />
      <audio ref={setEl} src={`${ENV.restUrl}${r.url}`} preload="auto" crossOrigin="anonymous"
        onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => { setPlaying(false); onDone() }} />
    </div>
  )
}

export function RadioPanel() {
  const isLive = useStore((s) => s.mode === 'live')
  const radio = useStore((s) => s.radio)
  const playing = useStore((s) => s.radioPlaying)
  const [pick, setPick] = useState<string | null>(null)
  if (!isLive) return null
  const current = radio.find((r) => r.id === (pick ?? playing)) ?? radio[radio.length - 1]
  return (
    <Panel title="RADIO CH3 · LIVE" code="09" delay={0.3} right={<span className="chip-mini">ASR → LLM → GRAPH</span>}>
      {!current && <div className="dim small">No transmissions yet. STREAMS → RADIO to transmit a built-in line or upload a recording.</div>}
      {current && (
        <>
          <div className="radio-head">
            <b>{current.speaker.toUpperCase()}</b>
            <span className="mono dim">{current.channel.toUpperCase()} · {fmtClock(current.ts)}</span>
          </div>
          <Player key={current.id} r={current} autoplay={current.id === playing} onDone={() => useStore.setState({ radioPlaying: null })} />
          <div className="transcript"><label>ASR TRANSCRIPT · {current.asr_ms ? `${(current.asr_ms / 1000).toFixed(1)} s` : ''}</label>“{current.transcript || '…'}”</div>
          {current.claim && (
            <div className={`radio-claim ${current.bus?.applied ? 'applied' : ''}`}>
              <label>CLAIM · LLM {current.llm_ms ? `${(current.llm_ms / 1000).toFixed(1)} s` : ''}</label>
              {current.claim.entity ? (
                <span><b>{current.claim.entity}</b> → {current.claim.claim} · {Math.round(current.claim.confidence * 100)}%
                  {current.bus ? ` · ${current.bus.action}${current.bus.entity_id ? ` ${current.bus.entity_id}` : ''}` : ''}
                  {current.claim.people_trapped ? ` · ${current.claim.people_trapped} trapped` : ''}</span>
              ) : <span className="dim">no graph claim in this transmission</span>}
            </div>
          )}
          {current.error && <div className="warn small">{current.error}</div>}
        </>
      )}
      {radio.length > 1 && (
        <ul className="radio-log">
          {radio.slice(-5).reverse().map((r) => (
            <li key={r.id} className={r.id === current?.id ? 'sel' : ''} onClick={() => setPick(r.id)}>
              <span className="mono">{fmtClock(r.ts)}</span><b>{r.speaker}</b><span>{r.transcript.slice(0, 48)}…</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
