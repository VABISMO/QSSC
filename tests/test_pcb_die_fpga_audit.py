# tests/test_pcb_die_fpga_audit.py
"""Per-datasheet pin audit for the two die-socket faces and the FPGA, pure-Python
(no ``pcbnew``, CI-runnable).

Answers the user's "are all Uchip pins correctly connected? and the FPGA's?":

  1. **Uchip (main, emitter face)**: 64 ``emitter_cat0..63`` pads + one
     ``EM_ANODE`` common-anode pad on the ``+12V_EMIT`` rail. No ``DET_IN`` may
     leak onto the emitter face (the die is two-faced no-TSV).
  2. **Uchip (head, detector face)**: 64 ``DET_IN0..63`` pads + one
     ``DET_COMMON`` die pad. The ``DET_COMMON`` pad is NETTED ``DET_REF`` (the
     1.65 V TIA virtual ground) by design -- the ITO back-electrode common is
     tied to the TIA reference for zero-bias short-circuit detector operation
     (gen_qssc_kicad.py: "DET_COMMON -> DET_REF"). No ``emitter_cat`` may leak
     onto the detector face.
  3. **FPGA U1 (XC7A35T-1CSG324)**: every power ball (VCCINT/VCCBRAM/VCCAUX/
     VCCADC/VCCBATT/CFGBVS/VCCO_*/GND/GNDADC/VREFN) sits on the rail the AMD/Xilinx
     ds181 Artix-7 data sheet requires (VCCINT=+1V0, VCCAUX/VCCADC=+1V8,
     VCCBATT/CFGBVS/VCCO=+3V3, GND=GND). Catches a regenerated FPGA left with a
     power ball on the wrong rail or no-net.

The FPGA CSG324 power ballout is embedded (from ds181) so the test has no
external file dependency. Pure-Python S-expr parse of the committed
``.kicad_pcb`` -- runs in the existing pytest CI job.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MAIN = REPO / "hardware" / "pcb" / "qssc.kicad_pcb"
HEAD = REPO / "hardware" / "pcb" / "chip_module.kicad_pcb"


# -- minimal KiCad S-expr parser (no pcbnew) -----------------------------------
_TOK = re.compile(r'\s*(\(|\)|"[^"]*"|[^\s()]+)')


def _parse(s):
    toks = _TOK.findall(s); pos = 0

    def pl():
        nonlocal pos
        pos += 1; items = []
        while toks[pos] != ")":
            if toks[pos] == "(":
                items.append(pl())
            else:
                t = toks[pos]
                items.append(t[1:-1] if t.startswith('"') else t)
                pos += 1
        pos += 1; return items
    return pl()


def _find_all(node, head, out):
    if isinstance(node, list):
        if node and node[0] == head:
            out.append(node)
        for c in node:
            _find_all(c, head, out)


def _prop(fp, name):
    for c in fp:
        if isinstance(c, list) and c and c[0] == "property" and len(c) > 2 and c[1] == name:
            return c[2]
    return None


def _pad_nets(board_path: Path, ref: str) -> dict[str, str]:
    """{pad_number: netname} for one footprint (pad numbers are the die ball /
    pin labels, e.g. 'DET_COMMON', 'F8', '1')."""
    tree = _parse(board_path.read_text()); fps = []; _find_all(tree, "footprint", fps)
    fp = next((f for f in fps if _prop(f, "Reference") == ref), None)
    if fp is None:
        return {}
    out = {}
    for c in fp:
        if isinstance(c, list) and c and c[0] == "pad":
            net = ""
            for sc in c:
                if isinstance(sc, list) and sc and sc[0] == "net" and len(sc) > 2:
                    net = sc[2]; break
            out[c[1]] = net
    return out


# -- Uchip die-socket face audits ---------------------------------------------
def _index_set(nets: dict[str, str], prefix: str) -> set[int]:
    idx = set()
    for nm in nets.values():
        m = re.match(rf"{re.escape(prefix)}(\d+)$", nm)
        if m:
            idx.add(int(m.group(1)))
    return idx


def test_uchip_main_emitter_face():
    """Main Uchip = 64 emitter_cat0..63 + EM_ANODE on +12V_EMIT; no DET_IN."""
    if not MAIN.exists():
        pytest.skip(f"{MAIN} not present")
    nets = _pad_nets(MAIN, "Uchip")
    assert nets, "Uchip not found on main board"
    em = _index_set(nets, "emitter_cat")
    assert em == set(range(64)), (
        f"main Uchip emitter_cat indices = {sorted(em)} (want 0..63)")
    det = _index_set(nets, "DET_IN")
    assert not det, f"main Uchip must carry NO DET_IN (emitter face): {sorted(det)}"
    # the one non-emitter netted pad is the EM_ANODE common on +12V_EMIT
    other = sorted({n for n in nets.values()
                    if n and not n.startswith("emitter_cat")})
    assert other == ["+12V_EMIT"], (
        f"main Uchip non-emitter net should be only +12V_EMIT (EM_ANODE): {other}")


def test_uchip_head_detector_face():
    """Head Uchip = 64 DET_IN0..63 + DET_COMMON pad on DET_REF; no emitter_cat.

    DET_COMMON (the die's ITO back-electrode) is tied to DET_REF, the 1.65 V TIA
    virtual ground, for zero-bias short-circuit detector operation. So the
    DET_COMMON die-pad's NET is DET_REF -- this is the intended design, not a
    missing common."""
    if not HEAD.exists():
        pytest.skip(f"{HEAD} not present")
    nets = _pad_nets(HEAD, "Uchip")
    assert nets, "Uchip not found on head board"
    det = _index_set(nets, "DET_IN")
    assert det == set(range(64)), (
        f"head Uchip DET_IN indices = {sorted(det)} (want 0..63)")
    em = _index_set(nets, "emitter_cat")
    assert not em, f"head Uchip must carry NO emitter_cat (detector face): {sorted(em)}"
    # the DET_COMMON die pad must exist and be netted DET_REF
    assert "DET_COMMON" in nets, (
        "head Uchip must have a DET_COMMON die pad (the ITO common electrode)")
    assert nets["DET_COMMON"] == "DET_REF", (
        f"head Uchip DET_COMMON -> {nets['DET_COMMON']!r} (want DET_REF, the "
        f"1.65 V TIA virtual ground for zero-bias detector operation)")


# -- FPGA U1 power-ball audit (Artix-7 ds181 CSG324 ballout) -------------------
# Ball -> expected rail, per AMD/Xilinx ds181 (Artix-7 Data Sheet). Embedded
# so the test is self-contained (no .tmpwork map dependency) for CI.
EXPECTED_RAIL = {
    "CFGBVS": "+3V3",
    "GND": "GND",
    "GNDADC": "GND",
    "VCCADC": "+1V8",
    "VCCAUX": "+1V8",
    "VCCBATT": "+3V3",
    "VCCBRAM": "+1V0",
    "VCCINT": "+1V0",
    "VCCO_0": "+3V3",
    "VCCO_14": "+3V3",
    "VCCO_15": "+3V3",
    "VCCO_16": "+3V3",
    "VCCO_34": "+3V3",
    "VCCO_35": "+3V3",
    "VREFN": "GND",
}
POWER_BALLS = {
    "CFGBVS": ["P8"],
    "GND": ["A12", "A2", "B15", "B5", "C18", "C8", "D1", "D11", "E14", "E4",
            "F11", "F17", "F7", "F9", "G10", "G12", "G8", "H11", "H13", "H3",
            "H7", "J12", "J16", "J6", "J8", "K11", "K7", "L12", "L2", "L8",
            "M11", "M15", "M5", "M7", "M9", "N10", "N12", "N18", "N8", "P1",
            "R14", "R4", "T17", "T7", "U10", "V13", "V3"],
    "GNDADC": ["H9"],
    "VCCADC": ["H10"],
    "VCCAUX": ["F12", "H12", "K12", "M12"],
    "VCCBATT": ["E8"],
    "VCCBRAM": ["F10", "G11"],
    "VCCINT": ["F8", "G7", "G9", "H8", "J11", "J7", "K8", "L11", "L7", "M10",
              "M8", "N11", "N7", "N9"],
    "VCCO_0": ["R9"],
    "VCCO_14": ["L17", "N13", "P16", "T12", "U15", "V18"],
    "VCCO_15": ["A17", "C13", "D16", "G15", "H18", "K14"],
    "VCCO_16": ["B10"],
    "VCCO_34": ["K4", "N3", "P6", "T2", "U5", "V8"],
    "VCCO_35": ["A7", "C3", "D6", "F2", "G5", "J1"],
    "VREFN": ["J9"],
}


def test_fpga_u1_power_balls_on_correct_rail():
    """Every power ball of U1 (XC7A35T-1CSG324) is on the rail ds181 requires."""
    if not MAIN.exists():
        pytest.skip(f"{MAIN} not present")
    nets = _pad_nets(MAIN, "U1")
    assert nets, "U1 (FPGA) not found on main board"
    wrong = []
    missing = []
    for grp, balls in POWER_BALLS.items():
        want = EXPECTED_RAIL[grp]
        for b in balls:
            got = nets.get(b)
            if got is None or got == "":
                missing.append((grp, b))
            elif got != want:
                wrong.append((grp, b, got, want))
    assert not missing, (
        f"FPGA U1: {len(missing)} power ball(s) with no pad / no net: "
        f"{missing[:20]}")
    assert not wrong, (
        f"FPGA U1: {len(wrong)} power ball(s) on the WRONG rail: "
        f"{wrong[:20]} (group, ball, got, want)")