# tests/test_generator_freshness.py
"""The committed KiCad project files must be exactly what the generator emits.

``tools.kicad.pcb.emit_pro()`` starts from the shipped ``qssc.kicad_pro`` as
its template and then applies the generator's severities / netclasses / rules
/ ERC block -- so any hand fix to a committed ``.kicad_pro`` that never
reaches the emitter is silently reverted by the next regeneration. That is
the issue #168-B class (artifacts hand-repaired, generator never taught ->
regression on regen), found in round 7 with exactly two live instances:

  * ``unconnected_items`` -- both shipped boards carry the real-DRC-gate
    ``error``, the emitter still forced ``ignore``;
  * ``meta.filename`` -- the head emit stamped the template's
    ``qssc.kicad_pro`` into ``chip_module.kicad_pro``.

Pure JSON + module import (no pcbnew / kicad-cli), so this gate runs in CI
as well as locally. The schematic/board reproducibility axes (netlist and
placement equality against a fresh emit) were verified by hand in round 6
and are intentionally not re-run here -- that needs a full subprocess
generator run.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

CASES = [
    pytest.param("qssc", REPO / "hardware/pcb/qssc.kicad_pro", id="main"),
    pytest.param("chip_module", REPO / "hardware/pcb/chip_module.kicad_pro",
                 id="head"),
]

_NETCLASS_KEYS = ("clearance", "track_width", "via_diameter", "via_drill",
                  "uvia_diameter", "uvia_drill")


def _emit(stem: str) -> dict:
    # emit_pro builds its netclass patterns from the component/net model
    # (all_nets()), so populate it exactly like tools/kicad/__main__ does:
    # the published main is emitted from the extended (gradient) build, the
    # head from its own builder. (The gradient nets classify as Default, so
    # main yields the same 88 patterns as the base build -- the assertion
    # against the shipped file is what pins that.)
    from tools.kicad import placement as _g
    from tools.kicad.pcb import emit_pro, MAIN_CFG, HEAD_CFG

    _g.COMPONENTS.clear()
    if stem == "qssc":
        _g.build_gradient_board()
    else:
        _g.build_detector_head()
    cfg = dict(MAIN_CFG if stem == "qssc" else HEAD_CFG)
    return json.loads(emit_pro(cfg))


def _classes(proj: dict) -> dict:
    return {c["name"]: {k: c.get(k) for k in _NETCLASS_KEYS}
            for c in proj["net_settings"]["classes"]}


def _patterns(proj: dict) -> set:
    return {tuple(sorted(p.items()))
            for p in proj["net_settings"]["netclass_patterns"]}


def _sev_diff(a: dict, b: dict) -> dict:
    return {k: (a.get(k), b.get(k))
            for k in set(a) | set(b) if a.get(k) != b.get(k)}


@pytest.mark.parametrize("stem,path", CASES)
def test_committed_pro_matches_generator(stem, path):
    fresh = _emit(stem)
    shipped = json.loads(path.read_text(encoding="utf-8"))
    rel = path.relative_to(REPO).as_posix()

    fs = fresh["board"]["design_settings"]
    ss = shipped["board"]["design_settings"]
    assert fs.get("rule_severities") == ss.get("rule_severities"), (
        f"{rel}: rule_severities drifted -- fresh vs shipped: "
        f"{_sev_diff(fs.get('rule_severities', {}), ss.get('rule_severities', {}))}")
    assert fs.get("rules") == ss.get("rules"), (
        f"{rel}: design rules drifted -- "
        f"{_sev_diff(fs.get('rules', {}), ss.get('rules', {}))}")
    assert _classes(fresh) == _classes(shipped), (
        f"{rel}: netclasses drifted -- fresh={_classes(fresh)} "
        f"shipped={_classes(shipped)}")
    assert _patterns(fresh) == _patterns(shipped), (
        f"{rel}: netclass patterns drifted -- "
        f"fresh-only={sorted(_patterns(fresh) - _patterns(shipped))[:5]}, "
        f"shipped-only={sorted(_patterns(shipped) - _patterns(fresh))[:5]}")
    assert fresh.get("erc") == shipped.get("erc"), (
        f"{rel}: erc settings drifted -- "
        f"{_sev_diff(fresh.get('erc', {}).get('rule_severities', {}), shipped.get('erc', {}).get('rule_severities', {}))}")
    assert fresh.get("meta") == shipped.get("meta"), (
        f"{rel}: meta drifted -- fresh={fresh.get('meta')} "
        f"shipped={shipped.get('meta')}")


def test_committed_xdc_matches_generator():
    """fpga/constraints.xdc must be byte-identical to tools.gen_xdc.emit().

    The audit-#109 timing section (false paths for the asynchronous inputs
    rx/adc_busy/spi_pins[3], the refclk_25m generated clock, and the gated-
    strobe notes -- 32 lines) was hand-added to the committed XDC and never
    taught to the emitter: one regeneration silently deleted all of it
    (round-8 audit), which would drop real Vivado timing constraints. The
    gradient-block header comment had drifted the same way.
    """
    from tools.gen_xdc import emit

    committed = (REPO / "fpga" / "constraints.xdc").read_text(encoding="utf-8")
    fresh = emit()
    assert fresh == committed, (
        "fpga/constraints.xdc no longer equals its generator output; "
        f"first difference at line "
        f"{next((i + 1 for i, (a, b) in enumerate(zip(fresh.splitlines(), committed.splitlines())) if a != b), 'length/EOF')}")


def _symbol_pinmaps(text: str) -> dict[str, dict[str, str]]:
    """{symbol_name: {pin_number: pin_name}} for TOP-LEVEL symbols of a
    KiCad symbol file (works for both qssc-lib.kicad_sym and the schematic's
    embedded lib_symbols cache).

    Top-level blocks are the ``(symbol "X" ...)`` openers whose name does NOT
    end in ``_<digits>_<digits>`` (those are unit/style sub-symbols, whose
    pins belong to the enclosing symbol); each block runs to the next
    top-level opener. Symbol *instances* in a schematic use
    ``(symbol (lib_id ...)`` (no quoted name) and never match.
    """
    openers = [(m.start(), m.group(1)) for m in re.finditer(r'\(symbol "([^"]+)"', text)
               if not re.search(r"_\d+_\d+$", m.group(1))]
    out: dict[str, dict[str, str]] = {}
    for i, (start, name) in enumerate(openers):
        end = openers[i + 1][0] if i + 1 < len(openers) else len(text)
        block = text[start:end]
        pins = {m.group(2): m.group(1) for m in re.finditer(
            r'\(\s*name\s+"([^"]*)"[\s\S]{0,400}?\(number\s+"(\d+)"', block)}
        out[name] = pins
    return out


def test_library_symbols_match_schematic_cache():
    """qssc-lib.kicad_sym must agree PIN-FOR-PIN with the lib_symbols caches
    embedded in BOTH committed sheets (issue #168-B generalized).

    The #168-B regression was exactly this class: the library said
    VCC/GND while the sheet cache said V-/V+ (and vice versa after a
    one-sided fix), so "Update Symbols from Library" or the next
    regeneration reverted whichever side was hand-repaired. The existing
    gates only cover LM358 by name; this compares EVERY embedded symbol
    on every committed sheet.
    """
    lib = _symbol_pinmaps(
        (REPO / "hardware/pcb/qssc-lib.kicad_sym").read_text(encoding="utf-8"))
    for sch_name in ("qssc.kicad_sch", "chip_module.kicad_sch"):
        sch = _symbol_pinmaps(
            (REPO / "hardware" / "pcb" / sch_name).read_text(encoding="utf-8"))
        assert sch, f"{sch_name}: no symbols parsed -- parser broken?"

        missing = sorted(n for n in sch if n not in lib)
        assert not missing, (
            f"{sch_name}: symbols embedded but absent from qssc-lib.kicad_sym: "
            f"{missing}")

        drifted = sorted(n for n in sch if lib.get(n) != sch[n])
        detail = []
        for n in drifted[:4]:
            only_lib = {k: v for k, v in lib[n].items() if sch[n].get(k) != v}
            only_sch = {k: v for k, v in sch[n].items() if lib[n].get(k) != v}
            detail.append(f"{n}: lib-only/differing={only_lib} sheet-only/differing={only_sch}")
        assert not drifted, (
            f"{sch_name}: pin maps differ between qssc-lib.kicad_sym and the "
            f"sheet cache for {len(drifted)} symbol(s): {detail}")


def _copy_gen_inputs(tmp: Path, with_pro: bool = True) -> None:
    """Copy the minimal generator inputs (tools/ + the two pinned .tmpwork
    snapshots) into tmp. ``with_pro`` also copies the shipped main project
    (only the board generator's emit_pro needs it as its template)."""
    import shutil

    (tmp / ".tmpwork").mkdir(parents=True, exist_ok=True)
    (tmp / "hardware" / "pcb").mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO / "tools", tmp / "tools",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    rels = [".tmpwork/pins.json", ".tmpwork/fpga_csg324_map.json"]
    if with_pro:
        rels.append("hardware/pcb/qssc.kicad_pro")
    for rel in rels:
        shutil.copy2(REPO / rel, tmp / rel)


def _run_generator(tmp: Path) -> None:
    """Copy the minimal generator inputs into tmp and run the REAL generator
    there (round-9 capstone).

    ROOT inside tools derives from the script's own location, so everything
    the generator writes lands inside the copy -- the working tree is never
    touched. Inputs: tools/, the two pinned .tmpwork snapshots (pins.json +
    fpga_csg324_map.json, the fresh-clone contract of issue #166), and the
    shipped main project (emit_pro uses it as its template). The system
    footprint library is READ (not copied), hence the skip guard below.
    """
    import os
    import subprocess
    import sys

    _copy_gen_inputs(tmp, with_pro=True)

    res = subprocess.run(
        [sys.executable, str(tmp / "tools" / "gen_qssc_kicad.py")],
        cwd=tmp, capture_output=True, text=True, timeout=600,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert res.returncode == 0, (
        f"the generator failed on a clean copy of the committed inputs "
        f"(fresh-clone contract):\nstdout tail: {res.stdout[-1500:]}\n"
        f"stderr tail: {res.stderr[-1500:]}")


def _sch_surface(path: Path):
    """(sorted instance tuples, sorted label tuples) of a schematic.

    Instances: (ref, lib_id, x, y, rot, value, footprint) -- the window after
    each (lib_id ...) starts with the INSTANCE's own (at ...), all properties
    precede the per-pin uuid list. Labels: (name, x, y) multiset. Coordinates
    compare as floats so formatting cannot cause false drift.
    """
    txt = path.read_text(encoding="utf-8")
    inst = []
    for m in re.finditer(r'\(lib_id "([^"]+)"\)', txt):
        win = txt[m.end():m.end() + 4000]
        at = re.search(r'\(at ([\d.eE+-]+) ([\d.eE+-]+)(?: ([\d.eE+-]+))?\)', win)
        ref = re.search(r'\(property "Reference" "([^"]*)"', win)
        if not (at and ref):
            continue
        val = re.search(r'\(property "Value" "([^"]*)"', win)
        fpp = re.search(r'\(property "Footprint" "([^"]*)"', win)
        inst.append((ref.group(1), m.group(1),
                     round(float(at.group(1)), 4), round(float(at.group(2)), 4),
                     round(float(at.group(3) or 0) % 360, 3),
                     val.group(1) if val else "", fpp.group(1) if fpp else ""))
    labels = sorted(
        (n, round(float(x), 4), round(float(y), 4), round(float(r or 0) % 360, 3))
        for n, x, y, r in re.findall(
            r'\(label "([^"]+)"\s*\(at ([\d.eE+-]+) ([\d.eE+-]+)(?: ([\d.eE+-]+))?\)',
            txt))
    ncs = sorted((round(float(x), 4), round(float(y), 4))
                 for x, y in re.findall(r'\(no_connect\s*\(at ([\d.eE+-]+) ([\d.eE+-]+)\)', txt))
    return sorted(inst), labels, ncs


def _pcb_surface(path: Path):
    """(sorted (ref, x, y, rot) tuples, sorted (ref, pad, net) tuples) of a
    board file, parsed from TEXT so the freshly emitted file (generator
    formatting) and the committed one (pcbnew round-trip) compare on content:
    footprints are sliced by line-start openers (no fixed window -- U1 spans
    ~30k chars), rotations normalised mod 360, coordinates as floats.
    """
    txt = path.read_text(encoding="utf-8")
    starts = [m.start() for m in re.finditer(r'^[ \t]*\(footprint "', txt, re.M)]
    place, padnets = [], []
    for i, s in enumerate(starts):
        block = txt[s: starts[i + 1] if i + 1 < len(starts) else len(txt)]
        lib = re.match(r'[ \t]*\(footprint "([^"]+)"', block).group(1)
        at = re.search(r'\(at ([\d.eE+-]+) ([\d.eE+-]+)(?: ([\d.eE+-]+))?\)', block)
        ref = re.search(r'\(property "Reference" "([^"]*)"', block)
        if not (at and ref):
            continue
        place.append((ref.group(1),
                      round(float(at.group(1)), 4), round(float(at.group(2)), 4),
                      round(float(at.group(3) or 0) % 360, 3), lib))
        # pads: split on the pad openers, take the first (net ...) of each
        pads = re.split(r'(?=\(pad ")', block)[1:]
        for chunk in pads:
            num = re.match(r'\(pad "([^"]*)"', chunk).group(1)
            net = re.search(r'\(net \d+ "([^"]*)"\)', chunk)
            padnets.append((ref.group(1), num, net.group(1) if net else ""))
    return sorted(place), sorted(padnets)


def _first_diff(a: list, b: list) -> str:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"first difference at index {i}: fresh={x!r} committed={y!r}"
    return f"length differs: fresh={len(a)} committed={len(b)}"


def test_committed_sch_and_pcb_match_generator(tmp_path):
    """Round-9 capstone: the committed schematics AND boards must be what the
    generator produces from the committed inputs.

    Covers, without kicad-cli/pcbnew (pure text, so CI can run it when the
    system footprint library exists): schematic instances (symbol/position/
    value/footprint), label positions, no-connect markers, board placement
    and -- the netlist axis proved by hand in round 6 -- every pad's net, for
    BOTH boards. A hand edit to a committed sch/pcb that the generator does
    not know about (or a generator change that was never regenerated) fails
    here instead of drifting until an audit finds it.
    """
    import pytest

    from tools.kicad._paths import FP_LIB

    if not Path(FP_LIB, "Capacitor_SMD.pretty").exists():
        pytest.skip("system KiCad footprint libraries not installed")

    _run_generator(tmp_path)

    for stem in ("qssc", "chip_module"):
        fresh_sch = tmp_path / "hardware" / "pcb" / f"{stem}.kicad_sch"
        comm_sch = REPO / "hardware" / "pcb" / f"{stem}.kicad_sch"
        fi, fl, fn = _sch_surface(fresh_sch)
        ci, cl, cn = _sch_surface(comm_sch)
        assert fi == ci, f"{stem}.kicad_sch instances: {_first_diff(fi, ci)}"
        assert fl == cl, f"{stem}.kicad_sch labels: {_first_diff(fl, cl)}"
        assert fn == cn, f"{stem}.kicad_sch no_connects: {_first_diff(fn, cn)}"

        fresh_pcb = tmp_path / "hardware" / "pcb" / f"{stem}.kicad_pcb"
        comm_pcb = REPO / "hardware" / "pcb" / f"{stem}.kicad_pcb"
        fp_, fnets = _pcb_surface(fresh_pcb)
        cp, cnets = _pcb_surface(comm_pcb)
        assert fp_ == cp, f"{stem}.kicad_pcb placement: {_first_diff(fp_, cp)}"
        assert fnets == cnets, f"{stem}.kicad_pcb pad nets: {_first_diff(fnets, cnets)}"


def test_committed_bom_matches_generator(tmp_path):
    """hardware/datasheets/BOM.csv must be exactly what tools/gen_bom.py
    emits from the committed generator inputs (round-10 gate).

    gen_bom drives the SAME build_* model functions the boards do (pure
    Python, no kicad-cli/system lib), so a BOM the generator would not
    produce -- a hand-added row, a stale value/footprint, a changed MPN map
    that was never regenerated -- fails here instead of drifting until an
    audit finds it. Reproducibility was proven by hand in round 2; this
    makes it permanent, mirroring the sch/pcb capstone above.
    """
    import os
    import subprocess
    import sys

    _copy_gen_inputs(tmp_path, with_pro=False)
    res = subprocess.run(
        [sys.executable, str(tmp_path / "tools" / "gen_bom.py")],
        cwd=tmp_path, capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert res.returncode == 0, (
        f"gen_bom.py failed on a clean copy of the committed inputs:\n"
        f"stdout tail: {res.stdout[-1200:]}\nstderr tail: {res.stderr[-1200:]}")

    fresh = (tmp_path / "hardware" / "datasheets" / "BOM.csv").read_text(
        encoding="utf-8")
    committed = (REPO / "hardware" / "datasheets" / "BOM.csv").read_text(
        encoding="utf-8")
    assert fresh == committed, (
        "hardware/datasheets/BOM.csv no longer equals gen_bom.py's output; "
        f"first difference at line "
        f"{next((i + 1 for i, (a, b) in enumerate(zip(fresh.splitlines(), committed.splitlines())) if a != b), 'length/EOF')}")
