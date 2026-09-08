# tests/test_pcb_connectivity.py
"""Graph-connectivity gate for both routed QSSC boards -- the real bar.

DRC ``unconnected_items`` is deliberately ``ignore`` while the headless router
catches up (a same-net-through-plane member would otherwise noise it up), so a
DRC-clean board can still hide nets whose pads are joined only by name.  The
weaker ``_dangling_non_plane`` check (a net has *any* copper at all) is fooled
by a partial route -- e.g. a die pad with a 0.15 mm stub + dog-bone via but the
via never closed to the TLC5947/TIA target: the net "has copper" yet is split
into two dead components.  This test builds the actual copper-connectivity
graph per non-plane net (pads + tracks + vias, layer-aware) and asserts every
net's pads sit in ONE connected component -- i.e. the net is electrically
complete, not just netlisted.

This is the test that catches the class of bug the user reported ("there are no
connections on the PCBs to the chip and the components"): a board can be
schematic-clean and DRC-clean yet still have dozens of nets whose pads are
joined only by coincident labels, or half-routed die nets whose via dangles.

No skips: ``tests/_pcb_env.ensure_pcbnew()`` resolves KiCad's binding (and
applies the repo's swig compat patch); CI installs KiCad and runs this
unconditionally. A missing board file is a FAILURE, not a skip.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from _pcb_env import ensure_pcbnew

pcbnew = ensure_pcbnew()

REPO = Path(__file__).resolve().parent.parent
BOARDS = {
    "main": REPO / "hardware" / "pcb" / "qssc.kicad_pcb",
    "head": REPO / "hardware" / "pcb" / "chip_module.kicad_pcb",
}

# Plane / rail nets: covered by full-board copper zones, so a pad-only member is
# still electrically connected through the pour.  Do not graph-check these (a
# pour ties every same-net pad through the zone, so they are one component by
# construction once the zone exists).
PLANE_NETS = {
    "GND", "+3V3", "+1V8", "+1V0", "+5V", "+5V_A", "+5V_RF", "+12V_EMIT",
    "VBUS_USB", "+5V_IN",
}

# Tolerance for "two copper features touch" (nm). 1 um absorbs integer rounding
# of VECTOR2I endpoints shared between a pad, a via and a track placed by the
# same router on a 0.2 mm grid.
EPS_NM = 1_000


# ---- minimal union-find ------------------------------------------------------
class _UF:
    __slots__ = ("p",)

    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra
            return True
        return False


def _pad_layer_set(pad):
    """Layers a pad's copper lives on. A drilled pad (PTH/NPTH) has a barrel on
    every copper layer; an SMD pad lives on its one surface layer only.
    NB: read the pad's LAYER SET, not GetLayer() -- KiCad 10 returns the
    unflipped m_layer (0 == F.Cu) for back-side pads even when the copper
    actually sits on B.Cu (IsOnLayer(B_Cu)=True, GetLayerSet()=={B.Cu,...})."""
    drill = pad.GetDrillSize()
    if drill.x > 0 or drill.y > 0:
        return None          # sentinel: all copper layers
    return set(pad.GetLayerSet().Seq())


def _via_layer_set(board, via):
    # All vias placed by the QSSC routers are THROUGH (barrel on every copper
    # layer). Micro/blind vias report a layer range; treat the inclusive range.
    # Through vias span the whole stack -> None sentinel ("all layers").
    if via.GetViaType() == pcbnew.VIATYPE_THROUGH:
        return None
    lset = via.GetLayerSet()
    return set(lset.Seq())


def _layers_share(a, b):
    """True if two layer-sets (None = all layers) share at least one layer."""
    if a is None or b is None:
        return True
    return bool(a & b)


def _seg_point_dist2(ax, ay, bx, by, px, py):
    """Squared distance from point P to segment AB."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return (px - cx) ** 2 + (py - cy) ** 2


def _seg_seg_dist2(ax, ay, bx, by, cx, cy, dx_, dy_):
    """Squared distance between two segments (AB and CD)."""
    # Try the four endpoint-to-segment distances and the segment intersection.
    best = min(
        _seg_point_dist2(ax, ay, bx, by, cx, cy),
        _seg_point_dist2(ax, ay, bx, by, dx_, dy_),
        _seg_point_dist2(cx, cy, dx_, dy_, ax, ay),
        _seg_point_dist2(cx, cy, dx_, dy_, bx, by),
    )
    return best


def _orient(ax, ay, bx, by, px, py):
    """Sign of the cross product AB x AP (+1/-1/0)."""
    d = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    return (d > 0) - (d < 0)


def _on_seg(ax, ay, bx, by, px, py):
    """Collinear point P: is it inside AB's bounding box?"""
    return (min(ax, bx) <= px <= max(ax, bx)
            and min(ay, by) <= py <= max(ay, by))


def _seg_seg_intersect(ax, ay, bx, by, cx, cy, dx_, dy_):
    """True if segments AB and CD share at least one point -- proper X
    crossings AND collinear overlaps. The endpoint-distance fallback below
    cannot see these (no endpoint touches the other segment), but two
    same-net tracks that cross on one layer ARE electrically joined; a
    first-route board has many such crossings."""
    d1 = _orient(cx, cy, dx_, dy_, ax, ay)
    d2 = _orient(cx, cy, dx_, dy_, bx, by)
    d3 = _orient(ax, ay, bx, by, cx, cy)
    d4 = _orient(ax, ay, bx, by, dx_, dy_)
    if d1 != d2 and d3 != d4:
        return True
    if d1 == 0 and _on_seg(cx, cy, dx_, dy_, ax, ay):
        return True
    if d2 == 0 and _on_seg(cx, cy, dx_, dy_, bx, by):
        return True
    if d3 == 0 and _on_seg(ax, ay, bx, by, cx, cy):
        return True
    if d4 == 0 and _on_seg(ax, ay, bx, by, dx_, dy_):
        return True
    return False


def _bbox_touches_seg(bx0, by0, bx1, by1, ax, ay, bxp, byp, expand):
    """True if segment A-Bp comes within ``expand`` of the rect [bx0,by0,bx1,by1]."""
    bx0 -= expand; by0 -= expand; bx1 += expand; by1 += expand
    # endpoint inside
    if bx0 <= ax <= bx1 and by0 <= ay <= by1:
        return True
    if bx0 <= bxp <= bx1 and by0 <= byp <= by1:
        return True
    # clip segment to box (Liang-Barsky) -> any overlap means it enters the box
    t0, t1 = 0.0, 1.0
    dx = bxp - ax; dy = byp - ay
    for p, q in ((-dx, ax - bx0), (dx, bx1 - ax), (-dy, ay - by0), (dy, by1 - ay)):
        if p == 0:
            if q < 0:
                return False
        else:
            r = q / p
            if p < 0:
                if r > t1:
                    return False
                if r > t0:
                    t0 = r
            else:
                if r < t0:
                    return False
                if r < t1:
                    t1 = r
    return t0 <= t1


def _split_nets(board_path: Path) -> dict[str, int]:
    """Map net name -> number of DISTINCT copper components that hold >=1 pad.

    1 = the net is one connected component (good).  >1 = the net is split: some
    pads are unreachable from others (a half-routed die net, a dangling stub).
    Only non-plane nets are checked (plane nets tie through the pour)."""
    b = pcbnew.LoadBoard(str(board_path))

    # group items by netcode
    by_net: dict[int, list] = {}
    for fp in b.GetFootprints():
        for p in fp.Pads():
            nc = p.GetNetCode()
            if nc <= 0:
                continue
            by_net.setdefault(nc, []).append(("pad", p))
    for t in b.GetTracks():
        nc = t.GetNetCode()
        if nc <= 0:
            continue
        if t.GetClass() == "PCB_VIA":
            by_net.setdefault(nc, []).append(("via", t))
        else:
            by_net.setdefault(nc, []).append(("trk", t))

    netnames: dict[int, str] = {}
    for code, ni in b.GetNetInfo().NetsByNetcode().items():
        netnames[code] = ni.GetNetname()

    split: dict[str, int] = {}
    for nc, items in by_net.items():
        nm = netnames.get(nc, "")
        if not nm or nm in PLANE_NETS:
            continue
        n = len(items)
        uf = _UF(n)
        # pre-extract geometry
        geom = []
        for kind, it in items:
            if kind == "pad":
                pos = it.GetPosition()
                bb = it.GetBoundingBox()
                geom.append({
                    "kind": "pad", "x": pos.x, "y": pos.y,
                    "bx0": bb.GetX(), "by0": bb.GetY(),
                    "bx1": bb.GetX() + bb.GetWidth(),
                    "by1": bb.GetY() + bb.GetHeight(),
                    "layers": _pad_layer_set(it), "r": min(bb.GetWidth(), bb.GetHeight()) // 2,
                })
            elif kind == "via":
                pos = it.GetPosition()
                r = it.GetWidth() // 2
                geom.append({
                    "kind": "via", "x": pos.x, "y": pos.y,
                    "layers": _via_layer_set(b, it), "r": r,
                })
            else:  # trk
                s = it.GetStart(); e = it.GetEnd()
                geom.append({
                    "kind": "trk", "ax": s.x, "ay": s.y, "bx": e.x, "by": e.y,
                    "hw": it.GetWidth() // 2, "layers": {it.GetLayer()},
                })

        for i in range(n):
            gi = geom[i]
            for j in range(i + 1, n):
                gj = geom[j]
                if not _layers_share(gi.get("layers"), gj.get("layers")):
                    continue
                if _touch(gi, gj):
                    uf.union(i, j)

        # count components that contain >=1 PAD
        comp_pads: dict[int, int] = {}
        for i, g in enumerate(geom):
            if g["kind"] != "pad":
                continue
            comp_pads[uf.find(i)] = comp_pads.get(uf.find(i), 0) + 1
        ncomp = len(comp_pads)
        if ncomp > 1:
            split[nm] = ncomp
    return split


def _touch(a, b) -> bool:
    """True if two copper geometries (same/shared layer) physically touch."""
    ta, tb = a["kind"], b["kind"]
    if ta == "trk" and tb == "trk":
        # same layer (already filtered); segments touch if they intersect OR
        # come within the combined half-widths
        if _seg_seg_intersect(a["ax"], a["ay"], a["bx"], a["by"],
                              b["ax"], b["ay"], b["bx"], b["by"]):
            return True
        d2 = _seg_seg_dist2(a["ax"], a["ay"], a["bx"], a["by"],
                            b["ax"], b["ay"], b["bx"], b["by"])
        reach = a["hw"] + b["hw"] + EPS_NM
        return d2 <= reach * reach
    if ta == "via" and tb == "via":
        d2 = (a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2
        reach = a["r"] + b["r"] + EPS_NM
        return d2 <= reach * reach
    if ta == "via" and tb == "trk":
        return _via_trk(a, b)
    if ta == "trk" and tb == "via":
        return _via_trk(b, a)
    if ta == "pad" and tb == "trk":
        return _pad_trk(a, b)
    if ta == "trk" and tb == "pad":
        return _pad_trk(b, a)
    if ta == "pad" and tb == "via":
        return _pad_via(a, b)
    if ta == "via" and tb == "pad":
        return _pad_via(b, a)
    if ta == "pad" and tb == "pad":
        # two pads overlap (stacked / same-net neighbours) -- rare but valid
        return not (a["bx1"] < b["bx0"] or b["bx1"] < a["bx0"] or
                    a["by1"] < b["by0"] or b["by1"] < a["by0"])
    return False


def _via_trk(via, trk) -> bool:
    d2 = _seg_point_dist2(trk["ax"], trk["ay"], trk["bx"], trk["by"], via["x"], via["y"])
    reach = via["r"] + trk["hw"] + EPS_NM
    return d2 <= reach * reach


def _pad_trk(pad, trk) -> bool:
    # endpoint inside the pad bbox (expanded by the track half-width)
    if _bbox_touches_seg(pad["bx0"], pad["by0"], pad["bx1"], pad["by1"],
                         trk["ax"], trk["ay"], trk["bx"], trk["by"], trk["hw"] + EPS_NM):
        return True
    return False


def _pad_via(pad, via) -> bool:
    # via centre inside pad bbox (expanded by via radius), or within pad/via radii
    if (pad["bx0"] - via["r"] - EPS_NM <= via["x"] <= pad["bx1"] + via["r"] + EPS_NM and
            pad["by0"] - via["r"] - EPS_NM <= via["y"] <= pad["by1"] + via["r"] + EPS_NM):
        return True
    d2 = (pad["x"] - via["x"]) ** 2 + (pad["y"] - via["y"]) ** 2
    reach = pad["r"] + via["r"] + EPS_NM
    return d2 <= reach * reach


# keep the weaker dangling check too -- a net with NO copper at all is an even
# worse defect than a split one, and the graph check would also catch it (a
# pad-only net has 1 component per pad), but this names it distinctly.
def _dangling_non_plane(board_path: Path) -> list[str]:
    b = pcbnew.LoadBoard(str(board_path))
    copper_nets: set[int] = set()
    for t in b.GetTracks():
        copper_nets.add(t.GetNetCode())
    for z in b.Zones():
        copper_nets.add(z.GetNetCode())
    netnames: dict[int, str] = {}
    for code, ni in b.GetNetInfo().NetsByNetcode().items():
        netnames[code] = ni.GetNetname()
    pad_nets: set[int] = set()
    for fp in b.GetFootprints():
        for p in fp.Pads():
            pad_nets.add(p.GetNetCode())
    pad_only = {nc for nc in pad_nets if nc > 0 and nc not in copper_nets}
    return sorted(netnames[nc] for nc in pad_only
                  if netnames.get(nc) and netnames[nc] not in PLANE_NETS)


# Known pad-only (dangling, zero-copper) non-plane nets -- explicit, ratcheting
# acceptance. ``mod_sync`` is the FPGA's 1 kHz lock-in modulation reference
# (adc_reader.v drives it as a square wave) that currently has no destination
# pad: the host modulates the microwave drive and the physical MW-gate drive is
# a future addition. HARDWARE_DESIGN.md documents this as the schematic's one
# intentional ``label_dangling`` ("1 label_dangling (mod_sync) -- all benign").
# Any OTHER pad-only net is a netlist defect (a net with a single pad can never
# be connected) and fails here.
KNOWN_DANGLING = {
    "main": {"mod_sync"},
    "head": set(),
}


@pytest.mark.parametrize("name,path", list(BOARDS.items()))
def test_no_dangling_non_plane_nets(name: str, path: Path):
    assert path.exists(), (
        f"{name}: board file {path} is missing -- it is committed source of "
        "truth and must exist"
    )
    dangling = _dangling_non_plane(path)
    unexpected = [n for n in dangling if n not in KNOWN_DANGLING[name]]
    assert not unexpected, (
        f"{name}: {len(unexpected)} non-plane net(s) have NO copper at all "
        f"(pad-only, dangling): {', '.join(unexpected[:20])}. A pad-only net "
        f"can never carry current -- net its partner pad or leave the pad "
        f"unnetted."
    )


# Known split nets -- the explicit, ratcheting acceptance list. This is NOT a
# skip: the graph check runs on every net, and any net that regresses (a new
# split on a previously complete net) FAILS here. Shrink the sets as routing
# completes -- never grow them.
#
# main (1): USB_DM. Upstream b322859 (PR #39) placed 0.20 mm HDI via-in-pads
#   on the 10 trapped interior config/JTAG/SPI/clock BGA balls, closing 8 of
#   the 11 previously split nets (CONF_DONE, SPI_CCLK, SPI_D2, SPI_D3, TDI,
#   TMS, VREFP, clk_100mhz); upstream 5816e5c (pcb/refclk-shorts-fix) then
#   routed refclk_25m to U3.ADF4351 pad 29 and closed em_blank by making its
#   via-in-pad blind F.Cu->In3.Cu. Only the USB-C D- pair tie (USB_DM) remains
#   -- a documented manual touch-up (90-ohm diff pair, hand-routed).
#
# head (29): the die-bus escape re-route is under way. Upstream commit
#   33c2a12 (PR #34, "head board DRC-clean for production") had deleted the
#   In1.Cu + In3.Cu routing of the DET bus as "FreeRouting artifacts"
#   (~1800 tracks + their vias) without re-routing it -- 97 nets regressed
#   from complete to split (129 on this metric: all DET_IN but 10, most
#   DET_TIA, the J12 ADC SPI wires), hidden from `kicad-cli pcb drc` by
#   chip_module.kicad_pro's unconnected_items/track_dangling/via_dangling
#   "ignore" settings. Upstream 2b24f91 (PR #40, FreeRouting cycle 1) re-routed
#   the bus and closed 100 of the 129 splits; the 15 DET_IN + 14 DET_TIA nets
#   below remain. This list documents that state; every entry must disappear
#   again as the bus is re-routed.
KNOWN_SPLIT = {
    "main": {
        "USB_DM",
    },
    "head": {
        "DET_IN15", "DET_IN21", "DET_IN28", "DET_IN29", "DET_IN3", "DET_IN30",
        "DET_IN36", "DET_IN38", "DET_IN42", "DET_IN43", "DET_IN45", "DET_IN48",
        "DET_IN59", "DET_IN7", "DET_IN9",
        "DET_TIA10", "DET_TIA11", "DET_TIA19", "DET_TIA20", "DET_TIA30",
        "DET_TIA34", "DET_TIA4", "DET_TIA47", "DET_TIA50", "DET_TIA52",
        "DET_TIA54", "DET_TIA56", "DET_TIA6", "DET_TIA8",
    },
}


@pytest.mark.parametrize("name,path", list(BOARDS.items()))
def test_nets_one_component(name: str, path: Path):
    """Every non-plane net's pads must sit in a single connected copper
    component -- a split net is a half-routed connection (the die-bus
    dog-bone via that never closed to its TLC5947/TIA target). Nets on
    ``KNOWN_SPLIT[name]`` are the committed, explicitly accepted incomplete
    routes; any split OUTSIDE that set is a regression and fails."""
    assert path.exists(), (
        f"{name}: board file {path} is missing -- it is committed source of "
        "truth and must exist"
    )
    split = _split_nets(path)
    unexpected = {k: v for k, v in split.items() if k not in KNOWN_SPLIT[name]}
    stale = sorted(set(KNOWN_SPLIT[name]) - set(split))
    sample = ", ".join(f"{k}({v})" for k, v in sorted(unexpected.items())[:20])
    assert not unexpected, (
        f"{name}: {len(unexpected)} non-plane net(s) are split across >1 "
        f"component and are NOT on the accepted KNOWN_SPLIT list: {sample}. "
        f"If this net was ALWAYS split, add it to KNOWN_SPLIT with a comment; "
        f"if it used to be complete, this is a routing regression."
    )
    assert not stale, (
        f"{name}: net(s) {stale} are on the KNOWN_SPLIT acceptance list but "
        f"are now fully connected -- shrink KNOWN_SPLIT (the ratchet only "
        f"tightens)."
    )