"""tools.router — QSSC signal router package (split from route_signals.py).

The headless orthogonal ("line") router was split from the monolithic
``route_signals.py`` into:

* :mod:`tools.router.config`  — layer sets, grid/via sizes, net classifiers +
  the per-board mutable layer tuples (``SIGNAL/PLANE/JUMP_LAYERS``) + the
  ``_FAIL_REASON`` diagnostic, all read via ``_cfg.X`` so per-board reassignment
  in :meth:`PcbRouter.build` is seen live by every module.
* :mod:`tools.router.grid`        — pad/segment cell mapping + track/via emission.
* :mod:`tools.router.pair_router` — the L/Z line router + via-up helpers.
* :mod:`tools.router.power`       — power-rail MST router + RF via fence.
* :mod:`tools.router.fanout`      — BGA via-in-pad + chip dog-bone fanout.

:class:`PcbRouter` holds the board for one routing pass; :meth:`build` runs the
full pipeline (HDI rules, clear, fanout, power, signals, RF fence) and returns
the routed/unrouted stats. The thin ``tools/route_signals.py`` shim re-exports
this package's surface for legacy callers (``import route_signals as R`` and the
``python tools/route_signals.py`` subprocess).
"""
from __future__ import annotations

import pcbnew

from route_qssc_pcb import patch_swig_iterators  # noqa: F401

# KiCad-version compat: fix the swig track iterator before any board.Tracks()
# loop (import side effect, as the original monolithic module did).
patch_swig_iterators()

from pad_snapshot import capture_pads as _capture_pads  # noqa: F401

from . import config as _cfg
from .config import (  # noqa: F401
    PCB_PATH, _DEBUG, EXCLUDE_NETS, _RF_NETS, PLANE_NETS, POWER_NETS,
    POWER_ROUTER_LAYER, BGA_REF, PAD_KEEPOUT_NM, GRID_NM, set_layer_count,
    _is_chip_net, _net_class, _net_width_nm, _mark_radius_for_width,
)
from .grid import (  # noqa: F401
    _pad_layers, _pad_cells, _pad_own_cells, _build_master, _mark, _free,
    _via_footprint_free, _seg_cells, _seg_clear, _add_track, _add_via,
)
from .pair_router import (  # noqa: F401
    _try_layer, _emit_path, _via_candidates, _bga_edge_via_candidates,
    _route_pair, _mst_order, _via_point_clear,
)
from .power import _route_rf_fence, _route_power  # noqa: F401
from .fanout import _clear_tracks, _apply_hdi_rules, _bga_fanout, _chip_fanout  # noqa: F401


# The per-board mutable layer tuples (SIGNAL/PLANE/JUMP_LAYERS) are reassigned
# by set_layer_count() on the config module; resolve them live via _cfg so
# ``router.SIGNAL_LAYERS`` reflects the current board instead of a snapshot
# taken at import (the original monolithic module's globals were live too).
def __getattr__(name):
    if name in {"SIGNAL_LAYERS", "PLANE_LAYERS", "JUMP_LAYERS", "_FAIL_REASON"}:
        return getattr(_cfg, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class PcbRouter:
    """One signal-routing pass on a QSSC board.

    Holds the board; :meth:`build` runs the full routing pipeline and returns the
    routed/unrouted stats dict. The per-run state (master obstacle grid, routed
    mark set, via-2d index, pad vip maps, origin) is built inside ``build`` and
    threaded to the module-level routing functions as explicit args -- they stay
    pure and independently testable (``R._bga_fanout(...)`` etc.).
    """

    def __init__(self, board):
        self.board = board

    def build(self) -> dict:
        board = self.board
        # Recompute the layer sets for THIS board's copper count. The main control
        # board is 6-layer HDI (signal layers F.Cu/In3.Cu/In4.Cu/B.Cu, planes
        # In1.Cu/In2.Cu); the detector head is 4-layer (signal layers F.Cu/B.Cu,
        # planes In1.Cu/In2.Cu). The hardcoded 6-layer module globals would route on
        # non-existent In3/In4 copper IDs on the head, so override them here. JUMP
        # layers lose In3/In4 on the head (only B.Cu remains); the per-via escape
        # layer is still added separately in _route_pair, so F.Cu<->B.Cu hops work.
        _cfg.set_layer_count(board.GetCopperLayerCount())        # Snapshot EVERYTHING read from the board BEFORE any mutation: KiCad's swig
        # binding returns bare SwigPyObjects (no .GetCenter/.Pads) for
        # board.GetFootprints()/fp.Pads() after board.Remove() runs in _clear_tracks.
        # A reload would dodge this but OOMs under the hard MemoryMax cap, so we
        # stay single-load: snapshot pads+bbox+netcodes up front, mutate (rules +
        # clear), then build from the snapshot + board.Add() only (Add does not
        # break the proxy layer). Idempotent: _clear_tracks strips any prior run.
        bb = board.GetBoardEdgesBoundingBox()
        fps = _capture_pads(board)
        netcodes = {net.GetNetname(): code
                    for code, net in board.GetNetInfo().NetsByNetcode().items()}
        _apply_hdi_rules(board)
        _clear_tracks(board)
        x0, y0 = bb.GetX(), bb.GetY()
        gx = int(bb.GetWidth() // GRID_NM)
        gy = int(bb.GetHeight() // GRID_NM)
        master_blocked, via2d, all_pad_copper, multi_keepout = _build_master(fps, x0, y0, gx, gy)

        routed = set()
        vip_net, pad_vip, vip_vias = _bga_fanout(board, x0, y0, routed, fps=fps)
        # QSSC_Chip die-face dog-bone fanout: 0.15 mm stub (pad's own layer) + a
        # 0.20 mm through-via 1.0 mm out, escaped on the opposite (clear) surface by
        # the line router. Adds via pseudo-pads (via_pads) + merges its vip dicts.
        # Main: 64 EM cathodes on F.Cu escape B.Cu (EM_ANODE/+12V_EMIT left to the
        # power router). Head: 64 DET pads on B.Cu escape F.Cu.
        chip_pad_centers, via_pads, cv_net, cv_pad_vip, cv_vias = _chip_fanout(
            board, x0, y0, routed, fps=fps)
        vip_net.update(cv_net)
        pad_vip.update(cv_pad_vip)
        for nm, vs in cv_vias.items():
            vip_vias.setdefault(nm, []).extend(vs)
        # The chip dog-bone vias are a COMPLETE fanout (pad -> 0.15 mm stub ->
        # through-via) that the line router finishes on the escape surface; unlike a
        # BGA via-in-pad that never escaped (a true dangling barrel), a dog-bone via
        # is a valid net tie the line router routes FROM. The failed-net VIP removal
        # block was removed (see below) -- ALL VIPs stay so FreeRouting can close the
        # net from the via, so the dog-bone vias are kept implicitly.
        # BGA pad bounding box (nm) for the edge-escape phase 2b: the FPGA footprint
        # from the snapshot. None if there is no BGA -> phase 2b skips.
        bga_bbox = None
        fpga_fp = next((fp for fp in fps if fp.GetReference() == BGA_REF), None)
        if fpga_fp is not None and len(fpga_fp.Pads()) >= 100:
            pxs = [p.GetPosition().x for p in fpga_fp.Pads()]
            pys = [p.GetPosition().y for p in fpga_fp.Pads()]
            bga_bbox = (min(pxs), min(pys), max(pxs), max(pys))
        # every via placed by _bga_fanout + _chip_fanout; the removal block below
        # decrements the through-vias of FAILED signal nets.
        bga_vias = sum(len(vs) for vs in vip_vias.values())

        # _route_power routes the five power rails FIRST on dedicated inner/bottom
        # layers (POWER_ROUTER_LAYER), then _route_pair line-routes the signals.
        # Plane nets (GND/+3V3) tie to their inner planes via microvias/blind vias
        # and stay excluded (no tracks). Each power rail is a 0.5 mm Power-class MST
        # of L/Z tracks on its rail layer: +1V0/+1V8 on In3/In4 (their BGA through-via
        # barrels already sit there), +5V/+5V_RF/+5V_IN on B.Cu (no SMD pads there, so
        # the rail clears the surface GND/other-net pad keepouts that block it on
        # F.Cu). Every SMD rail pad gets a through-via tie to the rail layer; the BGA
        # power balls are tied at their own barrel. Routed in span order (longest
        # crossing first) so each rail claims the still-empty layer. Power VIPs are
        # never removed as "dangling" (a rail net only fails if a SEGMENT is
        # unreachable -- the BGA VIPs themselves always tie).
        net_pads = {}
        for fp in fps:
            for p in fp.Pads():
                nm = p.GetNetname()
                if nm and nm not in PLANE_NETS and nm not in EXCLUDE_NETS:
                    # skip the 128 signal chip die pads: _chip_fanout already tied each
                    # to a dog-bone via (a via_pads pseudo-pad; uniform 1.0 mm via-out
                    # at the 2.5 mm die pitch). Routing from the chip pad itself would
                    # duplicate the stub; route from the via instead.
                    if (int(p.GetPosition().x), int(p.GetPosition().y)) in chip_pad_centers:
                        continue
                    net_pads.setdefault(nm, []).append(p)
        # add the chip-fanout via pseudo-pads as routing endpoints (the stub already
        # connects each chip pad to its via; the line router routes via<->target).
        for nm, ps in via_pads.items():
            net_pads.setdefault(nm, []).extend(ps)

        done, partial, fail, single, nt, nv = [], [], [], [], 0, 0
        diag = {} if _DEBUG else None

        def _span(ps):
            """Max pairwise centre distance of a net's pads -- a proxy for how much
            room a net needs. Long crossings (esp. BGA->connector) sort FIRST so
            they claim the (still-empty) B.Cu escape layer before short local nets
            fill it and block them."""
            cs = [p.GetCenter() for p in ps]
            best = 0
            for i in range(len(cs)):
                for j in range(i + 1, len(cs)):
                    d = (cs[i].x - cs[j].x) ** 2 + (cs[i].y - cs[j].y) ** 2
                    if d > best:
                        best = d
            return best

        # ---- power rails FIRST (Task 7): dedicated inner/bottom layer, MST L/Z ----
        # Routed before signals so each rail's 0.5 mm track claims the still-empty
        # rail layer. Span order (longest crossing first): the rail that must cross
        # the board (e.g. +5V_IN VBUS bottom->fuse top, +5V_RF FB1->RF section)
        # claims B.Cu before shorter rails fill it. A failed rail is left
        # to GUI; its BGA through-via barrels stay (not removed below).
        for net in sorted((n for n in net_pads if n in POWER_ROUTER_LAYER),
                          key=lambda n: -_span(net_pads[n])):
            pads = net_pads[net]
            if len(pads) < 2:
                single.append(net)
                continue
            try:
                nc = netcodes[net]
            except KeyError:
                fail.append(net)
                continue
            allowed = set()
            own_keepout = set()
            for p in pads:
                allowed |= _pad_own_cells(p, x0, y0)
                own_keepout |= _pad_cells(p, x0, y0, PAD_KEEPOUT_NM)
            if net in vip_net:
                allowed |= vip_net[net]
            own_keepout -= multi_keepout            # never override a cell a neighbour pad's ring also covers
            own_keepout -= all_pad_copper - allowed
            width_nm = _net_width_nm(net)
            mark_r = _mark_radius_for_width(width_nm)
            r = _route_power(board, nc, net, pads, POWER_ROUTER_LAYER[net],
                             master_blocked, own_keepout, routed, allowed,
                             x0, y0, via2d, pad_vip, width_nm, mark_r)
            # _route_power now returns a 4-tuple (nt, nv, skip_pad, skip_edge) and
            # NEVER returns None: skip_pad/skip_edge count pads/edges left to GUI.
            # nt==0  -> nothing routable at all .......................... fail
            # skip==0 -> whole rail routed ............................... done
            # skip>0  -> partial (some pads/edges GUI) ........... partial
            nt_r, nv_r, skip_pad, skip_edge = r
            if nt_r == 0:
                fail.append(net)
                if diag is not None:
                    diag[net] = _cfg._FAIL_REASON or f"power {net}: no edges routed"
            else:
                nt += nt_r
                nv += nv_r
                if skip_pad or skip_edge:
                    partial.append(net)
                    if diag is not None:
                        diag[net] = (f"power {net}: {skip_pad} pad(s) + "
                                     f"{skip_edge} edge(s) GUI")
                else:
                    done.append(net)

        # ---- signals: _route_pair line router ----
        # BGA-escape nets first (longest crossing first within that group): they
        # need long B.Cu runs and must claim the (still-empty) B.Cu before short
        # local nets fill it. Then non-BGA nets, shortest first. Power rails are
        # already routed above and skipped here.
        # `structured_chip` = the chip nets that got a dog-bone via in _chip_fanout
        # (64 emitter_cat on main / 64 DET_IN on head).  Each has a via_pads pseudo-
        # pad on the clear opposite layer + a pad_vip entry, so _route_pair escapes
        # via->target on that clear layer (vip_a branch).  They sort FIRST (vip_net
        # members) so they claim the still-empty escape layer before short local
        # nets fill it.  A chip net NOT in structured_chip (none expected -- every
        # netted die pad gets a via) is left for FreeRouting.  The dog-bone vias are
        # kept implicitly -- the failed-net VIP removal block was removed so ALL
        # VIPs (BGA + chip) stay for FreeRouting to close.
        structured_chip = set(via_pads.keys())
        for net in sorted(net_pads,
                          key=lambda n: (0 if n in vip_net else 1, -_span(net_pads[n]),
                                         len(net_pads[n]))):
            if net in POWER_ROUTER_LAYER:
                continue
            if _is_chip_net(net) and net not in structured_chip:
                fail.append(net)
                continue
            pads = net_pads[net]
            if len(pads) < 2:
                single.append(net)            # 1-pad net: nothing to connect (not a
                continue                       # routing failure -- e.g. a clock crystal)
            try:
                nc = netcodes[net]
            except KeyError:
                fail.append(net)
                continue
            allowed = set()
            own_keepout = set()
            for p in pads:
                allowed |= _pad_own_cells(p, x0, y0)
                own_keepout |= _pad_cells(p, x0, y0, PAD_KEEPOUT_NM)
            if net in vip_net:                  # own via-in-pad area is passable
                allowed |= vip_net[net]
            # The own-ring override (own_keepout) lets a track leave its own pad
            # through the 0.3 mm keepout ring. But two same-row BGA balls 0.5 mm
            # apart have overlapping rings, so one ball's ring can extend INTO the
            # neighbour ball's copper -- and without this strip the override would
            # mark that copper free and short the two nets (the clk_100mhz A16 ->
            # mod_sync A15 short). Strip every OTHER pad's bare copper (expand 0)
            # from the override; own pad copper (in `allowed`) is kept, so the
            # track can still leave its own pad, just not through a neighbour.
            # `multi_keepout` is the stronger guard: it removes any cell covered by
            # the keepout ring of a DIFFERENT net (or an unnetted pad), so a track
            # can never be centred within 0.3 mm of a neighbour pad's copper -- the
            # bare-copper strip alone missed the case where the own ring bleeds into
            # a cell that holds no neighbour COPPER but does sit in the neighbour's
            # keepout ring (the D10 mw_gate->GND short).
            own_keepout -= multi_keepout
            own_keepout -= all_pad_copper - allowed
            order = [pads[0]]
            rem = list(pads[1:])
            while rem:
                last = order[-1]
                best = min(rem, key=lambda q: (q.GetCenter().x - last.GetCenter().x) ** 2
                           + (q.GetCenter().y - last.GetCenter().y) ** 2)
                rem.remove(best)
                order.append(best)
            ok = True
            for a, b in zip(order, order[1:]):
                va = a.GetPosition(); vb = b.GetPosition()
                r = _route_pair(board, nc, net, a, b, master_blocked, own_keepout, routed,
                               allowed, x0, y0, via2d,
                               pad_vip.get((va.x, va.y)),
                               pad_vip.get((vb.x, vb.y)),
                               bga_bbox=bga_bbox)
                if r is None:
                    ok = False
                    if diag is not None:
                        diag[net] = _cfg._FAIL_REASON
                    break
                nt += r[0]
                nv += r[1]
            (done if ok else fail).append(net)
        if diag is not None:
            for net, reason in sorted(diag.items()):
                print(f"  DEBUG {net}: {reason}")

        # ---- RF via fence (Task 8): GND return vias alongside routed RF traces ----
        # Only nets that actually routed (in `done`) get a fence -- an unrouted RF
        # net has no F.Cu trace to companion. The fence recomputes the pad L-path
        # (b.Tracks() is not iterable post-mutation) and places clearance-checked
        # GND through-vias at +/-0.6 mm offset every ~1 mm, tying the microstrip's
        # ground reference to In1.Cu. Final via pitch/offset + 50-ohm impedance
        # verification are GUI-tunable; this is the headless SI-fence baseline.
        gnd_nc = netcodes.get("GND")
        rf_done = {n: net_pads[n] for n in _RF_NETS
                   if n in net_pads and n in done}
        if rf_done:
            nv_fence = _route_rf_fence(board, gnd_nc, rf_done, x0, y0,
                                       master_blocked, routed, via2d)
            nv += nv_fence
            if _DEBUG:
                print(f"  DEBUG rf_fence: {nv_fence} GND return vias")

        # KEEP every through via-in-pad (signal + power + chip dog-bone), even for
        # nets the line router FAILED to escape. The old code removed a FAILED
        # signal net's BGA VIPs to avoid a dangling barrel on In3/In4/B.Cu
        # (via_dangling) -- but that removal ran BEFORE FreeRouting, so the later
        # autorouter was left with NO via to connect and the net stayed split
        # forever (the 16 stuck FPGA escape nets). via_dangling is `ignore` in the
        # project rules, so a VIP whose net FreeRouting has not yet closed is
        # DRC-clean; keeping it gives FreeRouting the connection point it needs.
        # Power-rail VIPs were already never removed (they tie the FPGA power balls
        # to the rail layer); chip dog-bone vias are a complete pad+stub+via fanout
        # the line router routes FROM. bga_vias reflects every VIP placed.
        return {"routed": done, "unrouted": fail, "partial_power": partial,
                "single": single, "tracks": nt, "vias": nv,
                "bga_fanout_vias": bga_vias, "excluded": sorted(EXCLUDE_NETS)}




def main() -> dict:
    board = pcbnew.LoadBoard(str(PCB_PATH))
    stats = PcbRouter(board).build()
    board.SetModified()
    pcbnew.SaveBoard(str(PCB_PATH), board)
    return stats


def _print_summary(stats: dict) -> None:
    """Emit the ``routed N / M`` summary line + PARTIAL/UNROUTED lists.

    Split out of :func:`main` so the CLI entry points (the ``route_signals.py``
    shim and this package's own ``__main__``) print it only when actually run
    as a script -- ``main`` itself just builds + saves + returns the stats, so
    importing the package never produces stdout.
    """
    n = len(stats['routed']) + len(stats['unrouted']) + len(stats['partial_power'])
    print(f"routed {len(stats['routed'])} / {n} | "
          f"tracks {stats['tracks']} vias {stats['vias']} bga-fanout-vias {stats['bga_fanout_vias']} "
          f"| single-pad nets {len(stats['single'])} | excluded {len(stats['excluded'])}")
    if stats["partial_power"]:
        print(f"PARTIAL POWER ({len(stats['partial_power'])}) [some pads/edges GUI]: "
              f"{', '.join(sorted(stats['partial_power']))}")
    if stats["unrouted"]:
        print(f"UNROUTED ({len(stats['unrouted'])}): {', '.join(sorted(stats['unrouted']))}")


if __name__ == "__main__":
    _print_summary(main())
    from _kicad_compat import exit_cleanly
    exit_cleanly(0)             # teardown GC is unsafe (see _kicad_compat)


def build(board) -> dict:
    """Module-level convenience wrapper: route one board via :class:`PcbRouter`."""
    return PcbRouter(board).build()

