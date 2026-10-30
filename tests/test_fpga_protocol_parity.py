"""FPGA ↔ simulator parity gate (RTL verbs, API table, docs §5, sim surface).

The user-facing contract is three-fold and must not drift:

* ``fpga/rtl/command_parser.v`` implements the **19 wire verbs** of
  ``HARDWARE_DESIGN.md`` §5 (the char-by-char matcher is parsed here).
* ``api/routes_fpga._FPGA_PROTOCOL`` (served by the API) is exactly those 19
  plus the two **sim/host convenience verbs** (``READ_COMPASS``,
  ``READ_GYRO``) that have no RTL wire form.
* ``SimulatedFPGA.send_command`` accepts exactly the API table's surface —
  including ``ID``/``SET_GRADIENT``, so a string-level client behaves the same
  against either mode — and the documented reply-level divergence on
  ``APPLY_PULSE`` (sim enforces the resonance linewidth, the FPGA is a dumb
  tuner) stays written down in §5 + the API row.

Method-level parity (same call → same shape/keys in both modes) is asserted
empirically: the sim methods vs the hardware parsers fed with canonical
replies. No hardware, no serial port, no iverilog needed.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RTL = ROOT / "fpga" / "rtl" / "command_parser.v"
HARDWARE_MD = ROOT / "hardware" / "HARDWARE_DESIGN.md"
ROUTES_FPGA = ROOT / "api" / "routes_fpga.py"
SIMULATOR = ROOT / "qssc" / "simulator.py"
FRONTEND_PROTOCOL = ROOT / "frontend" / "src" / "constants.js"

SIM_ONLY = {"READ_COMPASS", "READ_GYRO"}


def _rtl_verbs() -> set[str]:
    """Parse the char-by-char matcher: cmd_str[i]=="C" runs give the verb, the
    following cmd_id <= CMD_x names its dispatch code."""
    src = RTL.read_text(encoding="utf-8")
    tok = re.compile(r'cmd_str\[(\d+)\]\s*==\s*"([A-Za-z0-9_])"')
    idtok = re.compile(r"cmd_id\s*<=\s*(CMD_[A-Z_0-9]+)")
    events = [(m.start(), "ch", m) for m in tok.finditer(src)]
    events += [(m.start(), "id", m) for m in idtok.finditer(src)]
    events.sort()
    verbs: set[str] = set()
    pending: dict[int, str] = {}
    for _, kind, m in events:
        if kind == "ch":
            i = int(m.group(1))
            if i == 0:
                pending = {0: m.group(2)}
            elif pending and i == max(pending) + 1:
                pending[i] = m.group(2)
            else:
                pending = {}
        elif pending:
            word = "".join(pending[k] for k in sorted(pending))
            if "_" in word or word.isalpha():
                verbs.add(word)
            pending = {}
    return verbs


def _docs_verbs() -> tuple[set[str], set[str]]:
    """(wire verbs, sim-only rows) from the §5 markdown table."""
    text = HARDWARE_MD.read_text(encoding="utf-8")
    sec = text.split("## 5. FPGA")[1].split("\n## 6.")[0]
    wire: set[str] = set()
    sim: set[str] = set()
    for line in sec.splitlines():
        m = re.match(r"\s*\|\s*`([A-Z][A-Z_0-9]*)", line)
        if not m:
            continue
        verb = m.group(1)
        if "sim/host only" in line:
            sim.add(verb)
        else:
            wire.add(verb)
    return wire, sim


def _api_verbs() -> set[str]:
    src = ROUTES_FPGA.read_text(encoding="utf-8")
    m = re.search(r"_FPGA_PROTOCOL = \[(.*?)\n\]", src, re.S)
    assert m, "_FPGA_PROTOCOL table not found"
    return set(re.findall(r'\{"cmd": "([A-Z_0-9]+)"', m.group(1)))


def _sim_verbs() -> set[str]:
    src = SIMULATOR.read_text(encoding="utf-8")
    return set(re.findall(r'head == "([A-Z_0-9]+)"', src))


def test_rtl_verbs_match_docs_section5():
    """The RTL matcher implements exactly the §5 wire table (the '19 verbs')."""
    rtl = _rtl_verbs()
    wire, _sim_rows = _docs_verbs()
    assert len(wire) == 19, f"§5 wire table has {len(wire)} rows, expected 19"
    missing = wire - rtl
    extra = rtl - wire
    assert not missing, f"§5 verbs missing from the RTL matcher: {sorted(missing)}"
    assert not extra, f"RTL matcher verbs absent from §5: {sorted(extra)}"


def test_api_protocol_table_is_docs_plus_sim_verbs():
    """The served API table = the19 wire verbs + the two sim/host verbs."""
    wire, sim_rows = _docs_verbs()
    api = _api_verbs()
    assert sim_rows == SIM_ONLY, f"§5 sim-only rows drifted: {sorted(sim_rows)}"
    assert api == wire | SIM_ONLY, (
        f"API table mismatch: missing={sorted((wire | SIM_ONLY) - api)}, "
        f"unexpected={sorted(api - (wire | SIM_ONLY))}")


def _frontend_verbs() -> set[str]:
    """The `cmd:` entries of the UI's FALLBACK_PROTOCOL (constants.js)."""
    src = FRONTEND_PROTOCOL.read_text(encoding="utf-8")
    return set(re.findall(r"cmd: '([A-Z_0-9]+)'", src))


def test_frontend_fallback_protocol_matches_api_table():
    """The UI's FALLBACK_PROTOCOL is a hand copy of the served API table.

    It is what the FPGA tab shows when /api/fpga/info is down or stale (the
    live list only wins on length), and it had already drifted: ID,
    READ_LOCKINP, RUN_ALL_ODMR and SET_GRADIENT were missing -- so degraded
    mode hid exactly the newer verbs, including the gradient command this
    repository's hardware work is about. The copy stays pinned both ways.
    """
    front = _frontend_verbs()
    api = _api_verbs()
    assert front == api, (
        f"frontend FALLBACK_PROTOCOL missing: {sorted(api - front)}; "
        f"unexpected: {sorted(front - api)}")


def test_sim_send_command_surface_matches_api_table():
    """The simulator answers exactly the API table's surface (ID and
    SET_GRADIENT included), so a string client behaves identically in both
    modes; anything else is ERR like the RTL."""
    api = _api_verbs()
    sim = _sim_verbs()
    assert sim == api, (
        f"sim send_command surface mismatch: missing={sorted(api - sim)}, "
        f"unexpected={sorted(sim - api)}")


def test_sim_unknown_verb_answers_err():
    from qssc.simulator import SimulatedFPGA
    fpga = SimulatedFPGA("NV", seed=5)
    assert fpga.send_command("NO_SUCH_VERB 1 2") == "ERR"
    assert fpga.send_command("READ_LOCKINP") == "ERR"   # missing args


def test_sim_id_and_set_gradient_reply_parity():
    """ID and SET_GRADIENT must answer like the RTL (QSSC v1 / OK-ERR)."""
    from qssc.simulator import SimulatedFPGA
    fpga = SimulatedFPGA("NV", seed=5)
    assert fpga.send_command("ID") == "QSSC v1"
    assert fpga.send_command("SET_GRADIENT 2048") == "OK"
    assert fpga.send_command("SET_GRADIENT 4096") == "ERR"
    assert fpga.send_command("SET_GRADIENT") == "ERR"


def test_method_shapes_identical_in_both_modes():
    """Same call, same result shape: sim methods vs hardware parsers fed with
    canonical replies (no serial needed)."""
    import numpy as np
    from qssc.simulator import SimulatedFPGA
    from qssc.backend_fpga import FPGAConnection

    fpga = SimulatedFPGA("NV", seed=5)
    for q in range(64):
        fpga.emitter_gs[q] = 4095

    # -- sim side --
    sim_lockin = fpga.read_lockin(0, 100)
    sim_lockinp = fpga.read_lockinp(0, 7, 3)
    sim_gyro = fpga.read_gyro(0, 1000)
    sim_seq = fpga.run_sequence(0, "ramsey", 4, 100)
    sim_odmr = fpga.run_all_odmr(2_870_000, 2_880_000, 16)

    # -- hardware side (parsers with canonical replies) --
    import threading
    conn = FPGAConnection.__new__(FPGAConnection)
    conn._io_lock = threading.Lock()
    conn.send_command = lambda cmd: "0000000123 0000000456 0000000050 0000000050"
    hw_lockin = conn.read_lockin(0, 100)
    conn.send_command = lambda cmd: "-0000000000001234 000000000000000 0000000007"
    hw_lockinp = conn.read_lockinp(0, 7, 3)
    conn.send_command = lambda cmd: "4 100 200 300 400"
    hw_seq = conn.run_sequence(0, "ramsey", 4, 100)
    gyro_codes = "64 " + " ".join(str(1000 + (i % 7) * 3) for i in range(64))
    conn.send_command = lambda cmd: gyro_codes
    hw_gyro = conn.read_gyro(0, 1000)
    conn.send_command = lambda cmd: ("16 " + " ".join("3000" for _ in range(16 * 64)))
    # run_all_odmr streams over the raw serial handle (not send_command): feed
    # it a fake port that yields n rows of 64 codes + OK.
    class _FakeSer:
        def __init__(self, rows):
            self._rows = list(rows)
        def write(self, _b):
            pass
        def readline(self):
            return self._rows.pop(0)
    conn.ser = _FakeSer(
        [((" ".join(["3000"] * 64) + "\n").encode()) for _ in range(16)]
        + [b"OK\n"])
    hw_odmr = conn.run_all_odmr(2_870_000, 2_880_000, 16)

    assert sorted(sim_lockin) == sorted(hw_lockin), (
        f"read_lockin keys differ: sim={sorted(sim_lockin)} hw={sorted(hw_lockin)}")
    assert sorted(sim_lockinp) == sorted(hw_lockinp), (
        f"read_lockinp keys differ: sim={sorted(sim_lockinp)} hw={sorted(hw_lockinp)}")
    assert sorted(sim_gyro) == sorted(hw_gyro), (
        f"read_gyro keys differ: sim={sorted(sim_gyro)} hw={sorted(hw_gyro)}")
    assert isinstance(sim_seq, np.ndarray) and isinstance(hw_seq, np.ndarray)
    assert sim_seq.shape == hw_seq.shape == (4,)
    assert sim_odmr.shape == hw_odmr.shape == (16, 64)


def test_apply_pulse_divergence_is_documented():
    """The one documented reply-level divergence (sim enforces the resonance
    linewidth, the FPGA tunes anywhere) must stay written down in §5 and in
    the API row -- if someone changes one side, this forces the docs to
    follow."""
    hd = HARDWARE_MD.read_text(encoding="utf-8")
    assert "Sim-vs-FPGA reply difference on `APPLY_PULSE`" in hd, (
        "§5 lost the APPLY_PULSE sim-vs-FPGA divergence note")
    src = ROUTES_FPGA.read_text(encoding="utf-8")
    gyro_row = re.search(r'\{"cmd": "READ_GYRO".*?\},', src, re.S)
    assert gyro_row and "sim convenience" in gyro_row.group(0), (
        "READ_GYRO row lost its sim-convenience annotation")
    comp_row = re.search(r'\{"cmd": "READ_COMPASS".*?\},', src, re.S)
    assert comp_row and "sim/host-only" in comp_row.group(0), (
        "READ_COMPASS row lost its sim/host-only annotation")
