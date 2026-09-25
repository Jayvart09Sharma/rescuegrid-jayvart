// The one facade the UI talks to. Mode is chosen at RUNTIME (LIVE / SIMULATION toggle in the top bar):
//  - live:   the Nano backend through Shresth's gateway (WS /stream, /qa, /netsim, /evidence). Nothing scripted.
//            If the gateway is not reachable the twin waits and keeps reconnecting; nothing runs.
//  - replay: the in-browser simulation of the same scenario (src/data/scenario.ts). ONLY behind the SIMULATION button;
//            it never starts on its own.
import { ENV } from '../config'
import { useStore } from '../store'
import type { NetworkState } from '../types'
import { answerLocally } from './qa'
import { live } from './liveSource'
import { replay } from './replaySource'
import { startCloudSim } from './cloudSim'
import { resetCoverage } from '../scene/coverage'

export type Mode = 'live' | 'replay'

const isLive = () => useStore.getState().mode === 'live'
let stopSource: (() => void) | null = null

async function gatewayUp(): Promise<boolean> {
  if (!ENV.liveUrl || !ENV.restUrl) return false
  try {
    const r = await fetch(`${ENV.restUrl}/health`, { signal: AbortSignal.timeout(2500) })
    return r.ok
  } catch {
    return false
  }
}

export const engine = {
  start() {
    const stopCloud = startCloudSim()
    this.setMode('live')
    return () => { stopSource?.(); stopSource = null; stopCloud() }
  },

  /** Switch source. Tears the current one down, clears the mirror, and starts the other. */
  setMode(mode: Mode) {
    if (stopSource && useStore.getState().mode === mode) return
    stopSource?.()
    stopSource = null
    const s = useStore.getState()
    s.resetGraph()
    s.setMode(mode)
    resetCoverage(0)
    if (mode === 'live') {
      // live source reconnects on its own; a gateway that is down means an empty console, never the simulation
      stopSource = live.start()
      useStore.setState({ starter: true })
      void gatewayUp().then((up) => {
        if (!up) useStore.setState({
          alerts: [...useStore.getState().alerts.slice(-2), { id: `al-gw-${Date.now()}`, title: 'Gateway not reachable', severity: 'warning', sub: `${ENV.restUrl ?? 'no VITE_RG_LIVE_URL'} · waiting for the Nano` }],
        })
      })
    } else {
      stopSource = replay.start()
    }
  },

  seek(t: number) {
    if (isLive()) return
    replay.seek(t)
    resetCoverage(t)
  },

  async ask(question: string) {
    const s = useStore.getState()
    s.setQA({ question, status: 'thinking', signal: undefined })
    // Let the "querying graph" animation read before the answer lands.
    await new Promise((r) => setTimeout(r, 650))
    let signal
    try {
      signal = isLive() && ENV.qaUrl ? await live.ask(question) : answerLocally(question)
    } catch {
      signal = answerLocally(question)
    }
    useStore.getState().setQA({ status: 'done', signal })
    useStore.getState().flyToSignal(signal)
  },

  setNetwork(state: NetworkState, kbps: number | null) {
    useStore.getState().setNetwork({ state, kbps })
    if (isLive() && ENV.netsimUrl) void live.setNetwork(state, kbps)
  },

  decide(id: string, decision: 'approved' | 'dismissed') {
    useStore.getState().decideSuggestion(id, decision)
    if (isLive()) void live.decide(id, decision)
  },
}
