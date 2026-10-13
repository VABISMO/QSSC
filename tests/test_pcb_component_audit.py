# tests/test_pcb_component_audit.py
"""Pure-Python PCB component-usage + BOM + netlist audit for all three QSSC
boards (main, head, gradient variant).

This is the **CI-runnable** audit (no ``pcbnew``, no ``kicad-cli``): it parses
the committed ``.kicad_pcb`` files as S-expressions and answers: *"all
resistors used for something, all capacitors used, all voltages correct, all
components present"*:

  1. **Every passive has a job.** Every R/C/L/D/Q has >=2 distinct, non-empty
     nets on its pads. A resistor with an empty pad is floating (doing
     nothing); a cap with both pads on the same net is a shorted placement
     (doing nothing). Either is exactly the "component that is not used for
     anything" defect.
  2. **No dangling nets.** No non-plane net has exactly 1 pad across the whole
     board (a named net nothing else connects to -- a stub to nowhere).
  3. **Every IC is powered.** Every true IC / crystal (U*/Y*) has >=1 pad on a
     power rail and >=1 pad on GND -- the sanity-level "all voltages correct"
     bar. The deep per-datasheet per-pin check (ADF4351 VTUNE on LF_OUT, AD7616
     VDRIVE on +3V3, etc.) is ``test_pcb_pin_audit.py`` (pcbnew, local-only);
     this test is the CI safety-net that no IC was left without supply.
  4. **BOM matches the board.** ``hardware/datasheets/BOM.csv`` designators
     match the placed footprints in both directions: every BOM line is placed
     on its board, every placed part is in the BOM (with a justified allowlist
     for mechanical / mounting / no-BOM parts).

The BOM gate uses two directions with different row scopes. *Row validity* is
strict per board: a row labeled ``main`` must be placed on the main board (and
likewise for ``head``/``grad``). *Coverage* is a union check: the grad section
is a hand-maintained DELTA over the main rows (``tools/gen_bom.py`` generates
only main+head), so a placed part counts as covered when it appears in ANY
board's rows -- that is the shape that catches an added part with no BOM row
at all (as Rgrad_feed once was) without flagging the whole main-derived clone
on the gradient board.

Pure-Python so it runs in the existing ``pytest`` CI job (CI has no KiCad).
The geometry-level connectivity gate (copper graph, split nets) is
``test_pcb_connectivity.py``; the ``kicad-cli`` DRC gate is
``test_pcb_drc.py`` (both need KiCad, run locally + in a dedicated KiCad CI
job).
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PCB_DIR = REPO / "hardware" / "pcb"
BOARDS = {
    "main": PCB_DIR / "qssc.kicad_pcb",
    "head": PCB_DIR / "chip_module.kicad_pcb",
    "grad": PCB_DIR / "qssc-grad.kicad_pcb",
}
BOM_CSV = REPO / "hardware" / "datasheets" / "BOM.csv"

# Plane / rail nets: tied by poured zones, so a single-pad member is still
# connected through the pour. Excluded from the 1-pad dangling check (kept in
# sync with test_pcb_connectivity.PLANE_NETS).
PLANE_NETS = {
    "GND", "+3V3", "+1V8", "+1V0", "+5V", "+5V_A", "+5V_RF", "+12V_EMIT",
    "VBUS_USB", "+5V_IN",
}
POWER_NETS = {n for n in PLANE_NETS if n != "GND"}

# Passive reference prefixes that MUST connect two distinct nodes. A passive
# is "R"/"C"/"L"/"D"/"Q" followed by digits -- this deliberately EXCLUDES "REG*"
# (voltage regulators like REG3 = AP2127K), which are ICs that legitimately
# carry a No-Connect / bypass pin (pin 4 on the AP2127K) and so can have an
# empty pad without being a "floating passive".
PASSIVE_RE = re.compile(r"^(R|C|L|D|Q)\d")
# ICs / crystals that must be powered (connectors J* are handled separately --
# a signal-only header legitimately has no power pin).
IC_PREFIXES = ("U", "Y")

# ICs that legitimately have NO power-rail pad and so are exempt from the
# "every IC has a power pin" sanity net:
#   * ``Uchip`` -- the 2.54 mm female-header chip SOCKET is a passive carrier
#     (64 EM/DET signal pads + a rail pad), not a powered IC.
#   * SOT-89 (3-pin) RF blocks like the GVA-84+ MMIC have no VCC pin -- DC bias
#     is delivered through the RF output line via a series inductor (bias-tee;
#     here L2 carries +5V_RF -> RF_AMP_OUT -> U5). The "no power pad" is the
#     MMIC's normal biasing, not a missing supply.
NO_POWER_CHECK_REF = {"Uchip"}
NO_POWER_CHECK_FP = ("SOT-89",)

# Non-plane nets that are intentionally single-pad on a given board (a reserved
# / future-route net, verified in the per-datasheet netlist audit -- see
# memory/netlist-verified-per-datasheet). ``mod_sync`` is the FPGA's emitter/
# detector sync output, held as a single pad on the main board pending a
# future board-to-board route; not a dangling mistake.
# ``grad_en`` exists only on the gradient board: the FPGA ball is pinned
# (gen_fpga_ballmap_csg324: ball V7) as a spare STATUS output, documented NC on
# the PCB -- tools/route_grad_nets.py: "grad_en is a 1-pad FPGA STATUS output,
# NC on the PCB -- left unrouted."
RESERVED_NETS = {"mod_sync", "grad_en"}

# Parts on the board but intentionally NOT in the BOM: mechanical mounting
# holes, board fiducials, logos, and any "*FID*" / "*LOGO*" / "MH*" references.
# (Uchip IS in the BOM as the chip socket, so it is not allowlisted here.)
NO_BOM_RE = re.compile(
    r"^(MH\d+|FID\d+|.*FID.*|.*LOGO.*|.*Logo.*|Ref.*|G.*Fid.*)$",
    re.I,
)
# BOM entries that are NOT physical placed footprints: KiCad schematic-only
# special symbols (references starting with "#", e.g. "#FLG1" = PWR_FLAG power
# markers). gen_bom lists them (they sit in the generator's COMPONENTS) but
# they never become a footprint on the PCB, so exclude them both ways.
SCHEMATIC_ONLY_RE = re.compile(r"^#")


# --------------------------------------------------------------------------
# minimal KiCad S-expression parser (no pcbnew, CI-portable)
# --------------------------------------------------------------------------
_TOK = re.compile(r'\s*(\(|\)|"[^"]*"|[^\s()]+)')


def _parse(s: str):
    toks = _TOK.findall(s)
    pos = 0

    def pl():
        nonlocal pos
        pos += 1                       # consume "("
        items = []
        while toks[pos] != ")":
            if toks[pos] == "(":
                items.append(pl())
            else:
                t = toks[pos]
                items.append(t[1:-1] if t.startswith('"') else t)
                pos += 1
        pos += 1                        # consume ")"
        return items

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


def _pad_net(pd):
    for c in pd:
        if isinstance(c, list) and c and c[0] == "net" and len(c) > 2:
            return c[2]
    return ""


def _extract(path: Path):
    """Return (footprints, nets) for a .kicad_pcb file.

    footprints: list of dicts {ref, value, fpid, nets: set[str] of pad nets}
    nets: {netname: count_of_pads_on_that_net}
    """
    tree = _parse(path.read_text())
    fps = []
    _find_all(tree, "footprint", fps)
    footprints = []
    netcount: dict[str, int] = {}
    for fp in fps:
        ref = _prop(fp, "Reference") or ""
        val = _prop(fp, "Value") or ""
        fpid = fp[1] if len(fp) > 1 else ""
        nets: set[str] = set()
        for c in fp:
            if isinstance(c, list) and c and c[0] == "pad":
                nm = _pad_net(c)
                nets.add(nm)
                if nm:
                    netcount[nm] = netcount.get(nm, 0) + 1
        footprints.append({"ref": ref, "value": val, "fpid": fpid, "nets": nets})
    return footprints, netcount


# --------------------------------------------------------------------------
# 1. every passive has a job: >=2 distinct, non-empty nets
# --------------------------------------------------------------------------
def _passive_problems(footprints):
    floating = []      # one or more empty nets
    one_net = []       # exactly one distinct net (passive needs two nodes)
    for fp in footprints:
        ref = fp["ref"]
        if not ref or not PASSIVE_RE.match(ref):
            continue
        nets = fp["nets"]
        nonempty = {n for n in nets if n}
        if len(nets) != len(nonempty):           # an empty/"" pad present
            floating.append(ref)
        if nonempty and len(nonempty) == 1:
            one_net.append(ref)
    return floating, one_net


@pytest.mark.parametrize("name,path", list(BOARDS.items()))
def test_every_passive_has_two_distinct_nets(name: str, path: Path):
    """A passive doing nothing: a floating pad (empty net) or both pads on one
    net (shorted). Either is a resistor/cap placed but not actually wired."""
    if not path.exists():
        pytest.skip(f"{path} not present")
    fps, _ = _extract(path)
    floating, one_net = _passive_problems(fps)
    assert not floating, (
        f"{name}: {len(floating)} passive(s) with a floating (un-netted) pad -- "
        f"a component doing nothing: {floating[:25]}")
    assert not one_net, (
        f"{name}: {len(one_net)} passive(s) with only ONE distinct net (both "
        f"pads same net = shorted/no-op): {one_net[:25]}")


# --------------------------------------------------------------------------
# 2. no dangling 1-pad non-plane nets
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name,path", list(BOARDS.items()))
def test_no_single_pad_non_plane_nets(name: str, path: Path):
    """A non-plane net with exactly one pad is a named net that nothing else
    connects to -- a stub to nowhere, or a net the generator declared but never
    wired to a second pad. Plane nets are excluded (a single rail pad is tied
    through the poured zone)."""
    if not path.exists():
        pytest.skip(f"{path} not present")
    _, netcount = _extract(path)
    dangling = sorted(n for n, c in netcount.items()
                      if c == 1 and n not in PLANE_NETS
                      and n not in RESERVED_NETS)
    assert not dangling, (
        f"{name}: {len(dangling)} non-plane net(s) with exactly 1 pad "
        f"(dangling stub / named-but-unwired): {dangling[:25]}")


# --------------------------------------------------------------------------
# 3. every IC / crystal is powered
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name,path", list(BOARDS.items()))
def test_every_ic_has_power_and_ground(name: str, path: Path):
    """Every U* IC and Y* crystal must have >=1 pad on a power rail and >=1 on
    GND. This is the CI-level 'all voltages correct' sanity net -- catches a
    regenerated IC left without supply. The per-pin datasheet check
    (test_pcb_pin_audit) is the deep version, run locally with pcbnew."""
    if not path.exists():
        pytest.skip(f"{path} not present")
    fps, _ = _extract(path)
    no_power, no_gnd = [], []
    for fp in fps:
        ref = fp["ref"]
        if not ref or not any(ref.startswith(p) for p in IC_PREFIXES):
            continue
        if ref in NO_POWER_CHECK_REF:
            continue
        if any(pat in fp["fpid"] for pat in NO_POWER_CHECK_FP):
            continue
        nets = {n for n in fp["nets"] if n}
        if not (nets & POWER_NETS):
            no_power.append(ref)
        if "GND" not in nets:
            no_gnd.append(ref)
    assert not no_power, (
        f"{name}: {len(no_power)} IC/crystal(s) with NO power-rail pad: "
        f"{no_power[:25]}")
    assert not no_gnd, (
        f"{name}: {len(no_gnd)} IC/crystal(s) with NO GND pad: {no_gnd[:25]}")


# --------------------------------------------------------------------------
# 4. BOM <-> board consistency
# --------------------------------------------------------------------------
def _bom_refs_by_board():
    """{board: set(refs)} from BOM.csv (Designators is a comma-separated list).

    Dynamically keyed on the Board column so a section added to the CSV (the
    grad rows arrived after the test was written) is audited automatically --
    a hardcoded {main, head} silently dropped every other section."""
    by_board: dict[str, set[str]] = {}
    if not BOM_CSV.exists():
        return by_board
    with BOM_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            board = (row.get("Board") or "").strip()
            if not board:
                continue
            by_board.setdefault(board, set())
            for d in (row.get("Designators") or "").split(","):
                d = d.strip()
                if d:
                    by_board[board].add(d)
    return by_board


@pytest.mark.parametrize("name,path", list(BOARDS.items()))
def test_bom_matches_board(name: str, path: Path):
    """Every BOM designator is placed on its board, and every placed part is in
    the BOM (except mechanical / mounting / logo / Uchip, which are allowlisted
    as intentionally no-BOM).

    Row scope differs by direction: rows labeled for THIS board must be placed
    on it (strict, per-board); placed parts must appear in the BOM under any
    board (union -- the grad rows are a hand-maintained delta over main, see
    the module docstring)."""
    if not path.exists() or not BOM_CSV.exists():
        pytest.skip("board or BOM not present")
    fps, _ = _extract(path)
    placed = {fp["ref"] for fp in fps if fp["ref"]}
    by_board = _bom_refs_by_board()
    bom_own = {r for r in by_board.get(name, set())
               if not SCHEMATIC_ONLY_RE.match(r)}
    bom_union = {r for refs in by_board.values() for r in refs
                 if not SCHEMATIC_ONLY_RE.match(r)}

    in_bom_not_placed = sorted(bom_own - placed)
    placed_not_in_bom = sorted(
        r for r in (placed - bom_union)
        if not NO_BOM_RE.match(r) and not SCHEMATIC_ONLY_RE.match(r)
    )
    assert not in_bom_not_placed, (
        f"{name}: {len(in_bom_not_placed)} BOM designator(s) NOT placed on the "
        f"board: {in_bom_not_placed[:25]}")
    assert not placed_not_in_bom, (
        f"{name}: {len(placed_not_in_bom)} placed part(s) with NO BOM row under "
        f"any board section (and not in the mechanical allowlist): "
        f"{placed_not_in_bom[:25]}")