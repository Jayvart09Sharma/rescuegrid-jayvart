// Uncaught browser errors are posted to the gateway (POST /client-error) so a crash on a laptop can be read on the Nano.
import { ENV } from './config'
function report(kind: string, message: string, stack?: string) {
  try {
    if (!ENV.restUrl) return
    void fetch(`${ENV.restUrl}/client-error`, { method: 'POST', headers: { 'content-type': 'application/json' }, keepalive: true,
      body: JSON.stringify({ kind, message, stack: stack ?? '', url: location.href, ua: navigator.userAgent }) })
  } catch { /* never let reporting throw */ }
}
window.addEventListener('error', (e) => report('error', String(e.message), e.error?.stack))
window.addEventListener('unhandledrejection', (e) => report('unhandledrejection', String((e.reason as Error)?.message ?? e.reason), (e.reason as Error)?.stack))
import { createRoot } from 'react-dom/client'
import { StrictMode } from 'react'
import '@fontsource/orbitron/500.css'
import '@fontsource/orbitron/700.css'
import '@fontsource/orbitron/900.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/600.css'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
