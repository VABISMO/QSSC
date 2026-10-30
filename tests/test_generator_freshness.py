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
