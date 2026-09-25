import { useEffect, useState } from 'react'
import { useStore } from '../store'
import { fmtClock, fmtMission, isoAt } from '../data/clock'
import { engine } from '../data/engine'
import { Scramble } from './Panel'

export function TopBar() {
  const t = useStore((s) => Math.floor(s.t))
  const net = useStore((s) => s.network)
  const danger = useStore((s) => Object.values(s.nodes).filter((n) => n.status === 'danger').length)
  const mode = useStore((s) => s.mode)
  const isLive = mode === 'live'
  const [wall, setWall] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setWall(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  const wan = net.state === 'normal' ? 'WAN NOMINAL' : net.state === 'throttled' ? `WAN THROTTLED ${net.kbps ?? ''}kbps` : 'WAN DOWN'
  return (
    <div className="topbar">
      <div className="brand">
        <svg viewBox="0 0 40 40" className="logo">
          <polygon points="20,2 37,11 37,29 20,38 3,29 3,11" />
          <polygon points="20,10 29,15 29,25 20,30 11,25 11,15" />
          <circle cx="20" cy="20" r="3" />
        </svg>
        <div>
          <Scramble text="RESCUEGRID" className="brand-name" />
          <div className="brand-sub">COUNTY EOC · WATCH OFFICER CONSOLE</div>
        </div>
      </div>

      <div className="incident">
        <span className="inc-dot" />
        <div>
          <div className="inc-title">INCIDENT EQ-1 · M5.8 SEISMIC · DOWNTOWN SECTOR 3</div>
          <div className="inc-sub">
            {danger} ACTIVE DANGER · IC @ STAGING AREA A · <b>{isLive ? 'REPLAYED INPUTS · LIVE INFERENCE + FUSION + GRAPH ON ZGX NANO' : 'REPLAYED INPUTS · LIVE LOCAL INFERENCE'}</b>
          </div>
        </div>
      </div>

      <div className="clocks">
        <div className="clk">
          <label>MISSION</label>
          <span className="mono big">{fmtMission(t)}</span>
        </div>
        <div className="clk">
          <label>SCENARIO</label>
          <span className="mono">{fmtClock(isoAt(Math.max(0, t)))}</span>
        </div>
        <div className="clk">
          <label>LOCAL</label>
          <span className="mono">{wall.toLocaleTimeString('en-US', { hour12: false })}</span>
        </div>
      </div>

      <div className="links">
        <button className="streams-btn" onClick={() => useStore.setState({ starter: true })} title="Choose the stream: upload a video, run the scenario, reset">STREAMS</button>
        <div className="seg mode-seg" title="LIVE: the Nano backend (vision, ASR, fusion, Neo4j) over the gateway. SIMULATION: the in-browser replay of the same scenario, no backend.">
          <button className={isLive ? 'on' : ''} onClick={() => engine.setMode('live')}>LIVE</button>
          <button className={!isLive ? 'on' : ''} onClick={() => engine.setMode('replay')}>SIMULATION</button>
        </div>
        <div className="link ok">
          <span className="led" />
          ZGX NANO · ON-DEVICE
        </div>
        <div className={`link ${net.state === 'normal' ? 'ok' : net.state === 'throttled' ? 'warn' : 'bad'}`}>
          <span className="led" />
          {wan}
        </div>
      </div>
    </div>
  )
}
