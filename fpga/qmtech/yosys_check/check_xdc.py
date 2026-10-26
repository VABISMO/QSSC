#!/usr/bin/env python3
"""
check_qmtech_xdc.py -- validate fpga/qmtech/constraints_qmtech.xdc against
top_qmtech.v.

Checks:
  1. every PACKAGE_PIN ball is a real user I/O of the QMTECH board
     (JP2 + JP3 + the on-board clock/LED/key balls)
  2. no ball is assigned to more than one port
  3. every port of top_qmtech.v has a PACKAGE_PIN (buses expanded)
  4. every port has an IOSTANDARD

Run from the repo root:  python3 fpga/qmtech/yosys_check/check_xdc.py
Exit code 0 = PASS, 1 = FAIL.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
XDC = ROOT / "fpga" / "qmtech" / "constraints_qmtech.xdc"
TOP = ROOT / "fpga" / "qmtech" / "top_qmtech.v"

# --- boards I/O that physically exist ---------------------------------------
JP2 = ("V8 V7 U7 V6 T7 R7 R6 T5 R5 T4 U6 U5 V4 U4 V3 V2 U2 U1 P4 T3 R3 T2 "
       "N4 P3 M6 M5 L5 L4 K3 K5 P1 R1 N1 N2 M2 M1 J5 J4 J6 K6 L2 L3 K2 K1").split()
JP3 = ("F18 G17 E18 F17 D18 E17 C17 C18 G15 H16 F15 G16 E16 E15 D16 D15 "
       "A17 B17 C16 B16 A15 B15 A14 B14 C14 D14 C13 D13 A13 C12 F14 E13 "
       "B12 A12 C11 B11 D11 D10 C9 D9 A10 B10 A9 B9").split()
ONBOARD = ["R2", "D8", "C8", "H18", "H17"]
ALLOWED = set(JP2 + JP3 + ONBOARD)


def get_ports_exprs(text):
    """Yield the raw expression inside each [get_ports ...], brace-balanced."""
    out, i = [], 0
    while True:
        j = text.find("[get_ports", i)
        if j < 0:
            return out
        k, depth = j, 0
        while k < len(text):
            if text[k] in "[{":
                depth += 1
            elif text[k] in "]}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        out.append(text[j + len("[get_ports"):k].strip())
        i = k + 1


def expand(expr, bus_widths):
    """Turn a get_ports expression into a list of concrete port names."""
    expr = expr.strip()
    if expr.startswith("{") and expr.endswith("}"):
        expr = expr[1:-1]
    names = []
    for tok in expr.split():
        tok = tok.strip()
        m = re.fullmatch(r"(\w+)\[(\d+|\*)\]", tok)
        if m:
            base, idx = m.group(1), m.group(2)
            w = bus_widths.get(base)
            if idx == "*":
                rng = range(w) if w else []
                names += ["%s[%d]" % (base, n) for n in rng]
            else:
                names.append(tok)
            continue
        m = re.fullmatch(r"(\w+)\[(\d+):(\d+)\]", tok)
        if m:
            base, hi, lo = m.group(1), int(m.group(2)), int(m.group(3))
            names += ["%s[%d]" % (base, n) for n in range(min(hi, lo), max(hi, lo) + 1)]
            continue
        names.append(tok)
    return names


def main():
    xdc = XDC.read_text()
    top = TOP.read_text()

    # --- ports + bus widths from the module header --------------------------
    hdr = top[top.index("module top_qmtech"):top.index(");")]
    ports, widths = [], {}
    for m in re.finditer(
            r"\b(input|output|inout)\s+(?:\[(\d+):(\d+)\]\s*)?(\w+)", hdr):
        name = m.group(4)
        ports.append(name)
        if m.group(2) is not None:
            widths[name] = abs(int(m.group(2)) - int(m.group(3))) + 1

    def concrete(names):
        """Expand port names to every concrete instance (bits of buses)."""
        out = []
        for n in names:
            if n in widths:
                out += ["%s[%d]" % (n, k) for k in range(widths[n])]
            else:
                out.append(n)
        return out

    # --- 1/2: PACKAGE_PIN ----------------------------------------------------
    pin_map, dupes = {}, []
    for stmt in re.finditer(r"set_property\s+PACKAGE_PIN\s+(\w+)\s+(.*?)(?=\nset_property|\Z)",
                            xdc, re.S):
        ball = stmt.group(1)
        for expr in get_ports_exprs(stmt.group(2)):
            for p in expand(expr, widths):
                if p in pin_map:
                    dupes.append((p, pin_map[p], ball))
                pin_map[p] = ball

    bad_balls = sorted((p, b) for p, b in pin_map.items() if b not in ALLOWED)

    # --- 3: every port pinned -----------------------------------------------
    unpinned = [p for p in sorted(concrete(ports)) if p not in pin_map]

    # --- 4: every port has an IOSTANDARD ------------------------------------
    io_ports = set()
    for stmt in re.finditer(r"set_property\s+IOSTANDARD\s+\w+\s+(.*?)(?=\nset_property|\Z)",
                            xdc, re.S):
        for expr in get_ports_exprs(stmt.group(1)):
            for p in expand(expr, widths):
                io_ports.add(p)
    no_io = [p for p in sorted(concrete(ports)) if p not in io_ports]

    # --- report --------------------------------------------------------------
    print("ports in top_qmtech.v : %d (%d bits)" % (len(ports), len(concrete(ports))))
    print("PACKAGE_PIN assigned  : %d" % len(pin_map))
    print("IOSTANDARD assigned   : %d" % len(io_ports))
    print()

    def block(title, items, fmt):
        print("--- %s ---" % title)
        if items:
            for it in items:
                print("   FAIL  " + fmt % (it,))
        else:
            print("   OK - none")

    block("ball not a valid QMTECH user I/O", bad_balls, "%s (%s)")
    block("port pinned to more than one ball", dupes, "%s (%s and %s)")
    block("port with no PACKAGE_PIN", unpinned, "%s")
    block("port with no IOSTANDARD", no_io, "%s")
    print()
    ok = not (bad_balls or dupes or unpinned or no_io)
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
