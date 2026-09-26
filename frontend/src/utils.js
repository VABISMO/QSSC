// Pure helpers for the QSSC dashboard.
//
// Extracted from App.jsx: bit-length + derived-qubit-count math for the
// factoring algorithms (shor/period/qftnos), and the PEM-file download helper
// for the QRNG cert export. No React, no side effects beyond the DOM download.

// helpers for shor / period / qftnos: the circuit width is a function of the
// integer N being factored (shor/period = 3·bitlen(N)+1, qftnos = 2·bitlen(N)+1).
// We keep N editable and show the qubit count as a READ-ONLY derived readout
// (shor/period = 3·bitlen(N)+1, qftnos = 2·bitlen(N)+1). Editing the qubit
// count never silently snaps N to a semiprime -- that bidirectional coupling
// made "inputs change by themselves" when the operator touched the qubits
// field.
export const bitLen = (n) => (n <= 1 ? 1 : Math.floor(Math.log2(n)) + 1)
export const derivedQubits = (algo, n) => algo === 'qftnos' ? 2 * bitLen(n) + 1 : 3 * bitLen(n) + 1

export const downloadPem = (filename, text) => {
  const blob = new Blob([text], { type: 'application/x-pem-file' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}