import { useEffect, useRef } from 'react'
import { Scene } from './scene/Scene'
import { engine } from './data/engine'
import { useStore } from './store'
import { REVEAL_ALL, TWIN } from './config'
import { TopBar } from './hud/TopBar'
import { LeftRail } from './hud/LeftRail'
import { RightRail } from './hud/RightRail'
import { Bottom } from './hud/Bottom'
import { Alerts, Boot, Flash, HoverTip, Provenance } from './hud/Overlays'
import { Starter } from './hud/Starter'
import './hud/hud.css'

// Dev-only handle for debugging / scripted demo checks: window.__rg.engine.seek(30)
if (import.meta.env.DEV) (window as unknown as { __rg: object }).__rg = { useStore, engine }

export default function App() {
  const hud = useRef<HTMLDivElement>(null)

  useEffect(() => engine.start(), [])

  // Cockpit parallax: the HUD tilts slightly with the pointer.
  useEffect(() => {
    let raf = 0
    const target = { x: 0, y: 0 }
    const cur = { x: 0, y: 0 }
    const move = (e: PointerEvent) => {
      target.x = e.clientX / window.innerWidth - 0.5
      target.y = e.clientY / window.innerHeight - 0.5
    }
    const loop = () => {
      cur.x += (target.x - cur.x) * 0.06
      cur.y += (target.y - cur.y) * 0.06
      hud.current?.style.setProperty('--px', cur.x.toFixed(4))
      hud.current?.style.setProperty('--py', cur.y.toFixed(4))
      raf = requestAnimationFrame(loop)
    }
    window.addEventListener('pointermove', move)
    raf = requestAnimationFrame(loop)
    return () => { window.removeEventListener('pointermove', move); cancelAnimationFrame(raf) }
  }, [])

  // HUD shake on dramatic events.
  useEffect(
    () =>
      useStore.subscribe((s, p) => {
        if (s.shock.nonce === p.shock.nonce || !hud.current) return
        const el = hud.current
        el.classList.remove('shake')
        void el.offsetWidth
        el.classList.add('shake')
      }),
    [],
  )

  return (
    <div className="app">
      <Scene />
      <div className={`hud${TWIN === 'mesh' ? ' twin-mesh' : ''}${REVEAL_ALL ? ' reveal-all' : ''}`} ref={hud}>
        <div className="scanlines" />
        <div className="frame-corners"><i /><i /><i /><i /></div>
        <TopBar />
        <LeftRail />
        <RightRail />
        <Bottom />
        <Alerts />
        <Provenance />
      </div>
      <Flash />
      <HoverTip />
      <Starter />
      <Boot />
    </div>
  )
}
