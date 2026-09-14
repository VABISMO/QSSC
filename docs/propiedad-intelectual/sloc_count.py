#!/usr/bin/env python3
"""Reproducible SLOC count + COCOMO valuation for the QSSC IP deposit.

Tool of record: **SLOCCount 2.26** (David A. Wheeler, GPL).

SLOCCount recognises Python, shell, Tcl, JavaScript (``.js``), C (``.c``)
and many others natively, but it has two gaps relevant to this deposit:

  * **no Verilog counter** and no ``.v`` / ``.xdc`` recognition — it silently
    skips all RTL and constraints;
  * **no ``.jsx`` / ``.css`` recognition** — it silently skips the React
    components and the stylesheet.

These skipped languages share comment syntax with languages SLOCCount *does*
recognise, so they are counted by **native SLOCCount** after an extension
rename:

  * ``.v`` / ``.jsx`` / ``.css`` → ``.c``  (ANSI-C counter: ``//`` and ``/* */``)
  * ``.xdc`` → ``.py``                    (Python counter: ``#`` comments)

Validation anchor: the two ``.js`` files counted natively by SLOCCount
(``api.js`` + ``derive.js`` = **336 SLOC**) give the *same* 336 when renamed
to ``.c`` and run through the ANSI-C counter.  This proves the rename
preserves SLOCCount's exact line-counting semantics for the C-syntax family.

COCOMO basic model (identical formula to SLOCCount)::

    PM      = 2.4 * KSLOC ** 1.05      # person-months
    PY      = PM / 12                  # person-years
    sched   = 2.5 * PM ** 0.38         # months
    cost    = PY * 56286 * 2.40        # USD (avg salary + overhead)

Per-file SLOC below come from native SLOCCount (``--details``) with the
extension rename applied; they are frozen here so the deposit is exactly
reproducible without re-running the rename.

Usage::

    python3 docs/propiedad-intelectual/sloc_count.py            # print totals
    python3 docs/propiedad-intelectual/sloc_count.py --cocomo N  # COCOMO for N SLOC
"""
from __future__ import annotations

import sys

# ---------------------------------------------------------------------------
# Native-SLOCCount per-file counts (with .c/.py rename for the extension gap)
# ---------------------------------------------------------------------------

FPGA_FILES = {
    # Verilog (.v -> .c, ANSI-C counter)
    "fpga/rtl/adc_reader.v": 303,
    "fpga/rtl/adf4351_ctrl.v": 159,
    "fpga/rtl/command_parser.v": 1769,
    "fpga/rtl/dac_ctrl.v": 104,
    "fpga/rtl/emitter_array.v": 198,
    "fpga/rtl/qrng_entropy.v": 67,
    "fpga/rtl/spi_master.v": 104,
    "fpga/rtl/tlc5947_ctrl.v": 112,
    "fpga/rtl/top_level.v": 288,
    "fpga/rtl/uart_rx.v": 106,
    "fpga/rtl/uart_tx.v": 94,
    "fpga/sim/tb_top_level.v": 926,
    # XDC (.xdc -> .py, # counter)
    "fpga/constraints.xdc": 75,
    # synth (native SLOCCount: .py / .sh / .tcl)
    "fpga/synth/make_report.py": 136,
    "fpga/synth/run_synth.sh": 29,
    "fpga/synth/build_bitstream.tcl": 13,
}

FRONTEND_FILES = {
    # JSX/CSS (.jsx/.css -> .c, ANSI-C counter); .js native
    "frontend/src/App.jsx": 1833,
    "frontend/src/components.jsx": 1628,
    "frontend/src/styles.css": 538,
    "frontend/src/derive.js": 281,
    "frontend/src/api.js": 55,
    "frontend/src/main.jsx": 9,
}

BACKEND_SLOC = 7825  # native SLOCCount on api/ + qssc/ (Python, no rename)

PCB_FILES = {
    # native SLOCCount on tools/ (Python, no rename); the 23-file KiCad EDA toolchain
    "tools/_audit_pins.py": 17,
    "tools/_chip_map.py": 58,
    "tools/_grep_ep.py": 12,
    "tools/_kicad_compat.py": 36,
    "tools/add_grad_fanout.py": 46,
    "tools/audit_connections.py": 121,
    "tools/audit_netlist.py": 58,
    "tools/clear_net.py": 20,
    "tools/close_residuals.py": 626,
    "tools/copy_zones.py": 60,
    "tools/fill_zones.py": 16,
    "tools/freeroute.py": 196,
    "tools/gen_bom.py": 149,
    "tools/gen_qssc_kicad.py": 1671,
    "tools/inject_base_routing.py": 118,
    "tools/maze_bridge_2d.py": 280,
    "tools/maze_bridge_3d.py": 439,
    "tools/route_grad_nets.py": 167,
    "tools/route_qssc_pcb.py": 645,
    "tools/route_signals.py": 984,
    "tools/sync_placement.py": 59,
    "tools/usb_c_reversible_tie.py": 103,
    "tools/widen_tracks.py": 37,
}

CI_TESTS_FILES = {
    # 4 GitHub Actions workflows (.yml -> .py rename, # counter — same as .xdc)
    ".github/workflows/ci.yml": 125,
    ".github/workflows/deploy.yml": 109,
    ".github/workflows/pcb.yml": 59,
    ".github/workflows/release.yml": 73,
    # 33 pytest test files (native SLOCCount on .py)
    "tests/test_algorithms_platforms.py": 127,
    "tests/test_api.py": 294,
    "tests/test_array_calibration.py": 220,
    "tests/test_circuit_histogram.py": 127,
    "tests/test_circuit_physics_audit.py": 179,
    "tests/test_core.py": 592,
    "tests/test_detector_centroid.py": 96,
    "tests/test_field_gradient_register.py": 87,
    "tests/test_fpga_sim.py": 67,
    "tests/test_gcode.py": 267,
    "tests/test_hardware_real.py": 294,
    "tests/test_magnetometer.py": 62,
    "tests/test_noise_filters.py": 222,
    "tests/test_noise_summary.py": 53,
    "tests/test_nuclear_memory.py": 83,
    "tests/test_parallel_computing.py": 287,
    "tests/test_pcb_component_audit.py": 173,
    "tests/test_pcb_connectivity.py": 235,
    "tests/test_pcb_die_fpga_audit.py": 143,
    "tests/test_pcb_drc.py": 38,
    "tests/test_pcb_head.py": 129,
    "tests/test_pcb_pin_audit.py": 121,
    "tests/test_pcb_route.py": 62,
    "tests/test_pcb_route_full.py": 55,
    "tests/test_pcb_route_signals.py": 78,
    "tests/test_pcb_stackup.py": 39,
    "tests/test_pixel_mode.py": 109,
    "tests/test_prime_readout.py": 101,
    "tests/test_qrng.py": 135,
    "tests/test_render_p0_svg.py": 29,
    "tests/test_render_p1_svg.py": 23,
    "tests/test_spincoater_gcode.py": 50,
    "tests/test_superres_bayesian_tensor.py": 266,
}

# cloc 2.11 cross-check (code lines, excludes blank+comment) — per unit
CLOC_CODE = {
    "FPGA": 4524,
    "Frontend": 4328,
    "Backend": 8013,
    "PCB": 5937,
    "CI+Tests": 5181,
}


# ---------------------------------------------------------------------------
# COCOMO basic model
# ---------------------------------------------------------------------------

def cocomo(sloc: int) -> dict[str, float]:
    pm = 2.4 * (sloc / 1000.0) ** 1.05
    py = pm / 12.0
    sched = 2.5 * pm ** 0.38
    return {
        "sloc": sloc,
        "person_months": pm,
        "person_years": py,
        "schedule_months": sched,
        "developers": pm / sched if sched else 0.0,
        "cost_usd": py * 56286.0 * 2.40,
    }


def _print_area(name: str, files: dict[str, int]) -> int:
    total = sum(files.values())
    print(f"=== {name} ({total} SLOC) ===")
    for path, n in sorted(files.items()):
        print(f"  {n:5d}  {path}")
    c = cocomo(total)
    print(f"  -> {c['person_years']:.2f} PY ({c['person_months']:.1f} PM), "
          f"${c['cost_usd']:,.0f}")
    print()
    return total


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--cocomo":
        sloc = int(args[1]) if len(args) > 1 else int(sys.stdin.read().strip())
        r = cocomo(sloc)
        print(f"SLOC                = {r['sloc']:,}")
        print(f"Person-Years (PM)   = {r['person_years']:.2f} "
              f"({r['person_months']:.1f} months)")
        print(f"Schedule            = {r['schedule_months']:.1f} months")
        print(f"Developers          = {r['developers']:.2f}")
        print(f"Cost (USD)          = ${r['cost_usd']:,.0f}")
        return 0

    fpga = _print_area("FPGA", FPGA_FILES)
    frontend = _print_area("Frontend", FRONTEND_FILES)
    c = cocomo(BACKEND_SLOC)
    print(f"=== Backend ({BACKEND_SLOC} SLOC, native SLOCCount api/+qssc/) ===")
    print(f"  -> {c['person_years']:.2f} PY ({c['person_months']:.1f} PM), "
          f"${c['cost_usd']:,.0f}")
    print()
    pcb = _print_area("PCB (KiCad EDA toolchain)", PCB_FILES)
    ci = _print_area("CI + Tests (workflows + pytest)", CI_TESTS_FILES)
    grand = fpga + frontend + BACKEND_SLOC + pcb + ci
    gc = cocomo(grand)
    print(f"=== GRAND TOTAL (5 units) === {grand} SLOC")
    print(f"  -> {gc['person_years']:.2f} PY ({gc['person_months']:.1f} PM), "
          f"${gc['cost_usd']:,.0f}")
    print()

    # ---- cloc 2.11 cross-check ----
    sloc_by_unit = {
        "FPGA": fpga,
        "Frontend": frontend,
        "Backend": BACKEND_SLOC,
        "PCB": pcb,
        "CI+Tests": ci,
    }
    print("=== SLOCCount vs cloc 2.11 (cross-check) ===")
    print(f"  {'Unit':<11}{'SLOCCount':>10}{'cloc':>8}{'diff':>7}")
    stot = ctot = 0
    for name in CLOC_CODE:
        s = sloc_by_unit[name]
        cl = CLOC_CODE[name]
        stot += s
        ctot += cl
        print(f"  {name:<11}{s:>10}{cl:>8}{cl - s:>+7d}")
    print(f"  {'TOTAL':<11}{stot:>10}{ctot:>8}{ctot - stot:>+7d}")
    sc = cocomo(stot)
    cc = cocomo(ctot)
    print(f"  SLOCCount {stot} -> {sc['person_years']:.2f} PY  "
          f"${sc['cost_usd']:,.0f}")
    print(f"  cloc      {ctot} -> {cc['person_years']:.2f} PY  "
          f"${cc['cost_usd']:,.0f}")
    print(f"  cost delta: ${cc['cost_usd'] - sc['cost_usd']:,.0f} "
          f"({(cc['cost_usd'] / sc['cost_usd'] - 1) * 100:+.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())