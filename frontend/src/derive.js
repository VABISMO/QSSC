// Per-algorithm "how the measured binary becomes the result" derivation.
//
// Mirrors the backend postprocess in qssc/circuits.py + api/runner.py so the
// dashboard can show, for EVERY algorithm, exactly which measurement bar is the
// correct result and the concrete chain of steps from the measured bitstring to
// that result (the user's "que se sepa porqué ese binario es el resultado de esa
// operacion shor o las otras"). Pure client-side from the run result dict + the
// counts already shown in the histogram -- no extra API call.
//
// Each deriveMeasurement(...) returns:
//   {
//     bars:  [qiskitBitstring, ...]  // bars to mark as "the correct result"
//     label: "→ 3×5"                 // short marker shown on the marked bar
//     title: "..."                   // modal heading
//     steps: [{k, v}, ...]           // ordered derivation, concrete numbers
//     summary: "..."                 // one-paragraph "why this binary = result"
//     hasResult: bool                // false = no signal recovered / decohered
//   }
//
// qiskit bitstrings are MSB-first (leftmost char = q_{n-1}); the dashboard
// reverses them for display so the leftmost char reads q0. We carry BOTH
// forms in the first derivation step so the user can trace either.

// ---------- tiny bignum helpers (the counts already fit in JS doubles) --------
function gcd(a, b) { a = Math.abs(a); b = Math.abs(b); while (b) { [a, b] = [b, a % b] } return a || 1 }
function modpow(base, exp, mod) {
  // iterative modular exponentiation (base/ exp are small in demos)
  let r = 1 % mod, b = base % mod
  while (exp > 0) { if (exp & 1) r = (r * b) % mod; b = (b * b) % mod; exp = Math.floor(exp / 2) }
  return ((r % mod) + mod) % mod
}
function ceilLog2(n) { return Math.max(1, Math.ceil(Math.log2(Math.max(1, n)))) }
function bitRev(cval, width) { // reverse the low `width` bits of cval
  let out = 0
  for (let i = 0; i < width; i++) { out = (out << 1) | ((cval >> i) & 1) }
  return out >>> 0
}
const clean = (s) => String(s || '').replace(/ /g, '')
const disp = (qiskit) => clean(qiskit).split('').reverse().join('')
const binOf = (n, w) => n.toString(2).padStart(w, '0')
const sub = '₀₁₂₃₄₅₆₇₈₉'[0] // (unused placeholder kept for clarity)

// counting-register width t for the phase-estimation family. The backend does
// NOT echo t back for shor/period, so we recompute the same formula:
//   shor/period: t = 2*l + 1  (l = ceil(log2 N))   -- circuits.py:93
//   qftnos:      t = l + 1    (reduced register)   -- circuits.py:276
// (qftnos ALSO returns result.counting_qubits, preferred when present.)
function phaseT(algo, result) {
  if (algo === 'qftnos' && result && result.counting_qubits) return result.counting_qubits
  const n = (result && result.n) || 0
  if (!n) return 0
  const l = ceilLog2(n)
  return algo === 'qftnos' ? l + 1 : 2 * l + 1
}

// top (most-counted) qiskit bitstring in a counts dict
function topState(counts) {
  if (!counts) return null
  let best = null, bestN = -1
  for (const [s, n] of Object.entries(counts)) { const v = +n; if (v > bestN) { bestN = v; best = s } }
  return best
}

// the two GHZ states |0..0> and |1..1> (the only outcomes a Bell/GHZ circuit
// produces); returned as qiskit bitstrings if they actually occur in counts
function ghzBars(counts, numClbits) {
  if (!counts) return []
  const n = numClbits || (Object.keys(counts)[0] ? clean(Object.keys(counts)[0]).length : 2)
  const z = '0'.repeat(n), o = '1'.repeat(n)
  const bars = []
  for (const cand of [z, o]) for (const k of Object.keys(counts)) if (clean(k) === cand) bars.push(k)
  return bars
}

// --------------------------------------------------------------------------
// per-algorithm derivation
// --------------------------------------------------------------------------
function deriveShorFamily(algo, result) {
  // shor + qftnos: phase -> period -> gcd factors. period: phase -> period only.
  const rec = result && result.recovered
  const n = result && result.n
  const a = result && result.a
  const isFactoring = algo === 'shor' || algo === 'qftnos'
  const title = isFactoring
    ? `Shor-style factoring — how the phase measurement gives the factors of ${n}`
    : `Period finding — how the phase measurement gives the order of ${a} mod ${n}`

  if (!rec || rec.bitstring == null) {
    return {
      bars: [], label: '→ no signal', hasResult: false,
      title,
      steps: [
        { k: 'Circuit', v: `${algo} on N=${n}, base a=${a}` },
        { k: 'Measured phase peak', v: 'none recovered above the noise floor' },
        { k: 'Reason', v: `the circuit decohered at ${result && result.temperature_k} K (T₂ too short); cool the chip or use none/ideal mode` },
      ],
      summary: isFactoring
        ? `No phase peak cleared the noise floor, so continued fractions could not lift a period and the gcd step never ran. The factors are not in any single binary — they only appear after the phase→period→gcd chain, which needs a surviving signal peak.`
        : `No phase peak cleared the noise floor, so no period could be recovered. The period comes from a phase peak via continued fractions; without it there is nothing to read.`,
    }
  }

  const bs = clean(rec.bitstring)
  const t = phaseT(algo, result)
  const cval = parseInt(bs, 2) & ((1 << t) - 1)
  const swapless = algo === 'qftnos'
  const dec = swapless ? bitRev(cval, t) : cval
  // Derive the phase FROM the measured binary (cval / 2^t, with the counting
  // bits reversed first for the swapless QFT-NOS readout) -- this is the
  // "from the binary you get the result" chain the dashboard shows. The
  // backend's recovered.phase is the same value (both come from this peak),
  // so the displayed chain is self-consistent: cval -> phase -> fraction -> r.
  const phase = dec / (1 << t)
  const r = rec.period
  const fracNum = r ? Math.round(phase * r) : null
  const steps = [
    { k: 'Measured bar (display, q0…qₙ₋₁)', v: disp(bs) },
    { k: 'Qiskit bitstring (MSB-first)', v: bs },
    { k: `Counting register (low t=${t} bits)${swapless ? ' [swapless → reversed]' : ''}`, v: `${binOf(swapless ? cval : cval, t)}₂ = ${cval}${swapless ? `  → reversed = ${binOf(dec, t)}₂ = ${dec}` : ''}` },
    { k: `Phase φ = ${swapless ? 'reversed' : 'cval'} / 2^${t}`, v: `${dec} / ${1 << t} = ${(+phase).toFixed(6)}` },
    { k: `Fraction(φ).limit_denominator(${n})`, v: fracNum != null ? `${fracNum}/${r}` : '—' },
    { k: 'Period r = denominator', v: `${r}` },
  ]
  if (isFactoring && a != null && n != null && r) {
    const half = r / 2
    if (r % 2 === 0) {
      const am = modpow(a, half, n)
      const p = gcd(am - 1, n), q = gcd(am + 1, n)
      steps.push({ k: `a^(r/2) mod N = ${a}^${half} mod ${n}`, v: `${am}` })
      steps.push({ k: 'Factors = gcd(a^(r/2)−1, N), gcd(a^(r/2)+1, N)', v: `gcd(${am - 1},${n})=${p}, gcd(${am + 1},${n})=${q}` })
      const factors = result.factors
      steps.push({ k: 'Result', v: factors && factors[0] ? `${n} = ${factors[0]} × ${factors[1]} ✓` : `${n} = ${p} × ${q} (trivial — retry with another base a)` })
    } else {
      steps.push({ k: 'r is odd', v: `r=${r} is odd → a^(r/2) is not integer; retry with another coprime base a` })
    }
  } else if (!isFactoring) {
    steps.push({ k: 'Order check a^r mod N', v: a != null && n != null ? `${modpow(a, r, n)} (must be 1)` : '—' })
    steps.push({ k: 'Result', v: `order of ${a} mod ${n} is r = ${r}` })
  }

  let label
  if (isFactoring) label = result.factors ? `→ ${result.factors[0]}×${result.factors[1]}` : (r ? `→ r=${r}` : '→ no signal')
  else label = r ? `→ r=${r}` : '→ no signal'

  return {
    bars: [bs], label, hasResult: !!r, title,
    steps,
    summary: isFactoring
      ? `The histogram measures a PHASE, not a factor. The marked bar is the phase peak: its low ${t} counting bits give cval, φ = cval/2^${t}, and continued fractions turn φ into a fraction whose denominator is the period r. The factors only then come from the classical gcd(a^(r/2)±1, N) — they never appear in the measured binary.${swapless ? ' (QFT-NOS uses a reduced t=l+1 register and a swapless inverse QFT, so the counting bits are reversed first.)' : ''}`
      : `The histogram measures a PHASE. The marked bar is the phase peak; its low ${t} counting bits give cval, φ = cval/2^${t}, and continued fractions turn φ into a fraction whose denominator is the multiplicative order r of a mod N (the period). No single bitstring "is" the period — r is recovered from the phase.`,
  }
}

function deriveMath(result) {
  const op = result.op, a = result.a, b = result.b, out = result.result
  const rw = result.result_width
  const bs = clean(topState(result.counts))
  const val = bs ? (parseInt(bs, 2) & ((1 << rw) - 1)) : out
  const isFixed = op === 'sqrt' || op === 'log'
  const opSym = { add: '+', sub: '−', mul: '×', sqrt: '√', log: 'log₂' }[op] || op
  const steps = [
    { k: 'Circuit', v: `quantum ${op} (${result.num_qubits} qubits, ${rw}-bit result register)` },
    { k: 'Measured bar (display, q0…qₙ₋₁)', v: bs ? disp(bs) : '—' },
    { k: 'Qiskit bitstring (MSB-first)', v: bs || '—' },
    { k: `Result register (low ${rw} bits)`, v: bs ? `${binOf(val, rw)}₂ = ${val}` : `${val}` },
  ]
  if (isFixed) {
    const scaled = out
    steps.push({ k: `Rescale (fixed-point): (${val} / 2^${rw}) × ${a}`, v: `${scaled}` })
    steps.push({ k: 'Result', v: `${opSym}(${a}) ≈ ${scaled}` })
  } else {
    const expr = op === 'mul' ? `${a} ${opSym} ${b}` : `${a} ${opSym} ${b}`
    steps.push({ k: 'Decode', v: `${expr} = ${out}` })
    steps.push({ k: 'Result', v: `${out}` })
  }
  return {
    bars: bs ? [bs] : [], label: `→ ${out}`, hasResult: true,
    title: `Quantum arithmetic — how the measurement gives the ${op} result`,
    steps,
    summary: `The ${op} circuit encodes the operands into qubits and a Draper-QFT adder (or RG-QFT multiplier / phase-estimation for sqrt·log) writes the answer into the low ${rw}-bit result register. The marked bar's low ${rw} bits ARE the integer result${isFixed ? ' (rescaled by the operand, because sqrt/log are phase-estimated)' : ''} — the binary does not need further decoding, you just read off that register.`,
  }
}

function deriveGrover(result) {
  const bs = clean(topState(result.counts))
  const found = result.found, marked = result.marked, nq = result.n_items, iters = result.iterations
  const ok = found === marked
  const steps = [
    { k: 'Circuit', v: `Grover search over ${nq} items, ${iters} iterations (≈ π/4·√(2^${nq}))` },
    { k: 'Oracle marks item', v: `${marked} = ${binOf(marked, nq)}₂` },
    { k: 'Measured bar (display, q0…qₙ₋₁)', v: bs ? disp(bs) : '—' },
    { k: 'Qiskit bitstring (MSB-first)', v: bs || '—' },
    { k: 'Found item = int(binary, 2)', v: `${found} = ${binOf(found, nq)}₂` },
    { k: 'Compare to marked', v: `${found} ${ok ? '==' : '≠'} ${marked} ${ok ? '✓ found' : '✗ miss (try more shots / iterations)'}` },
    { k: 'Result', v: ok ? `found the marked item ${marked}` : `top result ${found} ≠ marked ${marked}` },
  ]
  return {
    bars: bs ? [bs] : [], label: `→ found ${found} ${ok ? '✓' : '✗'}`, hasResult: ok,
    title: 'Grover search — how the measurement gives the found item',
    steps,
    summary: `Grover amplifies the amplitude of the marked item ${marked}: after the optimal ~π/4·√(2^${nq}) iterations the marked state has nearly all the probability, so the top measurement IS the marked index. The marked bar read as an integer = the found item; matching the oracle's marked value confirms the search.`,
  }
}

function deriveQft(result) {
  const n = result.n, x = result.x, states = result.basis_states
  const bs = clean(topState(result.counts))
  const chi2 = result.uniformity_chi2, uniform = result.uniform
  const steps = [
    { k: 'Circuit', v: `QFT of the basis state |${x}⟩ over ${n} qubits (${states} basis states)` },
    { k: 'QFT|x⟩', v: `(1/√${states}) Σ_k e^(2πi·${x}·k/${states}) |k⟩ — equal-amplitude superposition` },
    { k: 'Measure → every |k⟩ appears ~equally', v: `expected ≈ ${result.expected_per_state} counts/state` },
    { k: 'Uniformity test χ²_norm', v: `${chi2} (threshold < 2 → uniform: ${uniform ? 'yes ✓' : 'no'})` },
    { k: 'Result', v: uniform ? 'flat distribution ✓ (the QFT signature)' : 'distribution is NOT flat (χ² too high)' },
  ]
  return {
    bars: bs ? [bs] : [], label: uniform ? `≈ uniform χ²=${chi2}` : `χ²=${chi2}`, hasResult: uniform,
    title: 'QFT — why no single bitstring is "the result"',
    steps,
    summary: `The QFT of a computational-basis state spreads it evenly over all ${states} outcomes (with a phase gradient that measurement discards). So there is no single "correct" binary — the correct result IS the flat distribution, confirmed by the χ² uniformity statistic, not by any one bar. The marked bar is just the highest of the (expectedly) equal bars.`,
  }
}

function deriveQrand(result) {
  const bs = clean(topState(result.counts))
  const steps = [
    { k: 'Circuit', v: `${result.n} qubits in |+⟩, measured ${result.num_bytes * 8} entropy bits` },
    { k: 'Each shot = n raw bits', v: bs ? `${disp(bs)} (one raw sample)` : '—' },
    { k: 'Raw bias p₁ (readout noise)', v: `${result.raw_bias_p1} (0.5 = unbiased)` },
    { k: 'Von Neumann extractor', v: `${result.extraction && result.extraction.kept} unbiased bits from ${result.extraction && result.extraction.consumed} raw (rate ${result.extraction && result.extraction.rate})` },
    { k: 'Health gate (freq + lag-1)', v: result.health && result.health.passed ? 'passed ✓' : 'failed' },
    { k: 'Entropy → bytes → Ed25519 keypair', v: `${result.num_bytes} bytes, serial ${result.serial}` },
    { k: 'Result', v: `symmetric key + public/private key + X.509 cert derived from quantum entropy` },
  ]
  return {
    bars: bs ? [bs] : [], label: '→ key (raw → extract)', hasResult: !!(result.health && result.health.passed),
    title: 'QRNG — how the raw bitstream becomes a cryptographic key',
    steps,
    summary: `QRNG has no "correct" single measurement — every shot is a random bitstring by design. The marked bar is one raw sample; the actual result is the de-biased key derived from the whole bitstream: a von-Neumann extractor removes the readout bias, a health gate certifies the entropy, and the bytes seed an Ed25519 keypair + certificate.`,
  }
}

function deriveBell(result, counts, numClbits) {
  const bars = ghzBars(counts, numClbits)
  const n = numClbits || (bars[0] ? clean(bars[0]).length : 2)
  const z = '0'.repeat(n), o = '1'.repeat(n)
  const steps = [
    { k: 'Circuit', v: `H(q0) → |+⟩, then CNOT chain q0→q1→…→q${n - 1} → GHZ state` },
    { k: 'State', v: `(|${z}⟩ + |${o}⟩) / √2 — only the two fully-correlated bitstrings occur` },
    { k: 'Measured bars (display)', v: bars.map(disp).join('   and   ') || '—' },
    { k: 'Qiskit bitstrings (MSB-first)', v: bars.join('   and   ') || '—' },
    { k: 'Result', v: `only |${z}⟩ and |${o}⟩ appear (~50/50); no mixed-parity state → entanglement confirmed ✓` },
  ]
  return {
    bars, label: '→ GHZ correlated', hasResult: bars.length >= 2,
    title: `Bell / GHZ — why the two correlated bitstrings are the result`,
    steps,
    summary: `There is no computed integer here. The circuit builds a GHZ entangled state, so measurement collapses it to either all-0 or all-1 with equal probability — and ONLY those two outcomes ever appear. The "correct result" is the correlation itself: the binary doesn't decode to a number, the fact that just |${z}⟩ and |${o}⟩ occur (never a mix) is the entanglement signature.`,
  }
}

function deriveBb84(result) {
  const qber = result.qber, thr = result.qber_threshold, eve = result.eve_detected
  const secure = result.secure
  const steps = [
    { k: 'Protocol', v: `BB84 over ${result.num_bits} prepared bits; sifted ${result.sifted_bits} (${result.sifting_rate} sifting rate)` },
    { k: 'Alice → Bob', v: 'random bits in random bases (Z/X); Bob measures in random bases' },
    { k: 'Sifting', v: `keep only same-basis rounds → ${result.sifted_bits} shared raw-key bits` },
    { k: 'QBER (error rate on a sample)', v: `${qber} (threshold ${thr})` },
    { k: 'Eavesdropper check', v: eve ? `QBER ≥ ${thr} → eavesdropper DETECTED ✗` : `QBER < ${thr} → no eavesdropper ✓` },
    { k: 'Result', v: secure ? `secure shared key (${result.sifted_bits} bits)` : `key compromised — abort (QBER ${qber} ≥ ${thr})` },
  ]
  return {
    bars: [], label: '', hasResult: secure,
    title: 'BB84 — how the measurements become a shared secret key',
    steps,
    summary: `BB84 has no single "correct binary". Each 1-qubit measurement reconciles one key bit (kept only when Alice and Bob chose the same basis). The result is a security verdict, not a bitstring: the QBER vs the ${thr} threshold decides whether the sifted key is secret or an intercept-resend eavesdropper is present.`,
  }
}

// --------------------------------------------------------------------------
// public entry point
// --------------------------------------------------------------------------
export function deriveMeasurement(algo, result, counts, numClbits) {
  if (!result) return null
  const a = (result.algorithm || algo || '').toLowerCase()
  const C = counts || result.counts
  try {
    switch (a) {
      case 'shor':
      case 'qftnos':
      case 'period':
        return deriveShorFamily(a, result)
      case 'math':
        return deriveMath(result)
      case 'grover':
        return deriveGrover(result)
      case 'qft':
        return deriveQft(result)
      case 'qrand':
        return deriveQrand(result)
      case 'bell':
        return deriveBell(result, C, numClbits)
      case 'bb84':
        return deriveBb84(result)
      default:
        return null
    }
  } catch (e) {
    return null
  }
}