#!/usr/bin/env python3
"""Reproducibly build ``.tmpwork/pins.json`` -- the standard-KiCad-symbol pin
tables that ``tools/gen_qssc_kicad.py`` (and ``gen_chip_module.py``) load at
import time via ``std_pins(libname, sym)``.

``pins.json`` is a *build input* to the board generators (it is read at import,
never hand-edited), but until now it had **no builder**: a fresh clone with no
``.tmpwork/`` could not regenerate ``hardware/pcb/qssc.kicad_*`` because the
generator crashed at ``json.loads((ROOT / ".tmpwork" / "pins.json").read_text())``.
This script is the missing builder, mirroring ``tools/gen_fpga_ballmap.py``
(which builds the sibling ``.tmpwork/fpga_cpg236_map.json`` from the real
Artix-7 symbol). Run it whenever the set of ``std_pins`` symbols changes:

    python3 tools/build_pins_json.py

It extracts, for each symbol the generators actually call ``std_pins`` on, the
pin list straight from the installed KiCad symbol library
(``/usr/share/kicad/symbols/<lib>.kicad_sym``): number, name, electrical type,
and the symbol-editor (at x y rot)/length geometry. The passives (R/C/L/D/LED/
Polyfuse), AMS1117 family and AP2127K are deliberately NOT here -- those are
datasheet-authored custom symbols in the generators, not ``std_pins`` lookups,
so their pin tables live in the generator source, not in this artifact.

Reproducibility note -- pinned snapshot vs builder
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The committed ``.tmpwork/pins.json`` is the **pinned snapshot** that produced
the checked-in boards; it is un-ignored and committed precisely so a fresh
clone reproduces those boards byte-for-byte. This builder regenerates the table
from the **currently installed** KiCad symbol library, so across KiCad versions
it can differ from the pinned snapshot (notably ``Connector:USB_C_Receptacle``:
the pinned snapshot has the 25-pin full pinout, newer KiCad ships a 19-pin
USB-2.0 variant). 12 of the 13 symbols reproduce identically. Re-running this
builder therefore *upgrades* pins.json to the current library -- after which
you must regenerate + re-ERC/DRC the boards and commit the result; do not run
it expecting to reproduce the committed boards. The committed snapshot is the
authority for reproducibility; this script is the authority for "how the pins
were obtained" (they are extracted from real KiCad symbols, not hand-authored).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / ".tmpwork" / "pins.json"
SYM_LIB = Path("/usr/share/kicad/symbols")

# The exact set of std_pins("lib", "sym") calls made by gen_qssc_kicad.py and
# gen_chip_module.py. Keep in sync with those files (grep std_pins).
SYMBOLS: list[tuple[str, str]] = [
    ("74xGxx", "74LVC1G04"),
    ("Amplifier_Operational", "ADA4530-1"),
    ("Connector", "Barrel_Jack"),
    ("Connector", "Conn_Coaxial"),
    ("Connector", "USB_C_Receptacle"),
    ("Connector_Generic", "Conn_01x02"),
    ("Connector_Generic", "Conn_02x05_Odd_Even"),
    ("Connector_Generic", "Conn_02x08_Odd_Even"),
    ("Connector_Generic", "Conn_02x30_Odd_Even"),
    ("Interface_USB", "FT232RL"),
    ("Oscillator", "ASE-xxxMHz"),
    ("Switch", "SW_Push"),
    ("Timer_PLL", "ADF4351"),
]

# (pin <etype> <shape> (at x y rot) (length L)
#    (name "N" (effects ...)) (number "NUM" (effects ...)))
# Effects blocks are nested and may contain parens, so match name/number by the
# opening "(name "..."/"(number "..." then rely on the next "(pin" / end to
# delimit -- simpler: capture up to the closing of name/number quotes only.
_PIN = re.compile(
    r"\(pin\s+(\w+)\s+(\w+)\s+"            # etype, shape
    r"\(at\s+([\-\d.]+)\s+([\-\d.]+)\s+([\-\d.]+)\)\s*"  # x y rot
    r"\(length\s+([\-\d.]+)\)\s*"          # length
    r"\(name\s+\"([^\"]*)\""               # name (quoted)
    r".*?"                                 # name effects block (non-greedy)
    r"\(number\s+\"([^\"]*)\"",            # number (quoted)
    re.S,
)


def _symbol_block(lib_text: str, sym: str) -> str:
    """Paren-balanced block of the top-level `(symbol "sym" ...)` definition."""
    key = f'(symbol "{sym}"'
    i = lib_text.find(key)
    if i == -1:
        raise KeyError(f'symbol "{sym}" not found in lib')
    # i points at the '(' of the top-level (symbol "sym" ...) definition.
    depth = 0
    j = i
    while j < len(lib_text):
        c = lib_text[j]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return lib_text[i:j + 1]
        j += 1
    raise RuntimeError(f"unbalanced parens for {sym}")


def extract_pins(lib: str, sym: str) -> list[dict]:
    """Extract the std_pins pin list for one lib:symbol from the KiCad lib."""
    path = SYM_LIB / f"{lib}.kicad_sym"
    if not path.exists():
        raise FileNotFoundError(f"KiCad symbol lib not installed: {path}")
    block = _symbol_block(path.read_text(), sym)
    pins = []
    for m in _PIN.finditer(block):
        etype, _shape, x, y, rot, length, name, number = m.groups()
        pins.append({
            "number": number,
            "name": name,
            "etype": etype,
            "x": float(x),
            "y": float(y),
            "rot": float(rot),
            "length": float(length),
        })
    if not pins:
        raise RuntimeError(f"no pins parsed for {lib}:{sym} (regex miss)")
    # std_pins consumers key by number; the generator expects a stable order.
    # Preserve symbol-definition order (regex finditer order), which matches how
    # the original artifact was extracted.
    return pins


def main() -> dict:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    table: dict[str, list[dict]] = {}
    for lib, sym in SYMBOLS:
        table[f"{lib}:{sym}"] = extract_pins(lib, sym)
    OUT.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT} ({len(table)} symbols)")
    for k in sorted(table):
        print(f"  {k}: {len(table[k])} pins")
    return table


if __name__ == "__main__":
    main()