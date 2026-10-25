import re
txt = open("hardware/pcb/qssc.kicad_pcb", encoding="utf-8").read()
i = txt.find('reference "UE0"')
j = txt.find('(footprint ', i + 1)
blk = txt[i:j]
for m in re.finditer(r'\(pad "" (\w+) (\w+)', blk):
    seg = blk[m.start():m.start() + 280]
    lay = re.search(r'\(layers "([^"]*)"\)', seg)
    net = re.search(r'\(net \d+ "([^"]*)"\)', seg)
    print(f"type={m.group(1):10s} shape={m.group(2):10s} layers={ (lay.group(1) if lay else '?'):20s} net={net.group(1) if net else '(NONE)'}")
print("count pad-empty:", len(re.findall(r'\(pad "" ', blk)))
print("count pad-33:  ", len(re.findall(r'\(pad "33"', blk)))