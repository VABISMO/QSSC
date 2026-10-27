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

Skips where ``pcbnew`` (system KiCad) is not importable (CI / .venv).
"""
from __future__ import annotations

from pathlib import Path

import pytest

pcbnew = pytest.importorskip("pcbnew")

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

    NB: ``pad.GetLayer()`` is NOT authoritative for SMD pads in KiCad 8 -- it
    returns F_Cu for a B.Cu SMD pad (the pad's real layer set is
    [B.Cu, B.Mask], and IsOnLayer(B_Cu) is True, but GetLayer() defaults to the
    front copper).  Using GetLayer() here makes the union-find place every B.Cu
    detector-die pad on F.Cu, so it never shares a layer with its B.Cu escape
    stub -> the net is falsely reported "split" even though it is routed
    end-to-end.  Derive the copper layer set from ``GetLayerSet()`` instead,
    keeping only real copper layers (layer id <= B_Cu; masks/silk are all >B_Cu)."""
    drill = pad.GetDrillSize()
    if drill.x > 0 or drill.y > 0:
        return None          # sentinel: all copper layers
    copper = [l for l in pad.GetLayerSet().Seq() if l <= pcbnew.B_Cu]
    return set(copper) if copper else {pad.GetLayer()}


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
        # same layer (already filtered); segments touch if within combined half-widths
        d2 = _seg_seg_dist2(a["ax"], a["ay"], a["bx"], a["by"],
                            b["ax"], b["ay"], b["bx"], b["by"])
        reach = a["hw"] + b["hw"] + EPS_NM
        return d2 <= reach * reach
    if ta == "via" and tb == "via":
        # KiCad's connectivity is anchor-based: two vias are one group only
        # when a centre lies inside the other's barrel copper.  Copper-tangent
        # vias (d == rA + rB) read as connected under the naive copper-overlap
        # test, but DRC reports them unconnected -- the closer then skipped
        # such pairs forever (e.g. the two 0.6 mm edge vias at 0.6 mm pitch).
        d2 = (a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2
        reach = max(a["r"], b["r"]) + EPS_NM
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


@pytest.mark.parametrize("name,path", list(BOARDS.items()))
def test_no_dangling_non_plane_nets(name: str, path: Path):
    if not path.exists():
        pytest.skip(f"{path} not present")
    dangling = _dangling_non_plane(path)
    sample = ", ".join(dangling[:20]) + (" ..." if len(dangling) > 20 else "")
    assert dangling == [], (
        f"{name}: {len(dangling)} non-plane net(s) have NO copper at all "
        f"(pad-only, dangling): {sample}"
    )


@pytest.mark.parametrize("name,path", list(BOARDS.items()))
def test_nets_one_component(name: str, path: Path):
    """Every non-plane net's pads must sit in a single connected copper
    component -- a split net is a half-routed connection (the die-bus
    dog-bone via that never closed to its TLC5947/TIA target)."""
    if not path.exists():
        pytest.skip(f"{path} not present")
    split = _split_nets(path)
    sample = ", ".join(f"{k}({v})" for k, v in sorted(split.items())[:20])
    sample += " ..." if len(split) > 20 else ""
    assert split == {}, (
        f"{name}: {len(split)} non-plane net(s) are split across >1 copper "
        f"component (pads not all connected): {sample}"
    )