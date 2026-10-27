"""Rail-pad / straggler-power helpers + the rail/stitching/straggler via-placement
methods for the copper-plane builder.

The stateless helpers (BGA detection, the pre-mutation rail-pad snapshot, the
isolated straggler-power-pad tie primitives) are module-level functions.
:class:`RailTieMixin` carries the three via-placement methods that
:class:`tools.router.planes.PlaneBuilder` inherits -- split out so ``planes.py``
stays under the readability threshold while keeping the methods ON the builder
(they read the obstacle snapshots ``PlaneBuilder.__init__`` / ``run`` populate).
Geometry is shared from :mod:`tools.geom`; via/track sizes and
``track_width_nm`` come from :mod:`tools.route_qssc_pcb` (the config home the
whole ``router`` package imports from).
"""
from __future__ import annotations

import os

import pcbnew

from geom import (
    seg_dist2, seg_seg_dist2, pt_rect_center_dist2, seg_rect_center_dist2,
)
from route_qssc_pcb import (  # noqa: F401 (some used only in RailTieMixin)
    track_width_nm, VIA_DIAM_NM, VIA_DRILL_NM, COPPER_GAP_NM, VIP_MIN_NM,
    RAIL_VIA_OFFSET_NM, RAIL_TRACK_NM, RAIL_TRACK_THIN_NM, RAIL_TRACK_THIN_MAX,
    DRILL_MIN_CENTER_NM, GRID_STEP_NM,
)
from .config import BGA_VIA_DIAM_NM, BGA_VIA_DRILL_NM

# Geometry helpers under the names the original monolith used.
_seg_dist2 = seg_dist2
_seg_seg_dist2 = seg_seg_dist2
_rect_dist2 = pt_rect_center_dist2
_rect_seg_dist2 = seg_rect_center_dist2


def _is_bga(fp):
    """True if the footprint is a ball-grid array -- i.e. it has many INTERIOR
    pads (pads whose centres sit well off the footprint perimeter). This is what
    distinguishes the FPGA BGA on the main board (236 balls, ~150 interior) from
    the QFN MUXes on the chip module (perimeter pads + at most one centre thermal
    pad), which ``BGA_REF="U1"`` alone cannot -- on the chip module U1 is a QFN
    MUX, not the FPGA, so a reference-designator match would wrongly skip its
    power/ground pads and leave the MUXes dead. Uses a 30% edge margin (a BGA's
    central balls sit >30% in from every edge; a QFN/LFCSP's perimeter leads and
    single centre thermal pad do not) and requires >= 10 such deep-interior pads,
    so a 42-pad LFCSP (2 interior) and a 65-pad QFN (5 interior) are never mistaken
    for the 236-ball FPGA BGA (46 interior)."""
    pads = list(fp.Pads())
    if len(pads) < 20:
        return False
    xs = [p.GetCenter().x for p in pads]
    ys = [p.GetCenter().y for p in pads]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    mx = (xmax - xmin) * 0.30
    my = (ymax - ymin) * 0.30
    n_interior = 0
    for p in pads:
        c = p.GetCenter()
        if (xmin + mx < c.x < xmax - mx) and (ymin + my < c.y < ymax - my):
            n_interior += 1
            if n_interior >= 10:
                return True
    return False


# Non-plane power nets whose dense SMT connector pins the line router
# (route_signals) often cannot tie -- a 0.5 mm pitch field defeats its 0.3 mm
# keepout ring, so e.g. J11.57 / J1.57 (+5V main->module power) end up ISOLATED
# from the +5V rail and the module gets no power. This router's 0.16 mm
# copper-gap rectangle model CAN fit a 0.2 mm fanout there, so it ties the
# isolated stragglers: thin F.Cu stub pad->via (reuses _add_rail_pad_vias)
# then a thin B.Cu L-trace via->nearest same-net rail copper. Idempotent: the
# pad is detected ISOLATED via connectivity, so a re-run (which does NOT strip
# +5V) skips an already-tied pad; a full pipeline re-run has route_signals
# strip+rebuild the +5V rail first, so this re-ties.
STRAGGLER_POWER_NETS = ("+5V", "+5V_RF", "+5V_IN", "+12V_EMIT", "+12V_EMIT_IN")


def _seg_nearest(px, py, x1, y1, x2, y2):
    """Squared distance from point (px,py) to segment (x1,y1)-(x2,y2) and the
    nearest point on that segment, in nm."""
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return (px - x1) ** 2 + (py - y1) ** 2, (x1, y1)
    tt = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    if tt < 0:
        tt = 0
    elif tt > 1:
        tt = 1
    cx = x1 + tt * dx
    cy = y1 + tt * dy
    return (px - cx) ** 2 + (py - cy) ** 2, (cx, cy)


def _straggler_obstacles(board):
    """Snapshot (segs, circles) carrying netcode (+ layer for tracks) for EVERY
    track/via on the board, taken BEFORE _clear_routed (board.Tracks() is only
    iterable then). The B.Cu straggler tie trace must reach the existing same-net
    rail, so the netcode lets the clearance check SKIP same-net copper -- the
    GND/+3V3 rail-pass obstacles (sig_segs/sig_circles, no netcode) cannot, so
    this richer snapshot is kept separate and only used by the straggler pass."""
    segs = []     # (x1, y1, x2, y2, hw, netcode, layer)
    circles = []  # (cx, cy, r, netcode)
    for t in board.Tracks():
        nc = t.GetNetCode()
        if isinstance(t, pcbnew.PCB_VIA):
            c = t.GetPosition()
            circles.append((c.x, c.y, track_width_nm(t) / 2, nc))
        else:
            s = t.GetStart()
            e = t.GetEnd()
            segs.append((s.x, s.y, e.x, e.y, track_width_nm(t) / 2, nc, t.GetLayer()))
    return segs, circles


def _detect_straggler_pads(board, netcodes_set, bga_xy):
    """{netcode: [(x,y,attr,sx,sy),...]} of SMD, non-BGA pads on each straggler
    power net that are ISOLATED -- no same-net track/via touches them (the line
    router could not tie them). Uses BuildConnectivity on the intact board
    (before _clear_routed). Pads already tied by a prior run of this pass are
    NOT isolated, so they are skipped -- the idempotency for a route_qssc_pcb
    -only re-run."""
    board.BuildConnectivity()
    conn = board.GetConnectivity()
    out = {}
    for fp in board.GetFootprints():
        is_bga = _is_bga(fp)
        for pad in fp.Pads():
            nc = pad.GetNetCode()
            if nc not in netcodes_set:
                continue
            if is_bga:
                continue
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_PTH:
                continue
            if len(list(conn.GetConnectedTracks(pad))) == 0:
                c = pad.GetCenter()
                sz = pad.GetSize()
                out.setdefault(nc, []).append(
                    (c.x, c.y, pad.GetAttribute(), int(sz.x), int(sz.y)))
    return out


def _nearest_same_net_point(vx, vy, netcode, obs_segs, obs_circles):
    """Nearest (x, y) point of existing same-net copper (a via centre or the
    nearest point on a same-net track) to (vx, vy), or None."""
    best_d2 = float("inf")
    best_pt = None
    for sx1, sy1, sx2, sy2, shw, snc, slay in obs_segs:
        if snc != netcode:
            continue
        d2, pt = _seg_nearest(vx, vy, sx1, sy1, sx2, sy2)
        if d2 < best_d2:
            best_d2, best_pt = d2, pt
    for cx, cy, cr, cnc in obs_circles:
        if cnc != netcode:
            continue
        d2 = (vx - cx) ** 2 + (vy - cy) ** 2
        if d2 < best_d2:
            best_d2, best_pt = d2, (cx, cy)
    return best_pt


def _bcu_seg_clear(x1, y1, x2, y2, skip_nc, obs_segs, obs_circles, hw, gap,
                   placed_vias=None):
    """True if a B.Cu trace segment clears every OTHER-net B.Cu track and
    through-via barrel by >= gap. Same-net (skip_nc) copper may be touched (the
    tie trace ends ON the rail). Through-via barrels sit on B.Cu regardless of
    their own layer span, so all non-same-net vias are obstacles (conservative
    for a microvia that does not reach B.Cu -- only over-blocks, never shorts).

    ``placed_vias`` is an optional list of (cx, cy, netcode) for vias placed
    DURING this build() run (rail-pad GND/+3V3 + stitching vias), which are NOT
    in ``obs_circles`` (snapshotted before _clear_routed). Without this, a +5V
    straggler L-trace on B.Cu crosses the just-placed GND/+3V3 rail-pad vias
    (a decoupling-cap bank row) and shorts them -- 5 shorting_items DRC errors.
    Radius is VIA_DIAM_NM/2 (same 0.6 mm via this script places everywhere)."""
    for sx1, sy1, sx2, sy2, shw, snc, slay in obs_segs:
        if snc == skip_nc or slay != pcbnew.B_Cu:
            continue
        if _seg_seg_dist2(x1, y1, x2, y2, sx1, sy1, sx2, sy2) < (hw + shw + gap) ** 2:
            return False
    for cx, cy, cr, cnc in obs_circles:
        if cnc == skip_nc:
            continue
        if _seg_dist2(cx, cy, x1, y1, x2, y2) < (hw + cr + gap) ** 2:
            return False
    if placed_vias:
        cr = VIA_DIAM_NM / 2
        for cx, cy, cnc in placed_vias:
            if cnc == skip_nc:
                continue
            if _seg_dist2(cx, cy, x1, y1, x2, y2) < (hw + cr + gap) ** 2:
                return False
    return True


class RailTieMixin:
    """Rail-pad via, GND stitching, and isolated straggler-power tie methods,
    inherited by :class:`tools.router.planes.PlaneBuilder`. They read the
    obstacle snapshots (``sig_circles`` / ``sig_segs`` / ``pads`` / ``bga_xy`` /
    ``via_centers`` / ``tie_segs`` / ``straggler_obs``) that ``PlaneBuilder.run``
    populates before any board mutation."""

    def _add_rail_pad_vias(self, netcode, rail_pads=None, micro_only=False):
        """For every SMD pad on this rail net (GND or +3V3) that is NOT on the BGA
        (the FPGA -- its plane balls get via-in-pad from route_signals._bga_fanout)
        and not a PTH pin, place one through-via so the surface pad reaches the
        inner plane (In1.Cu GND or the PWR +3V3 plane). With the 6-layer stackup
        F.Cu/B.Cu carry no GND/+3V3 flood, so a surface rail pad needs an explicit
        via to its plane. Returns the vias.

        Two placement strategies, by pad size:

        * **Via-in-pad** for a large pad (min copper side >= VIP_MIN_NM): the via
          sits AT the pad centre. This is the only way to tie a QFN/LFCSP centre
          exposed-pad (the chips' main ground return, 3.5-5 mm of copper) -- an
          offset via can never clear the dense perimeter pin field around it, and
          a centre via in a big same-net pad needs no clearance to that pad.
        * **Offset spiral** for a small pad (a 0.25 mm QFN lead): try 0.6-3.0 mm
          offsets in 8 directions. The pad being tied is EXCLUDED from the
          obstacle check -- the via IS the pad's connection to the plane, so it
          may sit as close to its own pad as needed; only OTHER nets' copper must
          clear.

        Every candidate is also checked for drill-to-drill spacing against
        ``via_centers`` (the surviving vias, snapshotted before _clear_routed) AND
        this call's already-placed vias, so two rail vias never co-locate (two
        pads sharing a centre, e.g. a connector's top/bottom GND lugs) or sit too
        close to a BGA VIP's drill -- the hole_near_hole / holes_co_located DRC
        rules.

        ``bga_xy``/``rail_pads`` are a pre-mutation snapshot from
        ``_capture_rail_pads`` (board.GetFootprints() is unreliable after
        ``_clear_routed`` mutates the board). ``rail_pads`` carries each pad's
        copper size (sx, sy) for the via-in-pad decision."""
        if rail_pads is None:
            rail_pads = self.rail_pads
        board = self.board
        bga_xy = self.bga_xy
        pads = self.pads
        sig_circles = self.sig_circles
        sig_segs = self.sig_segs
        sig_segs_fcu = self.sig_segs_fcu
        sig_segs_in1 = self.sig_segs_in1
        via_centers = self.via_centers
        tie_segs = self.tie_segs
        via_r = VIA_DIAM_NM / 2
        gap = COPPER_GAP_NM
        self_eps = 50_000  # 0.05 mm -- a pad is "self" if within this of its centre
        drill_min2 = DRILL_MIN_CENTER_NM ** 2
        # Rectangle pad model: a candidate clears a pad when the (squared)
        # distance from the via CENTRE to the pad's copper RECTANGLE >=
        # (via_r + gap)^2, i.e. the via land keeps >= gap (0.16 mm) of copper gap
        # to the pad. Each check below adds the OTHER object's half-width so the
        # gap is copper-to-copper:
        #   via vs pad rect  : via_r + gap            (rect already is the copper edge)
        #   via vs signal via: via_r + cr + gap       (both lands)
        #   via vs signal trk: via_r + hw + gap
        #   via vs diff via  : 2*via_r + gap          (centre-to-centre)
        via_pad2 = (via_r + gap) ** 2
        via_via2 = (2 * via_r + gap) ** 2
        track_hw = RAIL_TRACK_NM / 2
        # via_centers is the shared, mutable via-spacing list threaded through
        # run(): it starts with the surviving (pre-_clear_routed) via centres and
        # every via this call places is appended to it, so the +3V3 pass sees the
        # GND vias just placed and the stitching pass sees both.

        def _clears(vx, vy, sx, sy):
            for ox, oy, hw, hh, onc, opth in pads:
                if onc == netcode and not opth:
                    continue  # same-net SMD pad: the via may overlap it (no DRC issue)
                if onc == netcode and opth:
                    continue  # same-net PTH pad: own via-in-pad handles plane tie
                if abs(ox - sx) < self_eps and abs(oy - sy) < self_eps:
                    continue  # the pad being tied (defensive: also same net)
                if _rect_dist2(vx, vy, ox, oy, hw, hh) < via_pad2:
                    return False
            for cx, cy, cr in sig_circles:
                req = via_r + cr + gap
                if (vx - cx) ** 2 + (vy - cy) ** 2 < req * req:
                    return False
            # the via is THROUGH -- it pierces every layer, so check signal tracks
            # on ALL layers (sig_segs), not just F.Cu.
            for x1s, y1s, x2s, y2s, hw in sig_segs:
                req = via_r + hw + gap
                if _seg_dist2(vx, vy, x1s, y1s, x2s, y2s) < req * req:
                    return False
            # the via sits on F.Cu too, so it must clear a DIFFERENT-net tie trace
            # placed by the other rail pass (a +3V3 via over a GND trace would
            # short). Same-net tie traces (this pass's own, appended below) are
            # skipped.
            for tx1, ty1, tx2, ty2, tnc, thw in tie_segs:
                if tnc == netcode:
                    continue
                req = via_r + thw + gap
                if _seg_dist2(vx, vy, tx1, ty1, tx2, ty2) < req * req:
                    return False
            # via-to-via spacing vs surviving vias + this call's placed vias:
            # same net -> drill spacing only (copper may overlap); different net
            # -> copper clearance (0.76 mm centre keeps the 0.16 mm gap to a 0.6
            # mm via).
            for cx, cy, cnc in via_centers:
                d2 = (vx - cx) ** 2 + (vy - cy) ** 2
                if cnc == netcode:
                    if d2 < drill_min2:
                        return False
                elif d2 < via_via2:
                    return False
            return True

        def _trace_clears(x1, y1, x2, y2, sx, sy):
            """The F.Cu tie trace pad-centre -> via-centre must keep >= gap of
            copper gap to every OTHER-net pad (rect-to-segment), signal via,
            signal track, and different-net via. Same-net copper may be crossed.
            The via endpoint already cleared via _clears; this guards the trace
            body.

            The trace is on F.Cu ONLY, so it is checked against sig_segs_fcu
            (F.Cu tracks) -- never against In3/In4/B.Cu tracks it physically
            cannot reach (the earlier all-layer check falsely blocked half the
            traces). It is still checked against ALL signal vias, because a
            through via's land sits on F.Cu too."""
            trk_pad2 = (track_hw + gap) ** 2
            for ox, oy, hw, hh, onc, opth in pads:
                if onc == netcode:
                    continue
                if abs(ox - sx) < self_eps and abs(oy - sy) < self_eps:
                    continue
                if _rect_seg_dist2(x1, y1, x2, y2, ox, oy, hw, hh) < trk_pad2:
                    return False
            for cx, cy, cr in sig_circles:
                req = track_hw + cr + gap
                if _seg_dist2(cx, cy, x1, y1, x2, y2) < req * req:
                    return False
            for x1s, y1s, x2s, y2s, hw in sig_segs_fcu:
                req = track_hw + hw + gap
                if _seg_seg_dist2(x1, y1, x2, y2, x1s, y1s, x2s, y2s) < req * req:
                    return False
            # a tie trace must keep copper gap to a DIFFERENT-net tie trace placed
            # by the other rail pass (parallel GND/+3V3 fanouts 0.5 mm apart would
            # otherwise leave a 0.1 mm gap < the 0.15 mm Power rule). Same-net
            # traces (this pass's own, appended below) may run parallel -- no DRC
            # issue.
            for tx1, ty1, tx2, ty2, tnc, thw in tie_segs:
                if tnc == netcode:
                    continue
                req = track_hw + thw + gap
                if _seg_seg_dist2(x1, y1, x2, y2, tx1, ty1, tx2, ty2) < req * req:
                    return False
            for cx, cy, cnc in via_centers:
                if cnc == netcode:
                    continue
                req = track_hw + via_r + gap
                if _seg_dist2(cx, cy, x1, y1, x2, y2) < req * req:
                    return False
            return True

        def _micro_clears(vx, vy):
            """Microvia at the BGA spec (0.20 mm / 0.10 mm laser drill,
            F.Cu->In1) clear of copper on the ONLY two layers it spans: F.Cu
            (pads / F tracks / other barrels) and In1 (the plane layer --
            detector-bus tracks that ride the pour + barrels + holes). The
            In3/In4/B.Cu signal-layer cage is irrelevant here: a single-
            dielectric micro never touches those layers, which is exactly why
            it is the only via type that can tie a plane pad walled in by the
            detector bus."""
            mr = BGA_VIA_DIAM_NM // 2
            for ox, oy, hw, hh, onc, opth in pads:
                if onc == netcode:
                    continue
                if _rect_dist2(vx, vy, ox, oy, hw, hh) < (mr + gap) ** 2:
                    return False
            for x1s, y1s, x2s, y2s, hw in sig_segs_fcu:
                if _seg_dist2(vx, vy, x1s, y1s, x2s, y2s) < (mr + hw + gap) ** 2:
                    return False
            for x1s, y1s, x2s, y2s, hw in sig_segs_in1:
                if _seg_dist2(vx, vy, x1s, y1s, x2s, y2s) < (mr + hw + gap) ** 2:
                    return False
            for cx, cy, cr in sig_circles:
                if (vx - cx) ** 2 + (vy - cy) ** 2 < (mr + cr + gap) ** 2:
                    return False
            for hx, hy, hnc in via_centers:
                if (vx - hx) ** 2 + (vy - hy) ** 2 < drill_min2:
                    return False
            return True

        dirs = ((1, 0), (-1, 0), (0, 1), (0, -1),
                (1, 1), (1, -1), (-1, 1), (-1, -1))
        radii = (RAIL_VIA_OFFSET_NM, 800_000, 1_000_000, 1_200_000,
                 1_600_000, 2_000_000, 2_500_000, 3_000_000)

        _fill_cache = {}
        _clr_cache = {}

        def _plane_fill(nc):
            """The poured fill of nc's plane zone (None when absent/unfilled/
            after the zone proxies degraded -- see the AttributeError guard).
            Also caches the zone's local clearance for _pour_clear."""
            if nc in _fill_cache:
                return _fill_cache[nc]
            fp = None
            zclr = COPPER_GAP_NM
            try:
                zone = next((z for z in board.Zones()
                             if z.GetNetCode() == nc), None)
                if zone is not None:
                    fp = zone.GetFilledPolysList(zone.GetFirstLayer())
                    if fp is not None and fp.OutlineCount() == 0:
                        fp = None
                    try:
                        zclr = int(zone.GetLocalClearance())
                    except (AttributeError, TypeError, RuntimeError):
                        zclr = COPPER_GAP_NM
            except AttributeError:
                fp = None
            _fill_cache[nc] = fp
            _clr_cache[nc] = zclr
            return fp

        def _pour_clear(nc, vx, vy):
            """Would the plane pour produce copper at THIS spot (checked at
            the zone's own clearance, i.e. the pour's fill model)? Used when
            the spot has no fill polygon yet -- typically an island-removed
            pocket (a fragment with no GND item gets dropped on every refill):
            the moment the micro lands, island removal keeps the region (it
            now contains a via) and the pour ties it. Only In1-side copper
            constrains the pour: In1 tracks, barrel lands, drilled pads."""
            zclr = _clr_cache.get(nc, COPPER_GAP_NM)
            mr = BGA_VIA_DIAM_NM // 2
            for ox, oy, hw, hh, onc, opth in pads:
                if onc == nc:
                    continue
                if _rect_dist2(vx, vy, ox, oy, hw, hh) < (mr + zclr) ** 2:
                    return False
            for x1s, y1s, x2s, y2s, hw in sig_segs_in1:
                if _seg_dist2(vx, vy, x1s, y1s, x2s, y2s) < (mr + hw + zclr) ** 2:
                    return False
            for cx, cy, cr in sig_circles:
                if (vx - cx) ** 2 + (vy - cy) ** 2 < (mr + cr + zclr) ** 2:
                    return False
            return True

        def _already_tied(px, py, psz_x, psz_y):
            """A previous run's tie already serves this pad: the pad sits in
            the plane fill, a same-net via lands on/next to it, or a same-net
            trace ends inside it. Used by --micro-tie to leave finished work
            untouched (it must never re-tie or duplicate)."""
            fpolys = _plane_fill(netcode)
            if fpolys is not None and fpolys.Contains(
                    pcbnew.VECTOR2I(int(px), int(py))):
                return True
            hx = psz_x // 2 + 350_000
            hy = psz_y // 2 + 350_000
            for t in board.Tracks():
                if t.GetNetCode() != netcode:
                    continue
                if t.GetClass() == "PCB_VIA":
                    p = t.GetPosition()
                    if abs(p.x - px) <= hx and abs(p.y - py) <= hy:
                        return True
                else:
                    for e in (t.GetStart(), t.GetEnd()):
                        if (abs(e.x - px) <= psz_x // 2
                                and abs(e.y - py) <= psz_y // 2):
                            return True
            return False

        def _micro_tie(px, py, psz_x, psz_y):
            """Tie one plane pad with a microvia: in-pad positions first, then
            OFFSET spots along the through-spiral's own radii/dirs (with an
            F.Cu tie trace) -- an offset is what saves a caged TSSOP lead whose
            plane fill does not reach under the pad itself. Every candidate
            must clear F.Cu + In1 copper at the project gap and (when a pour
            is loaded) land INSIDE the fill, so the tie is real rather than
            merely dangling-clean. Returns True if a micro was placed."""
            mi_r = BGA_VIA_DIAM_NM // 2
            fpolys = _plane_fill(netcode)
            has_fill = fpolys is not None
            inset_x = max(0, psz_x // 2 - mi_r - 50_000)
            inset_y = max(0, psz_y // 2 - mi_r - 50_000)
            offs = [(0, 0)]
            for k in range(1, int(inset_x // 30_000) + 1):
                offs += [(k * 30_000, 0), (-k * 30_000, 0)]
            for k in range(1, int(inset_y // 30_000) + 1):
                offs += [(0, k * 30_000), (0, -k * 30_000)]
            cands = [(px + dx, py + dy, False) for dx, dy in offs
                     if abs(dx) <= inset_x and abs(dy) <= inset_y]
            # fine 0.1 mm steps along the 8 dirs: the In1 vertical carves
            # leave ~0.3-0.5 mm slivers of pour between them and the coarse
            # through-spiral radii (0.4+ mm jumps) step right over them.
            fine = [RAIL_VIA_OFFSET_NM + k * 100_000 for k in range(0, 25)]
            for r in fine:
                for dxn, dyn in dirs:
                    cands.append((px + int(dxn * r), py + int(dyn * r), True))
            # Rank landings: inside the MAIN fill fragment first (a micro in a
            # stranded sliver would tie the pad to an island -- its pour region
            # has no onward path -- which is exactly the failure this ordering
            # exists to beat), then any already-poured spot, then not-yet-poured
            # spots the pour would still reach (island-removed pockets that
            # become connected the moment the micro anchors them).
            main_idx = -1
            if has_fill:
                best_a = -1
                for i in range(fpolys.OutlineCount()):
                    a = abs(fpolys.Outline(i).Area())
                    if a > best_a:
                        best_a, main_idx = a, i

            def _frag_of(x_, y_):
                if not has_fill:
                    return -2
                v_ = pcbnew.VECTOR2I(int(x_), int(y_))
                if not fpolys.Contains(v_):
                    return -1
                for i in range(fpolys.OutlineCount()):
                    if fpolys.Contains(v_, i):
                        return i
                return -1

            scored = []
            for vx_, vy_, nt_ in cands:
                fr = _frag_of(vx_, vy_)
                rank = (0 if fr == main_idx and main_idx >= 0
                        else 1 if fr >= 0 else 2)
                scored.append((rank, vx_, vy_, nt_, fr))
            scored.sort(key=lambda s: s[0])
            for _rank, vx, vy, needs_trace, fr in scored:
                if not _micro_clears(vx, vy):
                    continue
                if has_fill and fr == -1 and not _pour_clear(netcode, vx, vy):
                    continue
                if needs_trace and not _trace_clears(px, py, vx, vy, px, py):
                    continue
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(pcbnew.VECTOR2I(int(vx), int(vy)))
                v.SetNetCode(netcode)
                v.SetViaType(pcbnew.VIATYPE_MICROVIA)
                v.SetDrill(BGA_VIA_DRILL_NM)
                v.SetWidth(BGA_VIA_DIAM_NM)
                ls = pcbnew.LSET()
                ls.AddLayer(pcbnew.F_Cu)
                ls.AddLayer(pcbnew.In1_Cu)
                v.SetLayerSet(ls)
                board.Add(v)
                vias.append(v)
                via_centers.append((vx, vy, netcode))
                if needs_trace:
                    t = pcbnew.PCB_TRACK(board)
                    t.SetStart(pcbnew.VECTOR2I(int(px), int(py)))
                    t.SetEnd(pcbnew.VECTOR2I(int(vx), int(vy)))
                    t.SetLayer(pcbnew.F_Cu)
                    t.SetWidth(this_trk_w)
                    t.SetNetCode(netcode)
                    board.Add(t)
                    tie_segs.append((px, py, vx, vy, netcode, track_hw))
                return True
            return False

        vias = []
        skipped = 0
        vip = 0
        micro = 0
        for (px, py, attrib, psz_x, psz_y) in rail_pads.get(netcode, []):
            if attrib == pcbnew.PAD_ATTRIB_PTH:
                continue
            if (px, py) in bga_xy:
                continue
            # Per-pad tie-trace width: a dense SMT connector lead (<= 0.3 mm
            # narrow side on a 0.5 mm pitch) cannot host a 0.4 mm trace alongside
            # the adjacent-row lead (0.15 mm gap < COPPER_GAP), so use the 0.2 mm
            # thin trace there. The closures below read `track_hw` late (cell
            # binding), so reassigning it here makes _clears / _trace_clears use
            # this pad's width.
            if min(psz_x, psz_y) <= RAIL_TRACK_THIN_MAX:
                track_hw = RAIL_TRACK_THIN_NM / 2
                this_trk_w = RAIL_TRACK_THIN_NM
            else:
                track_hw = RAIL_TRACK_NM / 2
                this_trk_w = RAIL_TRACK_NM
            if micro_only:
                # --micro-tie completion mode: non-destructive -- a pad an
                # existing tie already serves is left alone; only loose pads
                # get the micro fallback (which may need this_trk_w for the
                # offset tie, hence the width selection above). No through
                # candidates, no stripping, no zone rebuild in this mode.
                if _already_tied(px, py, psz_x, psz_y):
                    continue
                if _micro_tie(px, py, psz_x, psz_y):
                    micro += 1
                continue
            placed = False
            # Via-in-pad for a large pad (centre exposed-pad / big connector
            # lug): a 0.6 mm via centred in a >= VIP_MIN_NM pad clears its own
            # pad's edges and -- being the pad's own net -- needs no clearance to
            # it.
            if min(psz_x, psz_y) >= VIP_MIN_NM:
                if _clears(px, py, px, py):
                    v = pcbnew.PCB_VIA(board)
                    v.SetPosition(pcbnew.VECTOR2I(int(px), int(py)))
                    v.SetNetCode(netcode)
                    v.SetViaType(pcbnew.VIATYPE_THROUGH)
                    v.SetDrill(VIA_DRILL_NM)
                    v.SetWidth(VIA_DIAM_NM)
                    board.Add(v)
                    vias.append(v)
                    via_centers.append((px, py, netcode))
                    vip += 1
                    placed = True
            if not placed:
                for r in radii:
                    for dxn, dyn in dirs:
                        vx, vy = px + int(dxn * r), py + int(dyn * r)
                        if not _clears(vx, vy, px, py):
                            continue
                        # an offset via only ties its pad if an F.Cu trace
                        # connects them -- without it the pad floats off the
                        # plane (hidden by the board's unconnected_items:ignore).
                        # Require the trace body to clear other nets before
                        # committing the via+trace.
                        if not _trace_clears(px, py, vx, vy, px, py):
                            continue
                        v = pcbnew.PCB_VIA(board)
                        v.SetPosition(pcbnew.VECTOR2I(int(vx), int(vy)))
                        v.SetNetCode(netcode)
                        v.SetViaType(pcbnew.VIATYPE_THROUGH)
                        v.SetDrill(VIA_DRILL_NM)
                        v.SetWidth(VIA_DIAM_NM)
                        board.Add(v)
                        vias.append(v)
                        via_centers.append((vx, vy, netcode))
                        # F.Cu tie trace: pad-centre -> via-centre. This is what
                        # actually connects the surface rail pad to its plane via.
                        t = pcbnew.PCB_TRACK(board)
                        t.SetStart(pcbnew.VECTOR2I(int(px), int(py)))
                        t.SetEnd(pcbnew.VECTOR2I(int(vx), int(vy)))
                        t.SetLayer(pcbnew.F_Cu)
                        t.SetWidth(this_trk_w)
                        t.SetNetCode(netcode)
                        board.Add(t)
                        # record the trace (+ its half-width) so the other rail
                        # pass keeps copper gap to it (see _clears /
                        # _trace_clears tie_segs checks, which use this trace's
                        # real half-width).
                        tie_segs.append((px, py, vx, vy, netcode, track_hw))
                        placed = True
                        break
                    if placed:
                        break
            if not placed and netcode == self.gnd and (
                    micro_only or os.environ.get("QSSC_RAIL_MICRO")):
                # HDI microvia fallback (see _micro_tie): in-pad first, then
                # offset-with-tie along the through-spiral radii. Saves a plane
                # pad caged so tightly no THROUGH site exists within 3 mm (a
                # TIA TSSOP GND lead walled by the detector bus: the In3 bands
                # make every through via in the whole field illegal) -- a micro
                # spans only F.Cu..In1, so the signal-layer cage cannot block
                # it. Landing gated on the poured fill when one is loaded.
                if _micro_tie(px, py, psz_x, psz_y):
                    placed = True
                    micro += 1
            if not placed:
                skipped += 1
        if skipped:
            print(f"  rail-pad via: {len(vias)} placed ({vip} via-in-pad), "
                  f"{skipped} SMD pads could not be tied "
                  f"(no clear via site within 3 mm)")
        if micro:
            print(f"  rail-pad microvia-in-pad: {micro} placed "
                  f"(F.Cu->In1 0.20/0.10, QSSC_RAIL_MICRO fallback)")
        return vias

    def _add_stitching_vias(self):
        """Place GND through-vias on a coarse grid, skipping any point too close
        to a pad OR an existing signal track/via. Ties the F.Cu/In1.Cu/B.Cu GND
        copper into one mesh. Returns the list of created via objects (kept so
        callers can verify without re-loading the board -- KiCad's python binding
        can return bare SwigPyObjects for a board re-loaded after mutation).

        Each grid point is also checked for drill-to-drill spacing against
        ``via_centers`` (surviving vias + the rail-pad vias just placed) and the
        already-placed stitching vias, so a stitching via never co-locates with a
        BGA GND VIP or a rail-pad via (hole_near_hole / holes_co_located).

        ``sig_circles``/``sig_segs`` are the signal via-circles / track-segments
        snapshotted BEFORE _clear_routed (the swig proxy layer breaks
        board.Tracks() after board.Remove(), so callers capture them up front)."""
        board = self.board
        netcode = self.gnd
        bbox = self.bbox
        pads = self.pads
        sig_circles = self.sig_circles
        sig_segs = self.sig_segs
        via_centers = self.via_centers
        tie_segs = self.tie_segs
        via_r = VIA_DIAM_NM / 2
        gap = COPPER_GAP_NM
        via_pad2 = (via_r + gap) ** 2
        via_via2 = (2 * via_r + gap) ** 2  # 0.76 mm: diff-net copper clearance
        drill_min2 = DRILL_MIN_CENTER_NM ** 2
        margin = 2_000_000  # 2 mm in from the board edge
        x0 = bbox.GetX() + margin
        y0 = bbox.GetY() + margin
        x1 = bbox.GetX() + bbox.GetWidth() - margin
        y1 = bbox.GetY() + bbox.GetHeight() - margin
        # merge surviving vias + rail-pad vias into one spacing list, then append
        # each stitching via as it is placed.
        all_via_centers = list(via_centers)
        vias = []
        x = x0
        while x <= x1:
            y = y0
            while y <= y1:
                clear = True
                for px, py, hw, hh, onc, opth in pads:
                    if onc == netcode and not opth:
                        continue  # same-net SMD pad: a GND stitching via may overlap
                    if onc == netcode and opth:
                        # same-net PTH pad (e.g. UE1 HTSSOP thermal-pad vias): the
                        # pad already carries its own drill + plane connection, so
                        # a stitching via landing on it co-locates drills
                        # (hole_near_hole, actual 0.0 mm). Require copper clearance
                        # so the via sits OFF the pad instead of on its hole.
                        if _rect_dist2(x, y, px, py, hw, hh) < via_pad2:
                            clear = False
                            break
                        continue
                    if _rect_dist2(x, y, px, py, hw, hh) < via_pad2:
                        clear = False
                        break
                if clear:
                    for cx, cy, cr in sig_circles:           # signal vias
                        req = via_r + cr + gap
                        if (x - cx) ** 2 + (y - cy) ** 2 < req * req:
                            clear = False
                            break
                if clear:
                    for x1s, y1s, x2s, y2s, hw in sig_segs:   # signal tracks (all layers)
                        req = via_r + hw + gap
                        if _seg_dist2(x, y, x1s, y1s, x2s, y2s) < req * req:
                            clear = False
                            break
                if clear:
                    # a GND stitching via must clear a DIFFERENT-net (e.g. +3V3)
                    # tie trace (it sits on F.Cu too); same-net (GND) traces are
                    # fine.
                    for tx1, ty1, tx2, ty2, tnc, thw in tie_segs:
                        if tnc == netcode:
                            continue
                        req = via_r + thw + gap
                        if _seg_dist2(x, y, tx1, ty1, tx2, ty2) < req * req:
                            clear = False
                            break
                if clear:
                    for cx, cy, cnc in all_via_centers:        # via-to-via spacing
                        d2 = (x - cx) ** 2 + (y - cy) ** 2
                        if cnc == netcode:
                            if d2 < drill_min2:                # same net: drill only
                                clear = False
                                break
                        elif d2 < via_via2:                   # diff net: copper
                            clear = False
                            break
                if clear:
                    v = pcbnew.PCB_VIA(board)
                    v.SetPosition(pcbnew.VECTOR2I(int(x), int(y)))
                    v.SetNetCode(netcode)
                    v.SetViaType(pcbnew.VIATYPE_THROUGH)
                    v.SetDrill(VIA_DRILL_NM)
                    v.SetWidth(VIA_DIAM_NM)
                    board.Add(v)
                    vias.append(v)
                    all_via_centers.append((x, y, netcode))
                y += GRID_STEP_NM
            x += GRID_STEP_NM
        return vias

    def _tie_stragglers_to_rail(self, netcode, via_positions, placed_vias=None):
        """For each straggler via just placed (a THROUGH via tying an isolated
        power pad via its F.Cu stub), route a thin 0.2 mm B.Cu L-trace from the
        via to the nearest existing same-net rail copper so the pad joins the
        rail. B.Cu carries no SMD pads, so an L-path (two orthogonal segments) is
        usually clear; if both L-orders are blocked the pad is left to GUI.
        ``via_positions`` is the list of (vx, vy, nc) the rail-pad spiral appended
        to via_centers (the via OBJECTS' GetPosition() returns a bare SwigPyObject
        after board mutation). ``placed_vias`` is the full via_centers list
        (surviving + rail-pad + stitching + straggler), so the L-trace also clears
        vias placed earlier in this run that the pre-clear obs snapshot cannot
        see. Returns n_tracks."""
        board = self.board
        obs_segs, obs_circles = self.straggler_obs
        hw = RAIL_TRACK_THIN_NM / 2
        gap = COPPER_GAP_NM
        nt = 0
        for vx, vy, vc in via_positions:
            if vc != netcode:
                continue
            tgt = _nearest_same_net_point(vx, vy, netcode, obs_segs, obs_circles)
            if tgt is None:
                continue
            tx, ty = tgt
            if (tx - vx) ** 2 + (ty - vy) ** 2 <= (hw * 4) ** 2:
                continue  # via already sits on / within touching distance of rail
            for mx, my in ((tx, vy), (vx, ty)):
                segs = ((vx, vy, mx, my), (mx, my, tx, ty))
                if all(_bcu_seg_clear(a, b, c, d, netcode, obs_segs, obs_circles,
                                      hw, gap, placed_vias)
                       for a, b, c, d in segs):
                    for x1, y1, x2, y2 in segs:
                        t = pcbnew.PCB_TRACK(board)
                        t.SetStart(pcbnew.VECTOR2I(int(x1), int(y1)))
                        t.SetEnd(pcbnew.VECTOR2I(int(x2), int(y2)))
                        t.SetLayer(pcbnew.B_Cu)
                        t.SetWidth(RAIL_TRACK_THIN_NM)
                        t.SetNetCode(netcode)
                        board.Add(t)
                        nt += 1
                    break
        return nt