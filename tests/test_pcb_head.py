# tests/test_pcb_head.py
"""Structural + DRC checks for the **detector head** board
(``hardware/pcb/chip_module.kicad_pcb``), the top half of the two-PCB sandwich.

The QSSC die is a two-faced (no-TSV) die clamped between TWO PCBs. This file
verifies the **head** board -- the 6-layer board that carries the detector
analog front-end and contacts the die TOP (detector) face on B.Cu via ACF.
Two extra inner signal layers (In3.Cu/In4.Cu) carry the 128-net die bus that a
4-layer stackup could not escape (commit b41009c):

    F.Cu   signal + components (16x TLV9004 TIA + 4x AD7616 ADC + 64x Rf/Cf + J12)
    In1.Cu GND plane
    In2.Cu PWR plane (+3V3 / +5V_A)
    In3.Cu die-bus signal (escape routing for the 64 DET + 64 EM nets)
    In4.Cu die-bus signal
    B.Cu   65 DET die pads (64 electrodes + DET_COMMON) + analog routing

The 9-wire AD7616 SPI + GND/+3V3/+5V cross J12 (BTB_DAUGHTER) to the main board.

All checks parse the committed ``.kicad_pcb`` / ``.kicad_pro`` sexp/JSON -- no
regeneration, no re-route, no pcbnew for the structural checks (so they run in
CI). The DRC check runs the real ``kicad-cli pcb drc --refill-zones`` (refill:
the stored fill must never decide the verdict -- see test_pcb_drc.py). The
main-board checks live in ``test_pcb_stackup.py``,
``test_pcb_drc.py``, ``test_pcb_route.py``; this file is the head-board mirror.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PCB = REPO / "hardware" / "pcb" / "chip_module.kicad_pcb"
PRO = REPO / "hardware" / "pcb" / "chip_module.kicad_pro"


# ---------------------------------------------------------------------------
# sexp parsing helpers (no pcbnew -- runs in CI)
# ---------------------------------------------------------------------------

def _layers(path: Path) -> list[tuple[str, str, str]]:
    """[(canonical_name, user_label, type)] for every declared layer in board
    order, parsing the ``(layers ...)`` block. Mirrors test_pcb_stackup._layers."""
    lines = path.read_text().splitlines()
    out: list[tuple[str, str, str]] = []
    in_block = False
    for ln in lines:
        s = ln.strip()
        if s == "(layers":
            in_block = True
            continue
        if not in_block:
            continue
        if s == ")":
            break
        m = re.match(r'\(\d+\s+"([^"]+)"\s+(\w+)(?:\s+"([^"]*)")?\)', s)
        if m:
            canon, typ, user = m.group(1), m.group(2), m.group(3) or ""
            out.append((canon, user, typ))
    assert out, "no layer declarations parsed from (layers ...) block"
    return out


def _copper_layers(path: Path) -> list[tuple[str, str, str]]:
    return [l for l in _layers(path) if l[2] in ("signal", "power")]


def _footprints(path: Path) -> list[tuple[str, str, str]]:
    """[(footprint_lib_id, reference, value)] for every placed footprint.

    Parses top-level ``(footprint "lib:Name" ... (property "Reference" "U1")
    (property "Value" "AD7616"))`` blocks. KiCad 8 writes the reference/value
    as ``property`` forms inside the footprint; we read those by name so we do
    not depend on the legacy positional ``(pad ...)`` order.
    """
    t = path.read_text()
    out: list[tuple[str, str, str]] = []
    # Match each footprint block opener + capture its lib:id, then scan forward
    # for the Reference and Value properties of THAT block. Footprint blocks are
    # indented with a single tab at top level in the KiCad 8 sexp layout.
    for m in re.finditer(r'(?m)^\t\(footprint "([^"]+)"', t):
        lib_id = m.group(1)
        start = m.end()
        # Find the next top-level footprint (or end of the board body) to bound
        # the property search to THIS footprint only.
        nxt = re.search(r'(?m)^\t\(footprint "', t[start:])
        end = start + nxt.start() if nxt else len(t)
        block = t[start:end]
        ref = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', block)
        val = re.search(r'\(property\s+"Value"\s+"([^"]+)"', block)
        ref_s = ref.group(1) if ref else ""
        val_s = val.group(1) if val else ""
        out.append((lib_id, ref_s, val_s))
    return out


def _has_kicad_cli() -> bool:
    # Kept for the local convenience check in the module docstring; the tests
    # themselves no longer skip -- a missing kicad-cli FAILS the DRC test.
    return shutil.which("kicad-cli") is not None


# ---------------------------------------------------------------------------
# stackup
# ---------------------------------------------------------------------------

def test_head_six_layer_stackup():
    """The head board is 6-layer: F.Cu + In1.Cu(GND) + In2.Cu(PWR) + In3.Cu +
    In4.Cu (two extra signal layers for the 128-net die bus, commit b41009c) +
    B.Cu. A 4-layer stackup could not escape the 64 DET + 64 EM die nets."""
    layers = _copper_layers(PCB)
    names = [l[0] for l in layers]
    assert names == ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"], (
        f"expected 6-layer head stackup, got {names}"
    )
    labels = {l[0]: l[1] for l in layers}
    assert labels["In1.Cu"] == "GND", labels
    assert labels["In2.Cu"] == "PWR", labels
    assert labels["F.Cu"] == "", labels
    assert labels["B.Cu"] == "", labels


# ---------------------------------------------------------------------------
# footprint audit -- the two-board split is correct on the head
# ---------------------------------------------------------------------------

def test_head_footprint_audit():
    fps = _footprints(PCB)
    by_value = [v for _, _, v in fps]
    by_ref = {r: v for _, r, v in fps if r}

    # --- the detector-face die footprint is on the HEAD, the emitter-face one
    # is NOT (it lives on the main board). ---
    assert "QSSC_Chip_DET" in by_value, "head must carry QSSC_Chip_DET (die top)"
    assert "QSSC_Chip_EM" not in by_value, "head must NOT carry the emitter face"

    # --- 4x AD7616 ADC (the parallel readout, replaces 16x LMV339). ---
    ad7616 = [v for v in by_value if v == "AD7616"]
    assert len(ad7616) == 4, f"expected 4x AD7616, got {len(ad7616)}"

    # --- 16x TLV9004 TIA (4 ch each -> 64 channels). ---
    tlv = [v for v in by_value if v == "TLV9004"]
    assert len(tlv) == 16, f"expected 16x TLV9004 TIA, got {len(tlv)}"

    # --- NO FPGA on the head (the FPGA is on the main board). ---
    assert not any("XC7A35T" in v for v in by_value), \
        "head board must NOT carry the FPGA"
    assert "U1" not in by_ref or "XC7A35T" not in by_ref.get("U1", ""), \
        "head board U1 must not be the FPGA"

    # --- NO stale comparator / threshold DAC parts. ---
    assert "LMV339" not in by_value, "stale LMV339 comparator left on head"
    assert "MCP4725" not in by_value, "stale MCP4725 threshold DAC left on head"

    # --- the BTB DAUGHTER connector J12 is present. ---
    btb = [v for v in by_value if v.startswith("BTB_DAUGHTER")]
    assert len(btb) == 1, f"expected 1x BTB_DAUGHTER connector, got {len(btb)}"


def test_head_j12_is_2x10():
    """J12 is a 2x10 (20-pin) board-to-board connector carrying the 9-wire
    ADC SPI + GND/+3V3/+5V. We assert it has 20 pins in the footprint sexp."""
    t = PCB.read_text()
    # Find the footprint block whose Value is BTB_DAUGHTER, then count its pads.
    fm = re.search(r'(?m)^\t\(footprint "[^"]*Conn_02x10_BTB"', t)
    assert fm, "Conn_02x10_BTB footprint not found on head"
    start = fm.start()
    nxt = re.search(r'(?m)^\t\(footprint "', t[fm.end():])
    end = fm.end() + nxt.start() if nxt else len(t)
    block = t[start:end]
    pads = re.findall(r'(?m)^\s+\(pad\b', block)
    assert len(pads) == 20, f"J12 BTB must have 20 pins, got {len(pads)}"


# ---------------------------------------------------------------------------
# die-contact layer -- the 64 DET pads are on B.Cu (head sits above the die,
# B.Cu faces down to the die top face), with NO B.Paste (ACF contact, not solder)
# ---------------------------------------------------------------------------

def test_head_det_pads_on_bcu():
    """QSSC_Chip_DET's 65 detector pads must be on B.Cu (die top face):
    64 DET electrodes + 1 DET_COMMON (the ITO common electrode, netted to
    DET_REF since commit ea2fa2a to close the detector common electrode)."""
    t = PCB.read_text()
    fm = re.search(r'(?m)^\t\(footprint "[^"]*QSSC_Chip_DET"', t)
    assert fm, "QSSC_Chip_DET footprint not found"
    start = fm.start()
    nxt = re.search(r'(?m)^\t\(footprint "', t[fm.end():])
    end = fm.end() + nxt.start() if nxt else len(t)
    block = t[start:end]
    # Every pad in this footprint carries its layer set, e.g.
    # (pad "1" smd ... (layers "B.Cu" "B.Mask")). Count pads on B.Cu.
    pad_blocks = re.findall(r'\(pad "[^"]+" smd.*?\(layers "([^"]+)"', block, re.DOTALL)
    assert pad_blocks, "no smd pads parsed in QSSC_Chip_DET"
    bcu = [p for p in pad_blocks if p == "B.Cu"]
    # 64 detector electrodes + DET_COMMON (the anode lives on the EM face /
    # main board; DET_COMMON is the shared ITO common electrode on the head).
    assert len(bcu) == 65, (
        f"expected 65 DET pads on B.Cu (64 electrodes + DET_COMMON), "
        f"got {len(bcu)} (layers: {pad_blocks[:5]}...)"
    )
    # ACF contact interface, not a solder joint -> NO B.Paste on the DET pads.
    assert '"B.Paste"' not in block, "DET pads must have NO B.Paste (ACF contact)"


# ---------------------------------------------------------------------------
# DRC -- the committed head board is DRC-CLEAN (0 violations), and this test
# is the RATCHET that keeps it that way: any new or regressed violation fails
# the build. Documented deviations go into KNOWN_VIOLATIONS (type, net
# signature) -- today the set is EMPTY; to add one, fix the board first or
# prove it is locally unfixable and document the constraint analysis here.
# ---------------------------------------------------------------------------

KNOWN_VIOLATIONS = set()


def test_head_drc_zero_violations(tmp_path):
    dst_pcb = tmp_path / "chip_module.kicad_pcb"
    dst_pro = tmp_path / "chip_module.kicad_pro"
    shutil.copy(PCB, dst_pcb)
    shutil.copy(PRO, dst_pro)

    out = tmp_path / "drc.json"
    res = subprocess.run(
        ["kicad-cli", "pcb", "drc", "--refill-zones", "--format", "json",
         "--output", str(out), str(dst_pcb)],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode == 0, f"kicad-cli pcb drc failed:\n{res.stderr}"

    data = json.loads(out.read_text())
    violations = data.get("violations", [])

    def signature(v):
        # Net names are bracketed in the ITEM descriptions ("Leiterbahn
        # [DET_IN0] auf In3.Cu"); the violation description itself writes them
        # unbracketed ("Netze DET_IN0 und DET_IN8") -- scan both.
        text = v.get("description", "") + "".join(
            it.get("description", "") for it in v.get("items", []))
        nets = set(re.findall(r"\[([A-Za-z0-9_+/.-]+)\]", text))
        return (str(v.get("type", "")), tuple(sorted(nets)))

    found = {signature(v) for v in violations}
    unknown = found - KNOWN_VIOLATIONS
    stale = KNOWN_VIOLATIONS - found
    assert not unknown, (
        f"{len(violations)} head DRC violation(s), {len(unknown)} not in the "
        f"allowlist (fix the board or document it in KNOWN_VIOLATIONS):\n"
        + json.dumps([v for v in violations
                      if signature(v) in unknown][:10], indent=2)
    )
    assert not stale, (
        f"stale allowlist entries -- the head board no longer exhibits "
        f"{stale}: remove them from KNOWN_VIOLATIONS"
    )