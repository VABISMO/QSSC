// Thin client for the QSSC FastAPI backend. Uses relative /api paths so it
// works both through the Vite dev proxy and a same-origin production build.
const BASE = import.meta.env.VITE_API_BASE || ''

async function get(path) {
  const r = await fetch(`${BASE}${path}`)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

async function post(path, body) {
  const r = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

async function del(path) {
  const r = await fetch(`${BASE}${path}`, { method: 'DELETE' })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

const q = (o) => new URLSearchParams(o).toString()

export const api = {
  health: () => get('/api/health'),
  profiles: () => get('/api/profiles'),
  backendInfo: (profile, temperature_k, seed) =>
    get(`/api/backend/info?profile=${profile}&temperature_k=${temperature_k}&seed=${seed}`),
  odmr: (profile, b_field, points) =>
    get(`/api/odmr?profile=${profile}&b_field=${b_field}&points=${points}`),
  protocol: () => get('/api/protocol'),
  calibration: (profile) => get(`/api/calibration?profile=${profile}`),
  signal: (o) => get(`/api/signal?${q(o)}`),
  calibrationRun: (o) => post(`/api/calibration/run?${q(o)}`, {}),
  // async circuit execution (Redis + RQ) -> returns {job_id, status}
  run: (body) => post('/api/circuit/run', body),
  runCustom: (body) => post('/api/circuit/custom', body),
  // job polling + history (MongoDB)
  job: (id) => get(`/api/jobs/${id}`),
  jobs: (limit = 50) => get(`/api/jobs?limit=${limit}`),
  deleteJob: (id) => del(`/api/jobs/${id}`),
  // saved Qiskit scripts (Code tab, MongoDB)
  scripts: () => get('/api/scripts'),
  saveScript: (body) => post('/api/scripts', body),
  deleteScript: (id) => del(`/api/scripts/${id}`),
}