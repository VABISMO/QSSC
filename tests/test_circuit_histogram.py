"""Grid vs measurement-histogram consistency + input-sensitivity tests.

Two UI bugs were reported against the simulator dashboard:

1. **Histogram / grid bit-order mismatch.** The 8x8 ``QubitGrid`` is indexed by
   qubit number (cell ``i`` = qubit ``i``, q0 top-left) and lights a cell with
   that qubit's measured-|1⟩ probability. The ``BarCounts`` histogram showed the
   raw qiskit bitstring, which is MSB-first (leftmost char = q_{n-1}). So on an
   asymmetric state the histogram read left-to-right as q0..qn while the grid
   read left-to-right as q0..qn -- opposite orders -- and they visibly
   disagreed. Example: grover marked=1 on 3 qubits -> qiskit top state "001"
   (q2=0,q1=0,q0=1); the grid correctly lights cell 0 (q0=1), but the histogram
   "001" read left-to-right implies cell 2. The fix is to display the histogram
   q0-leftmost (reverse the qiskit string) so position i in the histogram =
   cell i in the grid = qubit i.

2. **Histogram insensitive to the circuit input.** Several algorithms returned
   the same measurement histogram regardless of the input: ``qrand`` was fully
   deterministic because the frontend always sent the fixed ``seed`` (defeating
   the OS-entropy default of issue #45), and ``qft`` is correctly ~uniform for
   every input x (that is physics, not a bug).

These tests model the frontend's ``QubitGrid`` and ``BarCounts`` logic in Python
and assert (a) the bit convention is consistent between the grid and a
q0-leftmost histogram display, and (b) each algorithm's counts respond to its
specific input.
"""
from __future__ import annotations

import pytest

from api.runner import run_circuit

ROOM = 300.0
SEED = 42
SHOTS = 2048


# -- frontend logic, mirrored from frontend/src/components.jsx ------------------

def grid_per_qubit_p1(counts: dict, num_clbits: int) -> list[float]:
    """Per-qubit P(measured |1>) as the QubitGrid computes it.

    Qiskit bitstrings are MSB-first, so classical bit i (qubit i) maps to
    string index ``num_clbits - 1 - i``. Cell i in the grid shows this value.
    """
    total = sum(counts.values())
    p1 = [0.0] * num_clbits
    if total <= 0:
        return p1
    for bs, c in counts.items():
        bs = bs.replace(" ", "")
        for i in range(num_clbits):
            s = num_clbits - 1 - i
            if 0 <= s < len(bs) and bs[s] == "1":
                p1[i] += c
    return [p / total for p in p1]


def histogram_display(state: str) -> str:
    """The bitstring label BarCounts shows after the fix: q0-leftmost.

    The raw qiskit state is MSB-first (leftmost = q_{n-1}); reversing it makes
    the displayed string read q0..q_{n-1} left-to-right, matching the grid's
    cell order (cell i = qubit i, q0 top-left).
    """
    return state.replace(" ", "")[::-1]


# -- bug 1: grid / histogram bit convention ------------------------------------

def test_grid_uses_qiskit_msb_first_convention():
    """The grid maps qubit i to string index num_clbits-1-i (qiskit MSB-first).

    A deterministic 3-qubit state |001> (q0=1, q1=0, q2=0) -> qiskit string
    "001". The grid must light cell 0 (q0), not cell 2.
    """
    # |001>: only q0 is |1>; qiskit prints c2 c1 c0 = "001"
    counts = {"001": 1000}
    p1 = grid_per_qubit_p1(counts, 3)
    assert p1[0] == pytest.approx(1.0)   # q0 lit
    assert p1[1] == pytest.approx(0.0)
    assert p1[2] == pytest.approx(0.0)


def test_histogram_display_matches_grid_order():
    """After the fix, histogram position i == grid cell i == qubit i.

    For qiskit "001" (q0=1), the reversed display is "100"; reading it
    left-to-right (position 0..2) gives [1,0,0] which matches the grid's
    per-qubit P(|1>) = [1,0,0]. The RAW qiskit string read left-to-right gives
    [0,0,1] which would imply cell 2 -- the mismatch the user saw.
    """
    state = "001"                      # qiskit MSB-first: q2=0,q1=0,q0=1
    grid = grid_per_qubit_p1({state: 1}, 3)
    disp = histogram_display(state)    # "100" after fix
    # histogram left-to-right bit pattern
    hist_bits = [int(ch) for ch in disp]
    assert hist_bits == [int(round(p)) for p in grid]
    # and the raw (pre-fix) string would NOT match -- this is the bug
    raw_bits = [int(ch) for ch in state]
    assert raw_bits != [int(round(p)) for p in grid]


def test_grover_asymmetric_top_state_lights_correct_grid_cell():
    """Reproduces the user's mismatch case end-to-end via the API.

    grover marked=1 on 3 qubits amplifies |1> (q0=1) -> qiskit top state "001".
    The grid lights cell 0; the fixed histogram shows "100" (q0 leftmost) so
    its leftmost bar bit is 1 = cell 0. They agree.
    """
    r = run_circuit({"algorithm": "grover", "n_items": 3, "marked": 1,
                     "profile": "NV", "temperature_k": ROOM, "seed": SEED,
                     "shots": SHOTS, "noise_mode": "real"})
    top = max(r["counts"], key=r["counts"].get)
    p1 = grid_per_qubit_p1(r["counts"], r["num_clbits"])
    # q0 is the amplified qubit -> highest P(|1>) among the three
    assert p1.index(max(p1)) == 0, (top, p1)
    # the fixed histogram display of the top state has its leftmost bit = 1
    assert histogram_display(top)[0] == "1", (top, histogram_display(top))


# -- bug 2: input sensitivity -------------------------------------------------

def test_qrand_without_seed_uses_os_entropy_and_differs():
    """Two qrand runs WITHOUT a seed must differ (issue #45 OS-entropy default).

    The frontend must NOT send the fixed seed for qrand, or every run returns
    the same entropy / same Ed25519 key -- the 'same histogram regardless of
    input' complaint.
    """
    r1 = run_circuit({"algorithm": "qrand", "num_qubits": 8, "num_bytes": 16,
                      "profile": "NV", "temperature_k": ROOM, "shots": 8192,
                      "noise_mode": "real"})
    r2 = run_circuit({"algorithm": "qrand", "num_qubits": 8, "num_bytes": 16,
                      "profile": "NV", "temperature_k": ROOM, "shots": 8192,
                      "noise_mode": "real"})
    assert "error" not in r1 and "error" not in r2
    assert r1["entropy_hex"] != r2["entropy_hex"]


def test_qrand_with_seed_is_reproducible():
    """An explicit seed must stay reproducible (backend contract; tests rely)."""
    req = {"algorithm": "qrand", "num_qubits": 8, "num_bytes": 16,
           "profile": "NV", "temperature_k": ROOM, "seed": 5, "shots": 8192,
           "noise_mode": "real"}
    a = run_circuit(dict(req)); b = run_circuit(dict(req))
    assert a["entropy_hex"] == b["entropy_hex"]


def test_grover_histogram_responds_to_marked_input():
    a = run_circuit({"algorithm": "grover", "n_items": 3, "marked": 2,
                     "profile": "NV", "temperature_k": ROOM, "seed": SEED,
                     "shots": SHOTS, "noise_mode": "real"})
    b = run_circuit({"algorithm": "grover", "n_items": 3, "marked": 5,
                     "profile": "NV", "temperature_k": ROOM, "seed": SEED,
                     "shots": SHOTS, "noise_mode": "real"})
    assert a["found"] == 2 and b["found"] == 5
    assert a["counts"] != b["counts"]


def test_math_histogram_responds_to_operands():
    a = run_circuit({"algorithm": "math", "op": "mul", "a": 3, "b": 5, "bits": 4,
                     "profile": "NV", "temperature_k": ROOM, "seed": SEED,
                     "shots": SHOTS, "noise_mode": "real"})
    b = run_circuit({"algorithm": "math", "op": "mul", "a": 3, "b": 7, "bits": 4,
                     "profile": "NV", "temperature_k": ROOM, "seed": SEED,
                     "shots": SHOTS, "noise_mode": "real"})
    assert a["result"] == 15 and b["result"] == 21
    assert a["counts"] != b["counts"]


def test_bell_histogram_responds_to_qubit_count():
    a = run_circuit({"algorithm": "bell", "num_qubits": 2,
                     "profile": "NV", "temperature_k": ROOM, "seed": SEED,
                     "shots": 1024, "noise_mode": "real"})
    b = run_circuit({"algorithm": "bell", "num_qubits": 3,
                     "profile": "NV", "temperature_k": ROOM, "seed": SEED,
                     "shots": 1024, "noise_mode": "real"})
    # different qubit count -> different bitstring lengths -> different counts
    assert a["num_qubits"] == 2 and b["num_qubits"] == 3
    assert a["counts"] != b["counts"]


def test_qft_is_uniform_regardless_of_x():
    """QFT of any computational-basis |x> is an equal-amplitude superposition,
    so the measurement histogram is ~uniform regardless of x. This is correct
    physics, NOT a bug -- asserted here so a future regression that makes x
    matter is caught."""
    a = run_circuit({"algorithm": "qft", "num_qubits": 3, "x": 1,
                     "profile": "NV", "temperature_k": ROOM, "seed": SEED,
                     "shots": SHOTS, "noise_mode": "real"})
    b = run_circuit({"algorithm": "qft", "num_qubits": 3, "x": 5,
                     "profile": "NV", "temperature_k": ROOM, "seed": SEED,
                     "shots": SHOTS, "noise_mode": "real"})
    assert a["uniform"] is True and b["uniform"] is True