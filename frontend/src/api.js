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
  // pixel_mode (register/ensemble) + shipping-invention HW badges. Used by the
  // global-controls card to label the two modes and the Prime-denoise toggle.
  modes: () => get('/api/modes'),
  backendInfo: (profile, temperature_k, seed) =>
    get(`/api/backend/info?profile=${profile}&temperature_k=${temperature_k}&seed=${seed}`),
  odmr: (profile, b_field, points) =>
    get(`/api/odmr?profile=${profile}&b_field=${b_field}&points=${points}`),
  calibration: (profile) => get(`/api/calibration?profile=${profile}`),
  signal: (o) => get(`/api/signal?${q(o)}`),
  signalArray: (o) => get(`/api/signal/array?${q(o)}`),
  calibrationRun: (o) => post(`/api/calibration/run?${q(o)}`, {}),
  // async circuit execution (Redis + RQ) -> returns {job_id, status}
  run: (body) => post('/api/circuit/run', body),
  runCustom: (body) => post('/api/circuit/custom', body),
  // 64-parallel distributed computing (Invention 4) + per-pixel array
  // calibration (Invention 4+5). Calibrate first, then run-parallel with
  // use_calibration=true so each pixel runs at its measured register width.
  runParallel: (body) => post('/api/circuit/run-parallel', body),
  calibrateArray: (body) => post('/api/circuit/calibrate-array', body),
  // job polling + history (MongoDB)
  job: (id) => get(`/api/jobs/${id}`),
  jobs: (limit = 50) => get(`/api/jobs?limit=${limit}`),
  deleteJob: (id) => del(`/api/jobs/${id}`),
  // saved Qiskit scripts (Code tab, MongoDB)
  scripts: () => get('/api/scripts'),
  saveScript: (body) => post('/api/scripts', body),
  deleteScript: (id) => del(`/api/scripts/${id}`),
  // FPGA tab — synthesis metrics + live command protocol (localhost-only)
  fpgaInfo: () => get('/api/fpga/info'),
  fpgaProbe: () => get('/api/fpga/probe'),
  fpgaCommand: (command) => post('/api/fpga/command', { command }),
  // hardware QRNG — ADC-noise entropy -> Ed25519 keypair + cert (wraps READ_RNG)
  fpgaQrng: (o) => get(`/api/fpga/qrng?${q(o)}`),
  // on-chip quantum sensors (compass / gyro / magneto-optic image)
  compass: (o) => get(`/api/compass?${q(o)}`),
  gyro: (o) => get(`/api/gyro?${q(o)}`),
  magneto: (o) => get(`/api/magneto?${q(o)}`),
  // contactless field-imaging sensors (ferromagnetic / geomagnetic nav / temp / current)
  ferro: (o) => get(`/api/ferro?${q(o)}`),
  nav: (o) => get(`/api/nav?${q(o)}`),
  thermal: (o) => get(`/api/thermal?${q(o)}`),
  current: (o) => get(`/api/current?${q(o)}`),
  // tensor gradiometer (Invention 19) + dipole source localization
  tensor: (o) => get(`/api/sensor/tensor?${q(o)}`),
}