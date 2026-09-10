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

def grid_top_bitstring(counts: dict, num_clbits: int) -> list[int]:
    """The bit each QubitGrid cell shows (1 = lit) under the top-bitstring rule.

    The grid lights cell i iff the most-counted (best-statistics) outcome has
    classical bit i = 1. Qiskit bitstrings are MSB-first, so bit i (qubit i)
    maps to string index ``num_clbits - 1 - i``. This mirrors the
    ``QubitGrid`` component: the lit cells are exactly the 1-bits of the top
    counts entry, so the grid agrees with the histogram's top bar and the
    reported numeric result (instead of the old marginal P(|1>) which readout
    noise pushed toward ~0.5 everywhere -> every cell lit).
    """
    if not counts or num_clbits <= 0:
        return [0] * num_clbits
    top = max(counts, key=counts.get).replace(" ", "")
    out = [0] * num_clbits
    for i in range(num_clbits):
        s = num_clbits - 1 - i
        if 0 <= s < len(top) and top[s] == "1":
            out[i] = 1
    return out


def grid_confidence(counts: dict) -> float:
    """The top state's share of total shots (the grid's lit intensity)."""
    total = sum(counts.values()) if counts else 0
    if total <= 0:
        return 0.0
    return max(counts.values()) / total


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
    "001". The grid lights cell 0 (q0), not cell 2.
    """
    # |001>: only q0 is |1>; qiskit prints c2 c1 c0 = "001"
    grid = grid_top_bitstring({"001": 1000}, 3)
    assert grid == [1, 0, 0]   # q0 lit, q1/q2 dark


def test_grid_lights_only_top_bitstring_ones():
    """The grid lights exactly the 1-bits of the TOP counts entry, no others.

    Reproduces the user's math-mul case: result 15 = 0b1111 in a 10-bit
    register -> qiskit top state "0000001111" -> grid lights cells 0-3 only,
    not 0-9. The old marginal renderer lit every cell because noise pushed
    each marginal toward 0.5.
    """
    counts = {"0000001111": 1500, "0000001110": 12, "0000011111": 5}
    grid = grid_top_bitstring(counts, 10)
    assert grid == [1, 1, 1, 1, 0, 0, 0, 0, 0, 0]
    assert sum(grid) == 4    # exactly the four 1-bits of 15


def test_histogram_display_matches_grid_order():
    """After the fix, histogram position i == grid cell i == qubit i.

    For qiskit "001" (q0=1), the reversed display is "100"; reading it
    left-to-right (position 0..2) gives [1,0,0] which matches the grid's
    top-bitstring cells [1,0,0]. The RAW qiskit string read left-to-right gives
    [0,0,1] which would imply cell 2 -- the mismatch the user saw.
    """
    state = "001"                      # qiskit MSB-first: q2=0,q1=0,q0=1
    grid = grid_top_bitstring({state: 1}, 3)
    disp = histogram_display(state)    # "100" after fix
    hist_bits = [int(ch) for ch in disp]
    assert hist_bits == grid
    # and the raw (pre-fix) string would NOT match -- this is the bug
    raw_bits = [int(ch) for ch in state]
    assert raw_bits != grid


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
    grid = grid_top_bitstring(r["counts"], r["num_clbits"])
    # q0 is the amplified qubit -> cell 0 is lit
    assert grid[0] == 1, (top, grid)
    # the fixed histogram display of the top state has its leftmost bit = 1
    assert histogram_display(top)[0] == "1", (top, histogram_display(top))


def test_math_mul_grid_matches_result_and_histogram():
    """The reported bug: math mul a=3 b=5 bits=4 -> result 15, histogram
    "1111000000" (q0-leftmost reversed of "0000001111"), but the grid showed
    every cell lit. After the fix the grid lights cells 0-3 (the four 1-bits of
    15), matching both the histogram's top bar and the numeric result=15.
    """
    r = run_circuit({"algorithm": "math", "op": "mul", "a": 3, "b": 5,
                     "bits": 4, "profile": "NV", "temperature_k": ROOM,
                     "seed": SEED, "shots": SHOTS, "noise_mode": "real"})
    assert r["result"] == 15
    top = max(r["counts"], key=r["counts"].get)
    grid = grid_top_bitstring(r["counts"], r["num_clbits"])
    disp = histogram_display(top)
    # the grid's lit cells == the histogram's left-to-right 1-bits
    assert grid == [int(ch) for ch in disp]
    # the lit cells are exactly the 1-bits of the numeric result (15 = 0b1111)
    assert grid[:4] == [1, 1, 1, 1]
    assert sum(grid) == bin(r["result"]).count("1")


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


# -- bug 3: runaway N hangs the panel (N / qubit cap) --------------------------

@pytest.mark.parametrize("algo", ["shor", "period", "qftnos"])
@pytest.mark.parametrize("n", [21, 35])
def test_factor_n_cap_rejects_too_large(algo, n):
    """N > 15 (>13 qubits) must be rejected up front so the Aer statevector sim
    cannot hang the in-process fallback worker past the 120 s frontend timeout
    (the "shor hangs from the panel" symptom). N=21 -> 16 qubits takes >120 s
    and decoheres to no factors at 300 K; N=35 -> 19 qubits hangs Aer outright.
    N=15 is the largest that factors at 300 K NV and runs in <1 s."""
    r = run_circuit({"algorithm": algo, "n": n, "profile": "NV",
                     "temperature_k": ROOM, "seed": SEED, "shots": SHOTS,
                     "noise_mode": "real"})
    assert "error" in r
    assert "too large" in r["error"].lower()