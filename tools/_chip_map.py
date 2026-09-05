#!/usr/bin/env python3
"""Print the full 64-per-side chip->component connection map for both boards.
Main: EM{i} -> emitter_cat{i} -> TLC5947 (UE{j}) output pad.
Head: DET{i} -> DET_IN{i} -> TLV9004 TIA (UT{j}) input pad; and the TIA output
      net DET_TIA{i} -> AD7616 (UAD{k}) analog input pad.
Nets are fixed (routing only adds copper), so this is valid mid-FreeRouting."""
import pcbnew
from pathlib import Path
REPO = Path("/home/cryptocalypse/projects/QCHIP/qssc")

def load(name):
    return pcbnew.LoadBoard(str(REPO / "hardware/pcb" / name))

# ---- MAIN: 64 EM cathodes -> TLC5947 ----
b = load("qssc.kicad_pcb")
fps = list(b.GetFootprints())
# map net -> list of (ref, padnum) excluding Uchip
net_dest = {}
for fp in fps:
    if fp.GetReference() == "Uchip":
        continue
    for p in fp.Pads():
        nm = p.GetNetname()
        if nm and nm.startswith("emitter_cat"):
            net_dest.setdefault(nm, []).append(f"{fp.GetReference()}.{p.GetNumber()}")
print("="*70)
print("MAIN BOARD -- 64 EMITTER cathodes (die bottom face) -> TLC5947 driver")
print("="*70)
print(f"{'DIE PAD':<8} {'NET':<16} -> {'TLC5947 SINK'}")
for i in range(64):
    nm = f"emitter_cat{i}"
    d = net_dest.get(nm, ["(UNCONNECTED!)"])
    print(f"EM{i:<5} {nm:<16} -> {d[0]}")
# EM_ANODE
anode = []
for fp in fps:
    if fp.GetReference() == "Uchip":
        continue
    for p in fp.Pads():
        if p.GetNetname() == "+12V_EMIT":
            anode.append(f"{fp.GetReference()}.{p.GetNumber()}")
print(f"EM_ANODE +12V_EMIT     -> {anode}")

# ---- HEAD: 64 DET electrodes -> TIA -> AD7616 ----
b = load("chip_module.kicad_pcb")
fps = list(b.GetFootprints())
net_dest_in = {}   # DET_IN{i} -> TIA pad
net_dest_tia = {}  # DET_TIA{i} -> AD7616 pad
for fp in fps:
    if fp.GetReference() == "Uchip":
        continue
    for p in fp.Pads():
        nm = p.GetNetname()
        if nm and nm.startswith("DET_IN") and not nm.startswith("DET_IN"):
            pass
        if nm and nm.startswith("DET_IN"):
            net_dest_in.setdefault(nm, []).append(f"{fp.GetReference()}.{p.GetNumber()}")
        elif nm and nm.startswith("DET_TIA"):
            net_dest_tia.setdefault(nm, []).append(f"{fp.GetReference()}.{p.GetNumber()}")
print()
print("="*70)
print("HEAD BOARD -- 64 DETECTOR electrodes (die top face) -> TIA -> AD7616 ADC")
print("="*70)
print(f"{'DIE PAD':<8} {'NET':<12} -> {'TIA INPUT':<14} -> {'ADC INPUT (via DET_TIA)'}")
for i in range(64):
    nin = f"DET_IN{i}"
    ntia = f"DET_TIA{i}"
    tin = net_dest_in.get(nin, ["(UNCONNECTED!)"])
    adc = [x for x in net_dest_tia.get(ntia, []) if x.startswith("UAD")]
    tia = [x for x in net_dest_in.get(nin, []) if x.startswith("UT")]
    print(f"DET{i:<4} {nin:<12} -> {tia[0] if tia else tin[0]:<14} -> {adc[0] if adc else '(none)'}")