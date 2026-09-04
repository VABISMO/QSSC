#!/usr/bin/env python3
"""Netlist + component audit for the two QSSC sandwich boards.

Dumps, per board, every component with its value/footprint and its full
pin->net map (and any declared no-connect pins), so the pinout can be checked
against each IC's datasheet BEFORE routing.  The user's bar: nothing missing,
nothing extra, every pin connected per its datasheet.

Run:  python3 tools/audit_netlist.py [--pins]
Pure-Python (imports gen_qssc_kicad, no pcbnew / no resource cap needed).

Default print: a compact per-board component roster (ref value fp #pins #nc).
With --pins: also print the full pin->net map for every IC (components whose
``pins`` dict is non-empty), grouped by reference prefix.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import gen_qssc_kicad as g  # noqa: E402

SHOW_PINS = "--pins" in sys.argv


def _roster(comps):
    return sorted(comps, key=lambda c: c["ref"])


def main() -> None:
    g.build_pincoords()
    for label, build_fn in (("MAIN", g.build_main_board),
                            ("HEAD", g.build_detector_head)):
        g.COMPONENTS.clear()
        build_fn()
        comps = list(g.COMPONENTS)
        print(f"\n{'=' * 72}\n{label}: {len(comps)} components\n{'=' * 72}")

        # Component-type census: nothing-missing / nothing-extra bar.
        by_prefix = Counter()
        ics = []
        for c in comps:
            prefix = "".join(ch for ch in c["ref"] if ch.isalpha())
            by_prefix[prefix] += 1
            if c["pins"]:
                ics.append(c)
        print("type census:", ", ".join(f"{p}={n}" for p, n in
              sorted(by_prefix.items(), key=lambda kv: (-kv[1], kv[0]))))

        # Distinct values (catch duplicates / stale leftovers).
        vals = Counter(c["value"] for c in comps)
        print("distinct values:", len(vals))
        for v, n in sorted(vals.items()):
            if n > 1 or not v:
                print(f"   {n}x  {v!r}")

        # Compact roster.
        print(f"\n-- roster ({len(comps)}) --")
        for c in _roster(comps):
            npin = len(c["pins"])
            nnc = len(c.get("nc_pins", []))
            tag = ""
            if not c["fp"]:
                tag = "  [NO FOOTPRINT]"
            elif not c["pins"] and c["ref"] not in ("Uchip",):
                # passive / mechanical -- has no pin dict (netted by pad name)
                pass
            print(f"  {c['ref']:<8} {c['value']:<22} {c['fp']:<40} "
                  f"pins={npin:<3} nc={nnc}{tag}")

        if SHOW_PINS:
            print(f"\n-- IC pin->net maps ({len(ics)}) --")
            for c in sorted(ics, key=lambda c: c["ref"]):
                print(f"\n  [{c['ref']}] {c['value']}  ({c['fp']})")
                pins = c["pins"]
                ncs = c.get("nc_pins", [])
                # show pins in numeric order if numeric, else sorted
                def _k(p):
                    return (0, int(p)) if p.isdigit() else (1, p)
                for p in sorted(pins, key=_k):
                    print(f"     {p:>3} -> {pins[p]}")
                if ncs:
                    print(f"     NC: {', '.join(sorted(ncs, key=_k))}")

        g.COMPONENTS.clear()


if __name__ == "__main__":
    main()