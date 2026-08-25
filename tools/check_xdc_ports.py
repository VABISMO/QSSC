#!/usr/bin/env python3
"""Check fpga/constraints.xdc against fpga/rtl/top_level.v port declarations.

Catches the two classes of pin-constraint drift that bite FPGA builds late:
  * an RTL port with no XDC pin assignment (unconstrained I/O -> Vivado picks a
    random pin), and
  * an XDC get_ports reference to a name not declared in RTL (stale constraint
    for a removed/renamed port -> silently ignored or a build warning).

Run locally:  python3 tools/check_xdc_ports.py
Run in CI:    see the `fpga` job in .github/workflows/ci.yml.

Exits non-zero on any mismatch. No external dependencies (stdlib only).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RTL = ROOT / "fpga" / "rtl" / "top_level.v"
XDC = ROOT / "fpga" / "constraints.xdc"


def parse_rtl_ports(src: str) -> dict[str, int]:
    """Return {port_name: width} for the top_level module port list.

    width is the bit width (1 for a scalar). Only the first `module ... (...)`
    port list is scanned, so internal module definitions later in the file
    (e.g. the behavioural XADC block) are ignored.
    """
    # Strip comments first: a `);` inside a `// ...` comment (e.g. the mw_gate
    # docstring "... output power (R4); this pin ...") would otherwise fool the
    # non-greedy port-list regex into stopping early.
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)  # block comments
    src = re.sub(r"//[^\n]*", "", src)               # line comments

    # Isolate the first module header up to the closing `);` of its port list.
    m = re.search(r"module\s+top_level\s*\#?[^;]*?\((.*?)\)\s*;", src, re.S)
    if not m:
        raise SystemExit("ERROR: could not find `module top_level` port list")
    port_region = m.group(1)

    ports: dict[str, int] = {}
    # A port declaration: direction [optional reg/wire] [optional [H:L]] name
    # Commas separate declarations.
    for decl in port_region.split(","):
        decl = decl.strip()
        if not decl:
            continue
        m = re.match(
            r"(?:input|output|inout)\b.*?(\[[^\]]*\])?\s*([A-Za-z_]\w*)\s*$",
            decl, re.S,
        )
        if not m:
            continue
        width_str, name = m.group(1), m.group(2)
        width = 1
        if width_str:
            mm = re.match(r"\[(\d+):(\d+)\]", width_str.strip())
            if mm:
                hi, lo = int(mm.group(1)), int(mm.group(2))
                width = abs(hi - lo) + 1
        ports[name] = width
    return ports


def parse_xdc_refs(src: str) -> dict[str, set[int | None]]:
    """Return {port_base_name: {indices}} referenced by get_ports in the XDC.

    index None means a scalar / wildcard `[*]` reference (whole bus).
    """
    refs: dict[str, set] = {}
    for m in re.finditer(r"get_ports\s+\{?([A-Za-z_]\w*)(?:\[(\d+|\*)\])?\}?", src):
        name, idx = m.group(1), m.group(2)
        key: int | None
        if idx is None or idx == "*":
            key = None
        else:
            key = int(idx)
        refs.setdefault(name, set()).add(key)
    return refs


def main() -> int:
    rtl_ports = parse_rtl_ports(RTL.read_text())
    xdc_refs = parse_xdc_refs(XDC.read_text())

    errors: list[str] = []

    # 1. RTL port with no XDC reference at all.
    for name, width in sorted(rtl_ports.items()):
        if name not in xdc_refs:
            errors.append(
                f"RTL port `{name}` (width {width}) has no XDC get_ports constraint"
            )

    # 2. XDC reference to a port not declared in RTL.
    for name in sorted(xdc_refs):
        if name not in rtl_ports:
            errors.append(f"XDC references `{name}` but it is not an RTL port")

    # 3. For vector RTL ports, every bit 0..width-1 needs a PACKAGE_PIN.
    #    IOSTANDARD/SLEW/DRIVE may be wildcarded with `[*]`, so we only require
    #    that each individual bit index has a pin.
    for name, width in sorted(rtl_ports.items()):
        if width <= 1:
            continue
        indices = xdc_refs.get(name, set())
        if None in indices and not any(isinstance(i, int) for i in indices):
            # only a wildcard ref, no per-bit pins
            errors.append(
                f"vector port `{name}`[{width}] only has wildcard XDC refs; "
                f"add per-bit PACKAGE_PIN assignments for indices 0..{width - 1}"
            )
            continue
        missing = [i for i in range(width) if i not in indices]
        if missing:
            errors.append(
                f"vector port `{name}`[{width}] missing PACKAGE_PIN for bits {missing}"
            )

    if errors:
        print("XDC <-> top_level.v port mismatch:")
        for e in errors:
            print(f"  - {e}")
        print(f"\n{len(errors)} problem(s). RTL ports: "
              f"{sorted(rtl_ports)}; XDC refs: {sorted(xdc_refs)}")
        return 1

    print(f"OK: {len(rtl_ports)} top_level ports all matched in XDC.")
    for name, width in sorted(rtl_ports.items()):
        print(f"  {name:<16} width={width}")
    return 0


if __name__ == "__main__":
    sys.exit(main())