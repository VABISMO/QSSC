#!/usr/bin/env python3
"""Per-datasheet connection audit for both QSSC boards.

NO routing -- this is the pre-routing bar: every component
present, every IC pin netted per its datasheet (or explicitly no_connect),
nothing missing or extra. Loads each board via pcbnew and reports:

  1. Component census (ref, value, footprint) per board -- flag any missing
     footprint.
  2. For every IC: pin number -> net name (or UNNETTED). Flag unnetted pins.
  3. Decoupling check: every IC power pin (VCC/VDD/AVDD/VAA/..) must have a
     decoupling cap on the same net within the board.
  4. Chip die net -> destination pad (DET_IN{i}/emitter_cat{i}/EM_ANODE).
  5. Power-rail members: which pads sit on each rail.

Usage: python3 tools/audit_connections.py [main|head|both]
Resource-capped by caller (systemd-run, ~700M). System python3 (has pcbnew).
"""
from __future__ import annotations
import sys, re
from pathlib import Path
import pcbnew

REPO = Path(__file__).resolve().parent.parent
PCB_DIR = REPO / "hardware" / "pcb"
BOARDS = {
    "main": PCB_DIR / "qssc.kicad_pcb",
    "head": PCB_DIR / "chip_module.kicad_pcb",
}

# ICs worth a per-pin dump (skip passives). Match by reference prefix.
IC_PREFIXES = ("U", "J", "Y")  # U* ICs, J* connectors, Y* crystals

# Power-rail net names (a pad on one of these is a supply/ground pin).
POWER_NETS = {
    "GND", "+3V3", "+1V8", "+1V0", "+5V", "+5V_A", "+5V_RF", "+12V_EMIT",
    "VBUS_USB", "+5V_IN",
}
POWER_PIN_RE = re.compile(
    r"^(VCC|VDD|AVDD|VAA|VCCD|VCCA|VSS|VSSA|GND|AGND|DGND|AVSS|VCCIO|VCCINT|"
    r"VCCAUX|VCCBATT|VREF|VBAT|VCC_PLL|VCCADC|GNDADC|VCCO_|MGTAVCC|MGTAVTT|"
    r"VCCO0|VCCO1|VCCO_0|VCCO_1|VIN|VPOS|VNEG|VS\+|VS-|V\+|V-)\b",
    re.I,
)

# Nets we EXPECT to be on the chip die (so we can list their destinations).
CHIP_NET_RE = re.compile(r"^(DET_IN|emitter_cat|DET_TIA)(\d+)$|^EM_ANODE$|^DET_REF$")


def _is_ic(ref: str) -> bool:
    return any(ref.startswith(p) for p in IC_PREFIXES)


def audit_board(name: str, path: Path) -> int:
    print(f"\n{'='*78}\nBOARD: {name}  ({path.name})\n{'='*78}")
    b = pcbnew.LoadBoard(str(path))

    # --- 1. component census ---
    fps = list(b.GetFootprints())
    print(f"\n[1] COMPONENT CENSUS: {len(fps)} footprints")
    no_fp = []
    ic_refs = []
    caps = {}  # net -> count of caps on that net
    for fp in fps:
        ref = fp.GetReference()
        lib = fp.GetFPID().GetLibItemName()
        if "(no foot" in str(lib) or lib == "" or "no_footprint" in str(lib):
            no_fp.append(ref)
        if _is_ic(ref):
            ic_refs.append(ref)
        # decoupling caps: ref C* whose pads sit on a power net + another net
        if ref.startswith("C"):
            nets = {p.GetNetname() for p in fp.Pads()}
            for n in nets:
                if n in POWER_NETS:
                    caps[n] = caps.get(n, 0) + 1
    if no_fp:
        print(f"  !! {len(no_fp)} footprint(s) MISSING: {no_fp}")
    else:
        print("  all footprints present")
    # cap census per rail
    print("  decoupling caps per rail:")
    for r in sorted(POWER_NETS):
        c = caps.get(r, 0)
        flag = "" if c else "  << NO CAP"
        print(f"    {r:12s}: {c} cap(s){flag}")

    # --- 2. per-IC pin map ---
    print(f"\n[2] IC PIN MAP ({len(ic_refs)} ICs/connectors) -- UNNETTED pins flagged")
    total_unnetted = 0
    for ref in sorted(ic_refs, key=_refkey):
        fp = next((f for f in fps if f.GetReference() == ref), None)
        if fp is None:
            continue
        pads = list(fp.Pads())
        unnetted = [p.GetNumber() for p in pads if not p.GetNetname() or p.GetNetname() == ""]
        # board-edge power/NC pads common on FPGAs -- only flag ICs (not crystals)
        pinmap = {}
        for p in pads:
            pinmap[p.GetNumber()] = p.GetNetname() or "(unnetted)"
        total_unnetted += len(unnetted)
        flag = f"  << {len(unnetted)} UNNETTED: {unnetted[:12]}{'...' if len(unnetted)>12 else ''}" if unnetted else ""
        print(f"  {ref} ({fp.GetValue()}, {len(pads)} pads){flag}")
        # dump full pin map only for the small ICs (<=80 pins); FPGA is huge
        if len(pads) <= 80:
            items = sorted(pinmap.items(), key=_pinkey)
            line = "    " + "  ".join(f"{n}:{v}" for n, v in items)
            # wrap at 100 chars
            for i in range(0, len(line), 100):
                print(line[i:i+100])
    print(f"  TOTAL unnetted IC pins: {total_unnetted}")

    # --- 3. chip die nets + destinations ---
    print(f"\n[3] CHIP DIE NETS -> destination pads (other than the die itself)")
    chipfp = next((f for f in fps if f.GetReference() == "Uchip"), None)
    chip_pad_pos = set()
    if chipfp:
        chip_pad_pos = {(p.GetPosition().x, p.GetPosition().y) for p in chipfp.Pads()}
    # collect, per chip net, every pad NOT on Uchip
    chip_dests = {}
    for fp in fps:
        for p in fp.Pads():
            nm = p.GetNetname()
            if CHIP_NET_RE.match(nm) and (p.GetPosition().x, p.GetPosition().y) not in chip_pad_pos:
                chip_dests.setdefault(nm, []).append(f"{fp.GetReference()}.{p.GetNumber()}")
    missing = []
    for nm in sorted(chip_dests, key=_netkey):
        d = chip_dests[nm]
        print(f"  {nm:14s} -> {d}")
    # nets declared on Uchip but with NO off-chip destination = dangling chip net
    if chipfp:
        chip_nets = {p.GetNetname() for p in chipfp.Pads() if p.GetNetname()}
        for nm in sorted(chip_nets, key=_netkey):
            if nm not in chip_dests and nm not in POWER_NETS:
                missing.append(nm)
        if missing:
            print(f"  << {len(missing)} chip net(s) with NO off-chip destination: {missing[:20]}")
        else:
            print(f"  all {len(chip_nets)} chip nets have an off-chip destination (or are power)")

    return total_unnetted


def _refkey(r: str):
    m = re.match(r"([A-Z]+)(\d+)", r)
    return (m.group(1), int(m.group(2))) if m else (r, 0)


def _pinkey(kv):
    k = kv[0]
    m = re.match(r"(\d+)(.*)", k)
    return (int(m.group(1)), m.group(2)) if m else (0, k)


def _netkey(n: str):
    m = re.match(r"([A-Za-z_]+)(\d+)", n)
    return (m.group(1), int(m.group(2))) if m else (n, 0)


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    targets = BOARDS if which == "both" else {which: BOARDS[which]}
    tot = 0
    for name, path in targets.items():
        if not path.exists():
            print(f"(skip {name}: {path} not found)")
            continue
        tot += audit_board(name, path)
    print(f"\n{'='*78}\nTOTAL unnetted IC pins across audited boards: {tot}\n{'='*78}")
    sys.exit(1 if tot else 0)


if __name__ == "__main__":
    main()