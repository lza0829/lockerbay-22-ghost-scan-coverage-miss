async function req(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export const api = {
  health: () => req('/api/health'),
  overview: () => req('/api/overview'),
  bay: () => req('/api/bay'),
  occupancies: () => req('/api/occupancies'),
  events: () => req('/api/events'),
  quarantine: () => req('/api/quarantine'),
  proofs: () => req('/api/occupancy-proofs'),
  ghost: () => req('/api/ghost'),
  ghostScan: () => req('/api/ghost/scan', { method: 'POST', body: '{}' }),
  ghostCoverage: (runId) =>
    req(runId ? `/api/ghost-scan/coverage?run_id=${encodeURIComponent(runId)}` : '/api/ghost-scan/coverage'),
  ghostScanRuns: () => req('/api/ghost-scan/runs'),
  ghostVerify: (runId) =>
    req(runId ? `/api/ghost-scan/verify?run_id=${encodeURIComponent(runId)}` : '/api/ghost-scan/verify'),
  ghostReconcile: () => req('/api/ghost-scan/reconcile'),
  settings: () => req('/api/settings'),
  setOverdue: (overdue_hours) =>
    req('/api/settings/overdue-hours', { method: 'PUT', body: JSON.stringify({ overdue_hours }) }),
  ingest: () => req('/api/ingest/run', { method: 'POST', body: '{}' }),
}
