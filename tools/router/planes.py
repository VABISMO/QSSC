"""The copper-plane foundation builder (GND/+3V3 pours, rail-pad vias, GND
stitching vias, isolated straggler-power ties) for the QSSC PCB.

:class:`PlaneBuilder` holds the board + the pre-mutation obstacle snapshots
(signal circles/segments, rail pads, surviving via centres, tie traces) that
the via-placement passes share, replacing the mutable-list state that was
threaded through the original monolithic ``build()``. The rail-pad / stitching
/ straggler via-placement methods live on :class:`tools.router.rails.RailTieMixin`
(inherited here, so they stay methods of the builder); the rail/straggler
stateless helpers also live in :mod:`tools.router.rails`.

Layer IDs, via/track sizes and ``track_width_nm`` come from
:mod:`tools.route_qssc_pcb` (the config home the whole ``router`` package
imports from), preserving the existing import direction and avoiding a cycle.
"""
from __future__ import annotations

import os

import pcbnew

from route_qssc_pcb import IN1_CU, track_width_nm, VIP_MAX_NM
from .rails import (
    RailTieMixin, _is_bga, STRAGGLER_POWER_NETS,
    _straggler_obstacles, _detect_straggler_pads,
)


def _capture_rail_pads(board):
    """Snapshot every pad's (centre, netcode, attribute) and the BGA pad centres
    BEFORE any board mutation. KiCad's swig binding returns a bare non-iterable
    SwigPyObject for ``board.GetFootprints()``/``Footprints()`` once
    ``board.Remove()`` runs in ``_clear_routed``, so ``_add_rail_pad_vias``
    cannot iterate footprints after the reset. Returns
    (bga_xy_set, {netcode: [(x, y, attribute), ...]}) for non-BGA pads only.

    A pad is skipped only on a real BGA (``_is_bga``), whose GND/+3V3 balls
    already carry via-in-pad from ``route_signals._bga_fanout`` -- not by
    reference designator, which collides between the boards (U1 = FPGA BGA on
    the main board but a QFN MUX on the chip module)."""
    bga_xy = set()
    by_net = {}
    for fp in board.GetFootprints():
        is_bga = _is_bga(fp)
        for pad in fp.Pads():
            c = pad.GetCenter()
            x, y = int(c.x), int(c.y)
            if is_bga:
                bga_xy.add((x, y))
                continue
            sz = pad.GetSize()
            by_net.setdefault(pad.GetNetCode(), []).append(
                (x, y, pad.GetAttribute(), int(sz.x), int(sz.y)))
    return bga_xy, by_net


def _find_netcode(board, name):
    for code, net in board.GetNetInfo().NetsByNetcode().items():
        if net.GetNetname() == name:
            return code
    raise KeyError(f"net {name!r} not in board netlist")


def _find_plane_layer(board, label):
    """Return the layer ID of the copper layer whose user label == `label`
    ("GND" or "PWR"), or None. The main board labels In1.Cu="GND" and
    In4.Cu="PWR"; the chip module labels In1.Cu="GND" and In2.Cu="PWR". Avoids
    CuStack() (a bare SwigPyObject in this build after board mutation).

    Iterate the full layer-ID range filtered to copper layers: KiCad >= 10
    interleaves tech layers with copper, so raw copper IDs are no longer
    contiguous (F.Cu=0, B.Cu=2, In1.Cu=4, In2.Cu=6, ...)."""
    for lid in range(pcbnew.PCB_LAYER_ID_COUNT):
        if not pcbnew.IsCopperLayer(lid):
            continue
        try:
            if board.GetLayerName(lid) == label:
                return lid
        except Exception:
            pass
    return None


def _clear_routed(board):
    """Idempotent reset of the plane foundation: remove every zone (planes) and
    every WIDE GND/+3V3 track/via this script owns (the stitching mesh + rail
    pad vias), but KEEP small BGA via-in-pad VIPs (<= VIP_MAX_NM, owned by
    route_signals._bga_fanout) and all non-plane signal/power tracks/vias so a
    prior ``route_signals.py`` pass is preserved when the plane foundation is
    (re)built on top of it.

    The swig track proxy is iterable by for-loop but ``list()`` of it can raise
    ``SwigPyObject not iterable`` after the board is mutated, so we collect
    references via comprehensions (which iterate, unlike ``list()``) first.
    """
    zones = [z for z in board.Zones()]
    for z in zones:
        board.Remove(z)
    tracks = [t for t in board.Tracks()]
    for t in tracks:
        nm = t.GetNetname()
        if nm not in ("GND", "+3V3"):
            continue
        if isinstance(t, pcbnew.PCB_VIA):
            # strip only this script's own WIDE vias (0.6 mm stitching / rail-pad);
            # KEEP the <= VIP_MAX_NM via-in-pad VIPs owned by route_signals
            # (GND 0.2 mm microvias, +3V3 0.3 mm THRU vias on the BGA).
            if track_width_nm(t) > VIP_MAX_NM:
                board.Remove(t)
        else:
            # PCB_TRACK: every GND/+3V3 tie trace is THIS script's (route_signals
            # routes no GND/+3V3 tracks -- those nets are inner planes + BGA
            # via-in-pad), so strip a tie trace at ANY width -- including the
            # 0.2 mm dense-connector trace, which the > VIP_MAX_NM width test
            # would leave behind as a ghost obstacle on a re-run.
            board.Remove(t)


def _add_gnd_or_pwr_zone(board, layer, netcode, bbox):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNetCode(netcode)
    z.SetAssignedPriority(0)
    # 0.20 mm pour clearance (HDI-appropriate; the 0.5 mm board default starves
    # dense via fields -- two routing vias <0.35 mm apart can't keep a 0.5 mm
    # pullback on both sides, so the plane edges trip clearance DRC). 0.20 mm is
    # twice the 0.10 mm board min_clearance and pours cleanly around 0.20 mm vias.
    # QSSC_ZONE_CLEARANCE_NM / QSSC_ZONE_MIN_WIDTH_NM: pour overrides for dense
    # boards (defaults keep the 0.20 mm above and KiCad's 0.25 mm min width).
    # A 0.15 mm clearance (>= every netclass on both boards) plus a 0.10 mm min
    # width lets 0.10 mm necks survive between dense via fields, so the plane
    # stops shredding into stranded fragments (DRC unconnected Zone/Zone pairs).
    # DRC (unconnected=error) remains the judge.
    z.SetLocalClearance(int(os.environ.get("QSSC_ZONE_CLEARANCE_NM", 200000)))
    _minw = os.environ.get("QSSC_ZONE_MIN_WIDTH_NM")
    if _minw:
        z.SetMinThickness(int(_minw))
    # solid (full) pad connection -- these are plane/flood pours, so we want a
    # direct copper connection, not thermal-relief spokes that can starve at
    # edge/adjacent pads and trip the starved_thermal DRC rule.
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    x0, y0 = bbox.GetX(), bbox.GetY()
    x1, y1 = x0 + bbox.GetWidth(), y0 + bbox.GetHeight()
    for cx, cy in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]:
        # AppendCorner(corner, holeIdx); holeIdx = -1 for the main outline
        z.AppendCorner(pcbnew.VECTOR2I(int(cx), int(cy)), -1)
    board.Add(z)
    return z


def _pad_obstacle(board):
    """Return a list of (cx, cy, hw, hh, netcode) pad RECTANGLES for
    via/trace-clearance checks -- each pad's centre plus the half-extents (hw,
    hh) of its copper (GetSize), used by _rect_dist2 / _rect_seg_dist2.

    The rectangle model is what makes dense 0.5 mm-pitch fanout viable: a
    0.25 x 0.88 mm QFN lead is 0.125 mm off its narrow side but 0.44 mm off its
    long side, and a via 0.58 mm perpendicular to the lead (which clears the
    0.25 mm side by 0.45 mm) was FALSely blocked by the earlier circle of the
    pad's half-diagonal (a 0.46 mm "radius" reaching 0.33 mm past the narrow
    side). The rectangle reaches only 0.125 mm off the narrow side, so that
    same via clears it by 0.45 mm and places. (Accurate for 0/90-degree pads,
    the common case; the bbox is a slight over-estimate for a round/oval pad,
    which is safe.)

    Only pads that actually carry COPPER on one of the board's copper layers
    are included: some footprints carry non-copper "pads" (courtyard /
    documentation / paste-only geometry with an empty copper layer-set, e.g. the
    16 unnetted interior cells inside the chip-module MUX's exposed pad) which
    do not short to anything and must NOT block a via -- counting them would
    falsely forbid a via-in-pad at a real copper pad they merely overlap."""
    cu_ids = set(range(board.GetCopperLayerCount() - 1)) | {pcbnew.B_Cu}
    pads = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            ls = pad.GetLayerSet()
            if not any(ls.Contains(l) for l in cu_ids):
                continue  # non-copper pad -- not a real obstacle
            pos = pad.GetCenter()
            sz = pad.GetSize()
            is_pth = pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH
            pads.append((pos.x, pos.y, int(sz.x) / 2, int(sz.y) / 2,
                         pad.GetNetCode(), is_pth))
    return pads


def _signal_obstacles(board):
    """Return (circles, segs, segs_fcu) for SIGNAL copper already on the board --
    vias as (cx, cy, r) circles and tracks as (x1, y1, x2, y2, hw) segments. GND
    and unnetted copper are skipped: a GND stitching via on a GND track is fine,
    and unnetted copper has no net to short. Used so the stitching-via grid never
    pierces a signal track/via (the original pad-only check shorted ~20 nets).

    ``segs`` is every signal track on every layer (a through via pierces all
    layers, so the via check must see inner-layer tracks too). ``segs_fcu`` is
    the F.Cu-only subset: the F.Cu tie trace only touches F.Cu, so checking it
    against an In3.Cu track it physically cannot reach would falsely block it --
    the trace check uses segs_fcu, the via check uses segs. ``segs_in1`` is the
    In1.Cu-only subset: the GND plane's own layer -- a microvia-in-pad fallback
    landing on In1 must clear detector-bus tracks riding the plane layer, while
    In3/In4/B.Cu tracks (which a F.Cu->In1 micro never touches) must NOT block
    it."""
    circles = []
    segs = []
    segs_fcu = []
    segs_in1 = []
    for t in board.Tracks():
        nm = t.GetNetname()
        # Skip GND AND +3V3: both are this router's domain (the two planes it
        # rebuilds + the tie traces it owns). route_signals never routes them
        # (they are plane nets), so every GND/+3V3 track or via on the board is
        # a leftover from a PREVIOUS run of this script. They are captured HERE,
        # before _clear_routed strips them, so keeping them as "signal" obstacles
        # would make the next pass block its own replacement copper -- a ghost
        # obstacle that oscillates the +3V3 via count 0/40/0/40 across runs.
        if not nm or nm in ("GND", "+3V3"):
            continue
        if isinstance(t, pcbnew.PCB_VIA):
            c = t.GetPosition()
            circles.append((c.x, c.y, track_width_nm(t) / 2))
        else:
            s = t.GetStart()
            e = t.GetEnd()
            hw = track_width_nm(t) / 2
            seg = (s.x, s.y, e.x, e.y, hw)
            segs.append(seg)
            if t.GetLayer() == pcbnew.F_Cu:
                segs_fcu.append(seg)
            if t.GetLayer() == pcbnew.In1_Cu:
                segs_in1.append(seg)
    return circles, segs, segs_fcu, segs_in1


def _surviving_via_centers(board):
    """(x, y, netcode) of every via that ``_clear_routed`` will KEEP -- i.e. all
    non-GND/+3V3 routing vias plus the small GND/+3V3 BGA via-in-pad VIPs (width
    <= VIP_MAX_NM). The wide GND/+3V3 stitching/rail vias this script owns are
    removed by ``_clear_routed`` and so are excluded. Must be snapshotted BEFORE
    ``_clear_routed`` mutates the board (board.Tracks() returns a broken swig
    proxy afterwards). Used for via-to-via spacing: SAME-net vias need only
    drill spacing (0.6 mm centre); DIFFERENT-net vias need copper clearance
    (0.8 mm centre for the 0.15 mm Power netclass), so the netcode is carried."""
    centres = []
    for t in board.Tracks():
        if not isinstance(t, pcbnew.PCB_VIA):
            continue
        nm = t.GetNetname()
        if nm in ("GND", "+3V3") and track_width_nm(t) > VIP_MAX_NM:
            continue  # this script's own wide via -- removed by _clear_routed
        c = t.GetPosition()
        centres.append((c.x, c.y, t.GetNetCode()))
    return centres


class PlaneBuilder(RailTieMixin):
    """Add the copper-plane foundation (GND/+3V3 pours + rail-pad vias + GND
    stitching vias + isolated straggler-power ties) to an already-loaded board.

    Holds the board plus the pre-mutation obstacle snapshots the via-placement
    passes share (signal circles/segments, rail pads, surviving via centres, tie
    traces, straggler obstacles) as instance state, replacing the mutable lists
    that were threaded through every call in the original monolithic ``build``.
    The via-placement methods themselves are on :class:`RailTieMixin`.
    """

    def __init__(self, board, gnd, p33, bbox, pads):
        self.board = board
        self.gnd = gnd
        self.p33 = p33
        self.bbox = bbox
        self.pads = pads
        # Populated by run() before _clear_routed (the swig proxy layer breaks
        # once board.Remove() runs, so these must be snapshotted up front).
        self.sig_circles = []
        self.sig_segs = []
        self.sig_segs_fcu = []
        self.sig_segs_in1 = []
        self.bga_xy = set()
        self.rail_pads = {}
        # Mutable lists threaded through every via-placement pass: each pass
        # appends its placed vias / tie traces so later passes keep spacing /
        # copper gap from earlier ones.
        self.via_centers = []
        self.tie_segs = []
        self.straggler_obs = ([], [])
        self.zones = []
        self.vias = []

    def run(self, fill_zones=True) -> dict:
        """Build the plane foundation and return a stats dict. ``fill_zones=False``
        skips the inline zone fill (on KiCad 10.x ``ZONE_FILLER.Fill`` segfaults
        in a process that just added hundreds of tracks/vias via the swig layer,
        so ``main`` fills in a fresh ``--fill`` subprocess after saving)."""
        board = self.board
        # Capture signal tracks/vias AND footprint pads BEFORE _clear_routed
        # mutates the board: the swig proxy layer breaks (board.Tracks()/
        # GetFootprints() return bare SwigPyObjects) once board.Remove() runs in
        # _clear_routed, so the via grids + rail-pad vias must be built from
        # obstacles snapshotted up front. _clear_routed keeps signal copper and
        # the small BGA VIPs (only removes zones + its own wide GND/+3V3 vias).
        (self.sig_circles, self.sig_segs,
         self.sig_segs_fcu, self.sig_segs_in1) = _signal_obstacles(board)
        self.bga_xy, self.rail_pads = _capture_rail_pads(board)
        # Surviving via centres (BGA VIPs + signal routing vias) snapshotted
        # BEFORE _clear_routed -- used for drill-to-drill spacing. Mutable list
        # threaded through every via-placement pass: each appends its placed vias
        # so later passes (+3V3 rail, then stitching) keep spacing from earlier.
        self.via_centers = _surviving_via_centers(board)
        self.tie_segs = []
        # Straggler power pads (e.g. +5V connector pins the line router could not
        # tie -> module gets no power): snapshot obstacles + detect the ISOLATED
        # pads BEFORE _clear_routed (board.Tracks() / BuildConnectivity need the
        # intact board; a re-run skips a pad a prior run already tied ->
        # idempotent).
        straggler_nc = {}
        for nm in STRAGGLER_POWER_NETS:
            try:
                straggler_nc[_find_netcode(board, nm)] = nm
            except KeyError:
                pass
        self.straggler_obs = _straggler_obstacles(board) if straggler_nc else ([], [])
        straggler_pads = (_detect_straggler_pads(board, set(straggler_nc),
                                                  self.bga_xy)
                          if straggler_nc else {})
        _clear_routed(board)

        # Detect the plane layers from the board's layer labels (works for both
        # the main board In4.Cu="PWR" and the chip module In2.Cu="PWR").
        gnd_layer = _find_plane_layer(board, "GND")
        pwr_layer = _find_plane_layer(board, "PWR")
        if gnd_layer is None:
            gnd_layer = IN1_CU   # fallback

        self.zones = [_add_gnd_or_pwr_zone(board, gnd_layer, self.gnd, self.bbox)]
        if self.p33 is not None and pwr_layer is not None:
            self.zones.append(_add_gnd_or_pwr_zone(board, pwr_layer, self.p33,
                                                   self.bbox))

        # Rail pad vias: tie every surface GND / +3V3 SMD pad (not on the BGA) to
        # its inner plane. Added BEFORE filling so the +3V3 plane carves anti-pad
        # clearances around each GND via barrel. via_centers and tie_segs are
        # mutated: the GND pass's placed vias/traces are seen by the +3V3 pass.
        vias = list(self._add_rail_pad_vias(self.gnd))
        if self.p33 is not None and pwr_layer is not None:
            vias += list(self._add_rail_pad_vias(self.p33))

        # GND stitching vias across the ground mesh (coarse grid of return vias
        # tying the plane to the surface layers for signal-integrity returns).
        vias += self._add_stitching_vias()

        # Tie isolated straggler power pads (e.g. +5V J11.57 / J1.57) to their
        # rail: a thin 0.2 mm F.Cu stub -> 0.6 mm via (the rail-pad spiral, which
        # fits the 0.5 mm connector pitch the line router's 0.3 mm keepout ring
        # cannot), then a 0.2 mm B.Cu L-trace from that via to the nearest
        # existing same-net rail copper. Run before zone fill so the GND/+3V3
        # planes carve anti-pads around the new through-via barrels.
        straggler_vias = []
        straggler_tracks = 0
        straggler_summary = []
        for nc, nm in straggler_nc.items():
            iso = straggler_pads.get(nc, [])
            if not iso:
                continue
            n_before = len(self.via_centers)
            placed = list(self._add_rail_pad_vias(nc, rail_pads={nc: iso}))
            straggler_vias.extend(placed)
            nt2 = self._tie_stragglers_to_rail(nc, self.via_centers[n_before:],
                                               self.via_centers)
            straggler_tracks += nt2
            straggler_summary.append(f"{nm}:{len(placed)}via/{nt2}trk")
        if straggler_vias:
            print(f"  straggler power tie: {len(straggler_vias)} via(s), "
                  f"{straggler_tracks} B.Cu tie track(s) "
                  f"({', '.join(straggler_summary)})")
        vias += straggler_vias
        self.vias = vias

        # fill all zones now that vias exist (ZONE_FILLER.Fill takes a vector)
        if fill_zones:
            filler = pcbnew.ZONE_FILLER(board)
            vec = pcbnew.ZONES()
            for z in self.zones:
                vec.append(z)
            filler.Fill(vec, False, None)

        # stats, built from the in-memory objects we hold (the zone/via proxies
        # we created stay valid; re-loading the board after mutation is
        # unreliable in this swig build).
        return {
            "zones": [
                {
                    "layer_id": z.GetFirstLayer(),
                    "layer": board.GetLayerName(z.GetFirstLayer()),   # user name
                    "net": z.GetNetname(),
                    "filled_mm2": z.GetFilledArea() / 1e12,   # nm^2 -> mm^2
                }
                for z in self.zones
            ],
            "via_count": len(vias),
            "via_nets": {v.GetNetname() for v in vias},
        }