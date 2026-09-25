import { useEffect, useState, type ReactNode } from 'react'
import { motion } from 'framer-motion'

export function Panel({
  title, code, children, className = '', delay = 0, right,
}: { title: string; code?: string; children: ReactNode; className?: string; delay?: number; right?: ReactNode }) {
  return (
    <motion.section
      className={`panel ${className}`}
      initial={{ opacity: 0, scaleY: 0.02, filter: 'brightness(4)' }}
      animate={{ opacity: 1, scaleY: 1, filter: 'brightness(1)' }}
      transition={{ delay, duration: 0.55, ease: [0.2, 0.9, 0.2, 1] }}
    >
      <i className="br tl" /><i className="br tr" /><i className="br bl" /><i className="br bottom-r" />
      <header>
        <span className="p-code">{code}</span>
        <h3>{title}</h3>
        <span className="p-right">{right}</span>
        <span className="p-scan" />
      </header>
      <div className="p-body">{children}</div>
    </motion.section>
  )
}

/** Types text out character by character; restarts when `text` changes. */
export function Typewriter({ text, speed = 14, className }: { text: string; speed?: number; className?: string }) {
  const [n, setN] = useState(0)
  useEffect(() => {
    setN(0)
    const id = window.setInterval(() => setN((v) => (v >= text.length ? (clearInterval(id), v) : v + 2)), speed)
    return () => clearInterval(id)
  }, [text, speed])
  return (
    <span className={className}>
      {text.slice(0, n)}
      {n < text.length && <span className="caret">▌</span>}
    </span>
  )
}

/** Scrambles into the target string, HUD-style. */
export function Scramble({ text, className }: { text: string; className?: string }) {
  const [out, setOut] = useState(text)
  useEffect(() => {
    const glyphs = '█▓▒░<>/\\|#%&*0123456789ABCDEF'
    let frame = 0
    const id = window.setInterval(() => {
      frame++
      const done = Math.floor(frame / 1.5)
      setOut(text.split('').map((c, i) => (i < done || c === ' ' ? c : glyphs[Math.floor(Math.random() * glyphs.length)])).join(''))
      if (done >= text.length) clearInterval(id)
    }, 28)
    return () => clearInterval(id)
  }, [text])
  return <span className={className}>{out}</span>
}
