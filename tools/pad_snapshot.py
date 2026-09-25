"""Plain-python pad / footprint snapshots for the router tools.

The pcbnew swig getters (``GetPosition``, ``GetBoundingBox``, ``GetLayerSet``)
resolve only while the underlying board object is alive and unmutated; once the
router starts clearing/adding tracks the swig wrappers can dereference stale
state. :func:`capture_pads` snapshots every footprint's pads into the proxy
classes here (``V``, ``Box``, ``PadProxy``, ``FpProxy``) so the router works
against stable plain-python geometry for the rest of the run.
"""
from __future__ import annotations

import pcbnew


class V:
    """2D point proxy (mimics pcbnew.VECTOR2I .x/.y)."""
    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x = x
        self.y = y


class Box:
    """Bbox proxy (mimics pcbnew GetBoundingBox .GetX/.GetY/.GetWidth/.GetHeight)."""
    __slots__ = ("_x", "_y", "_w", "_h")

    def __init__(self, x, y, w, h):
        self._x = x
        self._y = y
        self._w = w
        self._h = h

    def GetX(self):
        return self._x

    def GetY(self):
        return self._y

    def GetWidth(self):
        return self._w

    def GetHeight(self):
        return self._h


class PadProxy:
    """Plain-python snapshot of one pad's geometry + net."""
    __slots__ = ("cx", "cy", "bx", "by", "bw", "bh",
                 "netname", "netcode", "is_pth", "layer", "attrib", "has_hole",
                 "cu_layers")

    def __init__(self, cx, cy, bx, by, bw, bh,
                 netname, netcode, is_pth, layer, attrib, has_hole,
                 cu_layers=None):
        self.cx = cx
        self.cy = cy
        self.bx = bx
        self.by = by
        self.bw = bw
        self.bh = bh
        self.netname = netname
        self.netcode = netcode
        self.is_pth = is_pth
        self.layer = layer
        self.attrib = attrib
        self.has_hole = has_hole
        # REAL copper layers the pad sits on (from GetLayerSet, filtered to
        # copper 0..31). GetLayer() returns the FRONT canvas layer (0) for a pad
        # on a FLIPPED (back-side) footprint, so it is WRONG for the head board's
        # B.Cu die pads -- use cu_layers instead. Default [layer] for synthetic
        # pads (via_pads) where the caller passes the intended single layer.
        self.cu_layers = cu_layers if cu_layers is not None else [layer]

    def GetCenter(self):
        return V(self.cx, self.cy)

    def GetPosition(self):
        return V(self.cx, self.cy)

    def GetNetname(self):
        return self.netname

    def GetNetCode(self):
        return self.netcode

    def GetAttribute(self):
        # Return the REAL pad attribute, not a synthesised 0/PTH. In KiCad 8
        # PAD_ATTRIB_PTH == 0 and PAD_ATTRIB_SMD == 1, so returning 0 for a
        # non-PTH pad would make _pad_layers treat every SMD pad as through-hole
        # and mark it on ALL four signal layers -- blocking the inner layers
        # with surface-pad keepouts that should not be there (the bug that
        # defeated inner-layer power routing: an SMD diode's pad blocked In3).
        return self.attrib

    def GetLayer(self):
        return self.layer

    def GetBoundingBox(self):
        return Box(self.bx, self.by, self.bw, self.bh)


class FpProxy:
    """Plain-python snapshot of one footprint (reference + position + pad
    snapshots).  Position is the footprint origin (die body centre for the
    QSSC_Chip, the BGA array centre for the FPGA) -- captured BEFORE track
    clearing so swig still resolves GetPosition()."""
    __slots__ = ("ref", "pads", "x", "y", "flayer")

    def __init__(self, ref, pads, x=0, y=0, flayer=0):
        self.ref = ref
        self.pads = pads
        self.x = x
        self.y = y
        self.flayer = flayer       # footprint's own copper face (F_CU / B_CU)

    def GetReference(self):
        return self.ref

    def Pads(self):
        return self.pads


def capture_pads(board):
    """Snapshot every footprint's pads into plain-python proxies BEFORE the
    board is mutated."""
    fps = []
    for fp in board.GetFootprints():
        pads = []
        fpos = fp.GetPosition()
        for p in fp.Pads():
            pos = p.GetPosition()
            bb = p.GetBoundingBox()
            # real copper layers from the layer set (GetLayer() is wrong for
            # pads on a flipped/back-side footprint -- it returns F.Cu).
            cu_layers = [L for L in p.GetLayerSet().Seq() if L <= 31]
            pads.append(PadProxy(
                cx=pos.x, cy=pos.y,
                bx=bb.GetX(), by=bb.GetY(), bw=bb.GetWidth(), bh=bb.GetHeight(),
                netname=p.GetNetname(), netcode=p.GetNetCode(),
                is_pth=(p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH),
                layer=p.GetLayer(),
                attrib=p.GetAttribute(),
                has_hole=(p.GetDrillSize().x > 0),
                cu_layers=cu_layers or [p.GetLayer()],
            ))
        fps.append(FpProxy(fp.GetReference(), pads, fpos.x, fpos.y,
                            fp.GetLayer()))
    return fps