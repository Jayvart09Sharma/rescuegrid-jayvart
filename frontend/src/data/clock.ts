// Scenario clock. t=0 is the seismic event; every replayed feed reads off this.
// Live mode: VITE_RG_SCENARIO_START = scenario.json's scenario_start (2026-09-25T14:00:00Z), so clocks match the graph.
export const BASE_MS = Date.parse((import.meta.env.VITE_RG_SCENARIO_START as string | undefined) ?? '2026-09-25T14:31:02-07:00')
export const BASE_ISO = new Date(BASE_MS).toISOString()

export const isoAt = (t: number) => new Date(BASE_MS + t * 1000).toISOString()

const clockFmt = new Intl.DateTimeFormat('en-US', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
  timeZone: 'America/Los_Angeles',
})

/** "14:31:17" in the EOC's local time. */
export const fmtClock = (iso: string) => clockFmt.format(new Date(iso))

/** "T+01:32" */
export function fmtMission(t: number) {
  const s = Math.max(0, Math.floor(t))
  return `T+${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}
