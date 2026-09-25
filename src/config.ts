import type { Status } from './types'

const params = typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : new URLSearchParams()
/** Which twin to draw: the reconstructed mesh city (default) or the original point cloud (?twin=particles). */
export const TWIN: 'mesh' | 'particles' = params.get('twin') === 'particles' ? 'particles' : 'mesh'
/** ?reveal=all: show the mesh city (and the HUD fill) fully built at once, no load transition. */
export const REVEAL_ALL = params.get('reveal') === 'all'

/** Semantic colors fixed by the brief. Never reuse these for chrome. */
export const STATUS_HEX: Record<Status, string> = {
  normal: '#22e67a',
  warning: '#ffcf3a',
  danger: '#ff2d4b',
  conflict: '#ffcf3a',
  safe: '#2f8cff',
}

export const STATUS_LABEL: Record<Status, string> = {
  normal: 'NORMAL',
  warning: 'WARNING',
  danger: 'DANGER',
  conflict: 'CONFLICTING REPORTS',
  safe: 'RESPONDER / SAFE',
}

/**
 * Live-mode endpoints. Leave unset to run the in-browser replay.
 *  VITE_RG_LIVE_URL    – Shresth's graph/event gateway (ws:// for the stream, http(s):// base for REST)
 *  VITE_RG_QA_URL      – Kenil's Q&A endpoint (POST {question} → FlyToSignal)
 *  VITE_RG_NETSIM_URL  – Jayvant's network-condition simulator (GET/POST /status)
 */
// Relative values (e.g. `/stream`, `/qa`, `/netsim`) resolve against the page's own origin, so one build works
// wherever Shresth's gateway (bus/gateway.py) is reached from: the Nano itself, or a laptop over an SSH tunnel.
const here = typeof window !== 'undefined' ? window.location : undefined
function resolve(u: string | undefined, ws = false): string | undefined {
  if (!u || !here || /^(wss?|https?):\/\//.test(u)) return u || undefined
  const proto = ws ? (here.protocol === 'https:' ? 'wss:' : 'ws:') : here.protocol
  return `${proto}//${here.host}${u.startsWith('/') ? '' : '/'}${u}`
}
const liveUrl = resolve(import.meta.env.VITE_RG_LIVE_URL as string | undefined, true)
export const ENV = {
  liveUrl,
  /** http(s) base of the gateway (REST: /suggestions/:id, /evidence/*), derived from the stream URL. */
  restUrl: liveUrl?.replace(/^ws/, 'http').replace(/\/stream\/?$/, ''),
  qaUrl: resolve(import.meta.env.VITE_RG_QA_URL as string | undefined),
  netsimUrl: resolve(import.meta.env.VITE_RG_NETSIM_URL as string | undefined),
}
