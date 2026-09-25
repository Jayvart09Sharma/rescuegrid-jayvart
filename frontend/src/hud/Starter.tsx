// Start screen (live mode): STAGE the incident, then press START. Nothing runs until the button.
//  01 drones: tick the drones that fly and give each its video (played into the Nano's vision service as that camera)
//  02 radio: tick built-in transmissions and/or add recordings; they play in order with a gap, each through ASR -> LLM -> graph
//  START INCIDENT: uploads everything, closes this screen, and the Nano runs it. Alternatives: rehearsal scenario,
//  in-browser simulation, reset.
import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { ENV } from '../config'
import { useStore } from '../store'
import { engine } from '../data/engine'
import { fmtClock } from '../data/clock'

interface EntityOpt { id: string; name: string }
interface RadioLine { id: string; speaker: string; channel: string; text: string; seconds: number }
interface Row { on: boolean; file: File | null; building: string; road: string; bridge: string; plan: string }
interface RadioPick { key: string; library_id?: string; file?: File; speaker: string; label: string }

export function Starter() {
  const open = useStore((s) => s.starter)
  const mode = useStore((s) => s.mode)
  const cams = useStore((s) => s.cameraMap)
  const streams = useStore((s) => s.streams)
  const reactive = useStore((s) => s.reactive)
  const queue = useStore((s) => s.radioQueue)
  const close = () => useStore.setState({ starter: false })

  const drones = Object.values(cams).filter((c) => c.type === 'drone').sort((a, b) => a.camera.localeCompare(b.camera))
  const [rows, setRows] = useState<Record<string, Row>>({})
  const [fps, setFps] = useState(2)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [entities, setEntities] = useState<Record<string, EntityOpt[]>>({})
  const [radioLib, setRadioLib] = useState<RadioLine[]>([])
  const [picks, setPicks] = useState<RadioPick[]>([])
  const [radioSpeaker, setRadioSpeaker] = useState('Field unit')
  const [gap, setGap] = useState(20)
  const [firstDelay, setFirstDelay] = useState(8)

  useEffect(() => {
    setRows((prev) => {
      const next = { ...prev }
      for (const c of drones) if (!next[c.camera]) next[c.camera] = { on: false, file: null, building: c.entities?.building ?? '', road: c.entities?.road ?? '', bridge: c.entities?.bridge ?? '', plan: [c.entities?.building, c.entities?.road, c.entities?.bridge].filter(Boolean).join(', ') }
      return next
    })
  }, [cams]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!open || mode !== 'live' || !ENV.restUrl) return
    fetch(`${ENV.restUrl}/entities`).then((r) => r.json()).then(setEntities).catch(() => undefined)
    fetch(`${ENV.restUrl}/radio/library`).then((r) => r.json()).then((j) => setRadioLib(j.lines ?? [])).catch(() => undefined)
  }, [open, mode])

  const setRow = (cam: string, patch: Partial<Row>) => setRows((r) => ({ ...r, [cam]: { ...r[cam], ...patch } }))
  const post = async (path: string, init?: RequestInit) => {
    const r = await fetch(`${ENV.restUrl}${path}`, init)
    const j = await r.json().catch(() => ({}))
    if (!r.ok) throw new Error((j as { detail?: string; error?: string }).detail ?? (j as { error?: string }).error ?? `HTTP ${r.status}`)
    return j
  }

  const toggleLine = (l: RadioLine) =>
    setPicks((p) => (p.some((x) => x.library_id === l.id) ? p.filter((x) => x.library_id !== l.id) : [...p, { key: l.id, library_id: l.id, speaker: l.speaker, label: `${l.speaker}: ${l.text.slice(0, 60)}…` }]))
  const addRecording = (f: File | null) => {
    if (!f) return
    setPicks((p) => [...p, { key: `${f.name}-${Date.now()}`, file: f, speaker: radioSpeaker, label: `${radioSpeaker}: ${f.name}` }])
  }
  const move = (i: number, d: number) => setPicks((p) => { const n = [...p]; const j = i + d; if (j < 0 || j >= n.length) return p; [n[i], n[j]] = [n[j], n[i]]; return n })

  const selected = drones.filter((c) => rows[c.camera]?.on)
  const live = mode === 'live'
  const ready = live && (selected.some((c) => rows[c.camera]?.file) || picks.length > 0)

  const startIncident = async () => {
    const missing = selected.filter((c) => !rows[c.camera]?.file)
    if (missing.length) return setMsg(`Choose a video for ${missing.map((c) => c.callsign).join(', ')} or untick it.`)
    if (!ready) return setMsg('Stage at least one drone video or one radio transmission.')
    setBusy('start')
    try {
      // 0. the earthquake: the seismic sensor starts measuring now, the quake fires 5 s later
      await post('/incident/start', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ magnitude: 5.8 }) })
      // 1. drone streams
      for (const c of selected) {
        const row = rows[c.camera]
        const fd = new FormData()
        fd.append('file', row.file!); fd.append('camera', c.camera); fd.append('building', row.building); fd.append('road', row.road); fd.append('bridge', row.bridge); fd.append('plan', row.plan)
        fd.append('fps', String(fps)); fd.append('speed', '1'); fd.append('loop', 'true')
        setMsg(`Uploading ${row.file!.name} for ${c.callsign}…`)
        await post('/streams', { method: 'POST', body: fd })
      }
      // 2. radio: hold recordings, then queue everything in order with the gap
      const items: { library_id?: string; file?: string; speaker?: string; delay_s: number }[] = []
      for (let i = 0; i < picks.length; i++) {
        const p = picks[i]
        const delay_s = i === 0 ? firstDelay : gap
        if (p.library_id) items.push({ library_id: p.library_id, delay_s })
        else if (p.file) {
          setMsg(`Uploading recording ${p.file.name}…`)
          const fd = new FormData(); fd.append('file', p.file)
          const h = (await post('/radio/hold', { method: 'POST', body: fd })) as { file: string }
          items.push({ file: h.file, speaker: p.speaker, delay_s })
        }
      }
      if (items.length) await post('/radio/queue', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ items }) })
      setMsg(null)
      setPicks([])
      close()
    } catch (e) {
      setMsg(`Start failed: ${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }

  const scenario = async () => {
    setBusy('scenario'); setMsg(null)
    try { await post('/scenario/start', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ speed: 1 }) }); close() }
    catch (e) { setMsg(`Could not start the scenario: ${(e as Error).message}`) } finally { setBusy(null) }
  }
  const reset = async () => {
    if (!window.confirm('Reset the incident? The shared graph goes back to the static seed, running streams stop, the radio queue is cleared.')) return
    setBusy('reset'); setMsg(null)
    try { await post('/reset', { method: 'POST' }); setMsg('Incident reset. The graph holds only the static world now.') }
    catch (e) { setMsg(`Reset failed: ${(e as Error).message}`) } finally { setBusy(null) }
  }
  const stop = async (id: string) => { try { await post(`/streams/${id}`, { method: 'DELETE' }) } catch (e) { setMsg((e as Error).message) } }
  const running = streams.filter((s) => s.state === 'running')
  const pendingQueue = queue.filter((q) => q.state === 'queued' || q.state === 'waiting' || q.state === 'transmitting')

  return (
    <AnimatePresence>
      {open && (
        <motion.div className="starter" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, filter: 'blur(6px)' }}>
          <div className="starter-card">
            <header>
              <div>
                <div className="boot-title">RESCUEGRID</div>
                <div className="brand-sub">COUNTY EOC · WATCH OFFICER CONSOLE · STAGE THE INCIDENT, THEN START</div>
              </div>
              <button className="x" onClick={close} title="Enter the console">✕</button>
            </header>

            <div className="starter-grid">
              <section>
                <h4>01 · DRONES · ONE VIDEO EACH</h4>
                <p className="dim small">Tick the drones that fly and give each its footage. Only ticked drones exist in the twin. Each file is played into the ZGX Nano's vision service as that camera at {fps} fps; the drone moves with its footage; only what the detector confirms reaches the graph.</p>
                {!live && <p className="warn">Switch to LIVE to stream to the backend (the gateway is not connected).</p>}
                {drones.map((c) => {
                  const r = rows[c.camera] ?? { on: false, file: null, building: '', road: '', bridge: '', plan: '' }
                  return (
                    <div key={c.camera} className={`drone-row ${r.on ? 'on' : ''}`}>
                      <label className="check big"><input type="checkbox" checked={r.on} onChange={(e) => setRow(c.camera, { on: e.target.checked })} /> {c.callsign.toUpperCase()}</label>
                      <label className="file">
                        <input type="file" accept="video/*" onChange={(e) => setRow(c.camera, { file: e.target.files?.[0] ?? null, on: true })} />
                        <span>{r.file ? `${r.file.name} · ${(r.file.size / 1e6).toFixed(1)} MB` : 'Choose video…'}</span>
                      </label>
                      <label className="plan">FLIGHT PLAN · places it flies over, in order
                        <input value={r.plan} onChange={(e) => setRow(c.camera, { plan: e.target.value })} placeholder="Building-14, Main Street, Bridge Street, Building 7" />
                      </label>
                    </div>
                  )
                })}
                <datalist id="rg-buildings">{(entities.building ?? []).map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</datalist>
                <datalist id="rg-roads">{(entities.road ?? []).map((r) => <option key={r.id} value={r.name}>{r.id}</option>)}</datalist>
                <div className="row">
                  <label>FPS TO DETECTOR<input type="number" min={0.5} max={10} step={0.5} value={fps} onChange={(e) => setFps(Number(e.target.value))} /></label>
                  <p className="dim small">The drone flies the plan across the clip: it hovers at each place, moves between them at 12 m/s, and every frame is tagged with the building, road and bridge nearest to it at that moment. Whatever the detector confirms lands on those. Order the places like the scenes in the footage. Videos loop until stopped.</p>
                </div>

                <h4 style={{ marginTop: 14 }}>02 · RADIO CH3 · IN ORDER</h4>
                <p className="dim small">Tick transmissions (built-in, spoken by a local TTS and shaped like a handheld radio) or add your own recordings. After START they play one after another; each goes through faster-whisper and the LLM before it lands in the graph.</p>
                <div className="radio-lib">
                  {radioLib.map((l) => {
                    const on = picks.some((x) => x.library_id === l.id)
                    return (
                      <button key={l.id} className={on ? 'on' : ''} onClick={() => toggleLine(l)} title={l.text}>
                        <b>{on ? '☑' : '☐'} {l.speaker}</b> <span>{l.text.slice(0, 58)}…</span> <em>{l.seconds}s</em>
                      </button>
                    )
                  })}
                </div>
                <div className="row">
                  <label className="file" style={{ flex: 2 }}>
                    <input type="file" accept="audio/*" onChange={(e) => { addRecording(e.target.files?.[0] ?? null); e.target.value = '' }} />
                    <span>+ Add recording (wav / mp3 / m4a)…</span>
                  </label>
                  <label>SPEAKER<input value={radioSpeaker} onChange={(e) => setRadioSpeaker(e.target.value)} placeholder="Engine 7" /></label>
                </div>
                {picks.length > 0 && (
                  <ol className="radio-order">
                    {picks.map((p, i) => (
                      <li key={p.key}>
                        <span className="mono">{i === 0 ? `+${firstDelay}s` : `+${gap}s`}</span>
                        <span>{p.label}</span>
                        <button onClick={() => move(i, -1)}>▲</button><button onClick={() => move(i, 1)}>▼</button>
                        <button onClick={() => setPicks((q) => q.filter((x) => x.key !== p.key))}>✕</button>
                      </li>
                    ))}
                  </ol>
                )}
                <div className="row">
                  <label>FIRST AFTER (s)<input type="number" min={0} max={300} value={firstDelay} onChange={(e) => setFirstDelay(Number(e.target.value))} /></label>
                  <label>GAP BETWEEN (s)<input type="number" min={0} max={300} value={gap} onChange={(e) => setGap(Number(e.target.value))} /></label>
                </div>
              </section>

              <section>
                <h4>START</h4>
                <button className="primary big-start" disabled={!ready || busy !== null} onClick={startIncident}>
                  {busy === 'start' ? 'STARTING…' : `▶ START INCIDENT · ${selected.length} DRONE${selected.length === 1 ? '' : 'S'} · ${picks.length} RADIO`}
                </button>
                <p className="dim small">Starts the seismic sensor (quake fires after 5 s of readings), uploads the videos, queues the radio, closes this screen. The Nano runs it from there.</p>

                <h4 style={{ marginTop: 14 }}>REACTIVE INCIDENT</h4>
                <button className={reactive ? 'primary' : ''} disabled={!live} onClick={() => { void post('/reactive', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ enabled: !reactive }) }); useStore.setState({ reactive: !reactive }) }}>
                  {reactive ? '● SIMULATED FEEDS REACT TO DETECTIONS · ON' : '○ SIMULATED FEEDS REACT TO DETECTIONS · OFF'}
                </button>
                <p className="dim small">Collapse seen: dispatch calls it, the nearest rescue unit rolls (GPS), the gas sensor by that building spikes 25 s later, crews report by voice. Road blocked: dispatch warns. Gas: withdraw suggestion. Simulated inputs are marked; vision, ASR, fusion and the graph are real.</p>

                <h4 style={{ marginTop: 14 }}>OR</h4>
                <button disabled={!live || busy !== null} onClick={scenario}>{busy === 'scenario' ? 'STARTING…' : '▶ RUN REHEARSAL SCENARIO (LIVE BACKEND)'}</button>
                <button onClick={() => { engine.setMode('replay'); close() }}>▷ SIMULATION (IN-BROWSER, NO BACKEND)</button>
                <button className="danger" disabled={!live || busy !== null} onClick={reset}>{busy === 'reset' ? 'RESETTING…' : '⟲ RESET INCIDENT'}</button>
                <button className="ghost" onClick={close}>ENTER CONSOLE →</button>
              </section>
            </div>

            {msg && <div className="starter-msg">{msg}</div>}

            {(streams.length > 0 || queue.length > 0) && (
              <div className="streams">
                <h4>RUNNING · {running.length} STREAM{running.length === 1 ? '' : 'S'} · {pendingQueue.length} RADIO PENDING</h4>
                <ul>
                  {streams.slice(-4).reverse().map((s) => (
                    <li key={s.id} className={s.state}>
                      <span className="mono">{fmtClock(s.started)}</span>
                      <b>{s.kind === 'scenario' ? 'SCENARIO' : s.camera?.toUpperCase()}</b>
                      <span>{s.file}</span>
                      <span className="mono">{s.state}{s.frames_sent ? ` · ${s.frames_sent} frames` : ''}</span>
                      {s.state === 'running' && <button onClick={() => stop(s.id)}>STOP</button>}
                    </li>
                  ))}
                  {queue.slice(-6).map((q) => (
                    <li key={q.id} className={q.state === 'transmitting' ? 'running' : ''}>
                      <span className="mono">+{q.delay_s ?? 0}s</span>
                      <b>RADIO</b>
                      <span>{q.library_id ?? q.file}{q.result?.claim?.entity ? ` → ${q.result.claim.entity} ${q.result.claim.claim} (${q.result.bus})` : ''}</span>
                      <span className="mono">{q.state}</span>
                      <span />
                    </li>
                  ))}
                </ul>
                {pendingQueue.length > 0 && <button className="ghost" onClick={() => void post('/radio/queue', { method: 'DELETE' })}>CANCEL PENDING RADIO</button>}
              </div>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
