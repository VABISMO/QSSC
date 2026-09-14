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
    grand = fpga + frontend + BACKEND_SLOC
    print(f"=== GRAND TOTAL === {grand} SLOC")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())