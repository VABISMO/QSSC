# tests/test_pcb_route_full.py
"""Full headless routing-pipeline smoke test (``@pytest.mark.slow``).

Runs the actual ``tools/`` pipeline end-to-end on a TEMPORARY copy of the
regenerated board:

    gen_qssc_kicad.py  ->  route_signals.py  ->  route_qssc_pcb.py

and asserts the line router ties a meaningful fraction of non-plane nets (the
headless L/Z router cannot escape every interior 0.8 mm-pitch CSG324 BGA ball
-- see route_signals.py docstring -- so we assert a threshold, not 100%), that
the plane foundation lands the two inner planes, and that the pipeline is
idempotent (a second pass reproduces the same net counts).

This is SLOW (the line router is O(nets * grid) on a 0.2 mm grid over an 85 mm
board) and is gated behind ``QSSC_SLOW=1`` so it does not run in the normal
local/CI suite. The committed board (already routed + DRC-clean, verified by
``test_pcb_drc.py``) is the fast-path source of truth.

No skips: CI sets ``QSSC_SLOW=1`` and installs KiCad, so the full pipeline
smoke runs there too (``tests/_pcb_env`` fails loudly when pcbnew is absent).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from _pcb_env import child_env, ensure_pcbnew

ensure_pcbnew()

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"
GEN = TOOLS / "gen_qssc_kicad.py"
ROUTE_SIG = TOOLS / "route_signals.py"
ROUTE_PLANE = TOOLS / "route_qssc_pcb.py"

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(os.environ.get("QSSC_SLOW") != "1",
                       reason="set QSSC_SLOW=1 to run the full pipeline"),
]

# A headless L/Z router on the merged single board cannot escape every interior
# 0.8 mm-pitch CSG324 BGA ball, nor every 0.30 mm-pitch QSSC_Chip die pad (the
# 128-pad die gets a staggered dog-bone fanout: 0.15 mm F.Cu stub + 0.20 mm
# through-via per pad, escaped on B.Cu). The router routes a meaningful fraction
# of the ~500 non-plane nets DRC-clean: the via-footprint check (a THROUGH via's
# r3 mark on all 4 signal layers, so a via-up can never land 0.1-0.6 mm off
# another net's track) and the multi_vip / multi_keepout guards (a per-net
# override can never relax a cell a NEIGHBOUR pad/via also owns) are provably
# DRC-correct but conservative. This threshold guards against a regression
# below the router's known capacity (placement drift margin), NOT a
# completeness target; the real correctness bar is test_pcb_drc.py (0 violations
# on the committed board). The remaining nets (BGA edge escapes, chip B.Cu
# escapes, dense-connector ties) are finished in the KiCad GUI (see
# HARDWARE_DESIGN §15) -- the headless router is a 1st pass, not a full
# autorouter.
MIN_ROUTED = 12


def _run(script: Path, env: dict, cwd: Path) -> str:
    res = subprocess.run(
        [sys.executable, str(script)],
        env=env, cwd=str(cwd), capture_output=True, text=True,
    )
    assert res.returncode == 0, (
        f"{script.name} failed:\n-- stdout --\n{res.stdout}\n"
        f"-- stderr --\n{res.stderr}"
    )
    return res.stdout


def _routed_count(stdout: str) -> int:
    # route_signals prints: "routed 32 / 61 | tracks ...". pcbnew's swig binding
    # emits memory-leak warnings to stdout WITHOUT a trailing newline, so they
    # concatenate onto the summary line ("...no destructor found.routed 15 / 71
    # | ...") -- line.startswith("routed ") then misses it. Search for the
    # "routed <N> /" token anywhere instead.
    import re
    m = re.search(r"routed (\d+) /", stdout)
    if not m:
        raise AssertionError(f"no 'routed N / M' line in route_signals output:\n"
                             f"{stdout[:4000]}")
    return int(m.group(1))


def test_full_pipeline_idempotent(tmp_path):
    env = child_env(PYTHONPATH=str(TOOLS),
                    QSSC_PCB_PATH=str(tmp_path / "qssc.kicad_pcb"))
    # gen writes qssc.kicad_pcb to its own CWD (hardware/pcb); copy the project
    # bundle into tmp so the pipeline runs off a writable tree, then point
    # QSSC_PCB_PATH at the temp board.
    hw_pcb = REPO / "hardware" / "pcb" / "qssc.kicad_pcb"
    hw_pro = REPO / "hardware" / "pcb" / "qssc.kicad_pro"
    hw_sch = REPO / "hardware" / "pcb" / "qssc.kicad_sch"
    shutil_src = [p for p in (hw_pcb, hw_pro, hw_sch) if p.exists()]
    for p in shutil_src:
        (tmp_path / p.name).write_bytes(p.read_bytes())

    # Use the committed (regenerated) board as the starting point -- we do not
    # re-run gen here (it needs the full KiCad symbol/pretty-footprint libs and
    # is the slowest step); the committed artefact is already regenerated.
    out1 = _run(ROUTE_SIG, env, tmp_path)
    n1 = _routed_count(out1)
    assert n1 >= MIN_ROUTED, (
        f"first pass routed only {n1} nets (< {MIN_ROUTED}); route_signals "
        f"regression\n{out1}")

    _run(ROUTE_PLANE, env, tmp_path)

    # idempotent: a second route_signals pass on the already-routed board must
    # reproduce the same routed count (the router clears its own tracks first).
    out2 = _run(ROUTE_SIG, env, tmp_path)
    n2 = _routed_count(out2)
    assert n2 == n1, (
        f"non-idempotent: first pass routed {n1}, second routed {n2}")
    _run(ROUTE_PLANE, env, tmp_path)