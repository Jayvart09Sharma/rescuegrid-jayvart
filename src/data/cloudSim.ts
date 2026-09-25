// Optional cloud context (satellite / weather / GIS). In live mode the status
// comes from the network-condition simulator's signal. In replay mode we emulate
// the same observable behaviour from NetworkStatus so the visuals can be tested.
import { useStore, type CloudService } from '../store'

const SERVICES: CloudService[] = ['satellite', 'weather', 'gis']
const TIMEOUT_MS = 4500

function simulatedLatency(): number | null {
  const { state, kbps } = useStore.getState().network
  if (state === 'disconnected') return null
  if (state === 'normal') return 90 + Math.random() * 140
  const cap = kbps ?? 256
  return (256 / cap) * (1600 + Math.random() * 3400)
}

function fetchOnce(svc: CloudService) {
  useStore.getState().setCloud(svc, { status: 'loading' })
  const lat = simulatedLatency()
  if (lat === null) {
    window.setTimeout(() => {
      const st = useStore.getState()
      st.setCloud(svc, { status: 'down', latencyMs: null })
      st.bumpCloudStat('failed')
      st.setNetwork({ latencyMs: null })
    }, 250)
    return
  }
  window.setTimeout(() => {
    const st = useStore.getState()
    if (lat >= TIMEOUT_MS) {
      st.setCloud(svc, { status: 'down', latencyMs: null })
      st.bumpCloudStat('failed')
    } else {
      const slow = lat > 900
      st.setCloud(svc, { status: slow ? 'slow' : 'ok', latencyMs: Math.round(lat), lastOkAt: Date.now() })
      st.bumpCloudStat(slow ? 'slow' : 'ok')
    }
    st.setNetwork({ latencyMs: lat >= TIMEOUT_MS ? null : Math.round(lat) })
  }, Math.min(lat, TIMEOUT_MS))
}

export function startCloudSim() {
  const timers = SERVICES.map((svc, i) => [
    window.setTimeout(() => fetchOnce(svc), 300 + i * 400),
    window.setInterval(() => fetchOnce(svc), 4200 + i * 700),
  ])
  return () => timers.forEach(([k, e]) => { clearTimeout(k); clearInterval(e) })
}
