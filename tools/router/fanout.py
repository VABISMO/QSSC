"""BGA via-in-pad fanout, chip die dog-bone fanout, HDI rule application
and pre-route track clearing. Reads ``_cfg.SIGNAL_LAYERS`` for the
per-board signal layer set.
"""
from __future__ import annotations

import pcbnew

from route_qssc_pcb import track_width_nm, VIP_MAX_NM
from _kicad_compat import VIATYPE_BLIND_BURIED
from pad_snapshot import PadProxy as _PadProxy

from . import config as _cfg
from .config import (
    BGA_REF, BGA_PITCH_NM, BGA_VIA_DIAM_NM, BGA_VIA_DRILL_NM,
    BGA_VIP_MARK_RADIUS, F_CU, IN3_CU, IN4_CU, B_CU,
    GRID_NM, PLANE_NETS, POWER_NETS,
    POWER_RAIL_LAYER,
)
from .grid import _mark


def _clear_tracks(board):
    """Idempotent reset of this router's own copper. Strip every track/via that
    is NOT a wide plane via owned by route_qssc_pcb -- i.e. keep only the GND /
    +3V3 tracks/vias WIDER than VIP_MAX_NM (the 0.6 mm stitching mesh + rail-pad
    vias). Everything else (signal tracks, the 0.20 mm BGA via-in-pad VIPs this
    router owns, power-rail tracks) is removed so a re-run rebuilds it cleanly.
    This keeps a prior route_qssc_pcb plane foundation intact when route_signals
    is re-run on top of it (the inverse of route_qssc_pcb._clear_routed, which
    keeps the small VIPs and signal copper)."""
    for t in [t for t in board.Tracks()]:
        nm = t.GetNetname()
        if nm in ("GND", "+3V3") and track_width_nm(t) > VIP_MAX_NM:
            continue                      # wide plane via/track -- not ours
        board.Remove(t)




def _apply_hdi_rules(board):
    """Relax the board's minimum via / drill / annular to HDI so the BGA fanout
    passes DRC: microvias (0.20 mm / 0.10 mm drill, F.Cu->In1 GND), blind vias
    (0.30 mm / 0.15 mm drill, F.Cu->In2 +3V3) and through via-in-pad (0.20 mm /
    0.10 mm). The 0.8 mm-pitch CSG324 BGA fanout is comfortable with these HDI
    sizes. A 0.10 mm drill is standard HDI laser-drill capability; a 0.05 mm
    annular is the standard via-in-pad figure (the PAD copper, not the via ring,
    provides the annular -- via-in-pad is filled+capped). Blind/buried + microvias
    must be explicitly allowed in the design rules."""
    br = board.GetDesignSettings()
    br.m_ViasMinSize = BGA_VIA_DIAM_NM
    br.m_MinThroughDrill = BGA_VIA_DRILL_NM
    br.m_ViasMinAnnularWidth = 50_000            # 0.05 mm via-in-pad annular
    br.m_BlindBuriedViaAllowed = True
    br.m_MicroViasMinSize = BGA_VIA_DIAM_NM       # 0.20 mm microvia
    br.m_MicroViasMinDrill = BGA_VIA_DRILL_NM     # 0.10 mm laser drill




def _bga_fanout(board, x0, y0, routed, fps=None):
    """HDI via-in-pad fanout for the 0.8 mm-pitch FPGA BGA (CSG324) on the
    6-layer stackup. Each netted ball ties to its net by via TYPE (see the
    PLANE_NETS / POWER_RAIL_LAYER / column-parity notes above):

      * GND  -> microvia  F.Cu->In1.Cu  (no signal-layer keepout)
      * +3V3 -> blind via F.Cu->In2.Cu  (no signal-layer keepout)
      * +1V0/+1V8 -> through via-in-pad tying to the rail layer (In3/In4)
      * signal/config/JTAG/VREFP -> through via-in-pad escaping on In3.Cu
        (even column) / In4.Cu (odd column). EVERY netted signal ball gets one
        (F.Cu escape is impossible under the 0.8 mm pad field).

    Returns (vip_net, pad_vip, vip_vias):

      vip_net : net_name -> set of (ix,iy,layer) cells (radius
                BGA_VIP_MARK_RADIUS) on the net's escape/rail layer; added to
                that net's ``allowed`` override so its own track can pass its
                via-in-pad area while OTHER nets are kept clear. PLANE_NETS
                (GND/+3V3) are absent -- their microvias/blind vias do not
                occupy any signal layer, so no keepout is needed.
      pad_vip : (x,y) pad-centre -> (esc_lay, seed_cells) for SHADOWED SIGNAL
                balls; tells _route_pair which inner layer to escape on. Power
                and plane balls are NOT here (no _route_pair escape).
      vip_vias: net_name -> [PCB_VIA, ...] for every via placed. build() KEEPS
                them all (even for nets the line router failed to escape) so the
                later FreeRouting pass has a connection point on In3/In4/B.Cu.
                via_dangling is `ignore`, so an unclosed VIP is DRC-clean.
                PLANE-net (GND/+3V3) and POWER-net (+1V0/+1V8) vias tie to their
                plane / rail and are likewise kept.

    Through-via barrels mark keepout in ``routed`` on the inner signal layers +
    B.Cu (radius BGA_VIP_MARK_RADIUS on the escape/rail layer, MARK_RADIUS on
    the others; F.Cu is skipped -- the BGA pad's 0.3 mm PAD_KEEPOUT already
    protects the barrel there). Unnetted balls stay bare (spare IO).
    """
    fpga = next((fp for fp in (fps if fps is not None else board.GetFootprints())
                 if fp.GetReference() == BGA_REF), None)
    if fpga is None:
        return {}, {}, {}
    # Only the FPGA BGA (CSG324, 324 pads) gets HDI via-in-pad fanout. Guard on
    # pad count: < 100 pads is not the FPGA BGA (the QSSC_Chip die faces are
    # fine-pitch QFN-style edge-pad dies, handled by _chip_fanout, not here).
    if len(list(fpga.Pads())) < 100:
        return {}, {}, {}
    pads = list(fpga.Pads())
    netted = [p for p in pads if p.GetNetname()]
    # BGA column origin for column-parity escape-layer assignment (even col ->
    # In3.Cu, odd col -> In4.Cu). Same-layer escape VIPs then sit 2 columns
    # (1.0 mm) apart -- wide enough to thread with BGA_VIP_MARK_RADIUS.
    xs_all = sorted({p.GetPosition().x for p in netted})
    x_min = xs_all[0] if xs_all else 0

    # Group ALL netted balls by row (incl. GND/+3V3) so a signal ball shadowed
    # by a plane ball is still detected as shadowed.
    by_row = {}
    for p in netted:
        by_row.setdefault(p.GetPosition().y, []).append(p)

    def _microvia(px, py, p, top, bot):
        """F.Cu->bot single-dielectric microvia (laser drill, 0.10 mm)."""
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I(int(px), int(py)))
        v.SetNetCode(p.GetNetCode())
        v.SetViaType(pcbnew.VIATYPE_MICROVIA)
        v.SetDrill(BGA_VIA_DRILL_NM)
        v.SetWidth(BGA_VIA_DIAM_NM)
        ls = pcbnew.LSET()
        ls.AddLayer(top); ls.AddLayer(bot)
        v.SetLayerSet(ls)
        board.Add(v)
        return v

    def _blind_via(px, py, p, layers):
        """F.Cu->In2 blind via (through In1, anti-pad carved by the GND plane).
        Slightly larger than the microvia (0.30 mm / 0.15 mm) per HDI norm."""
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I(int(px), int(py)))
        v.SetNetCode(p.GetNetCode())
        v.SetViaType(VIATYPE_BLIND_BURIED)   # compat: >=10 split BLIND/BURIED
        v.SetDrill(150_000)             # 0.15 mm blind drill
        v.SetWidth(300_000)             # 0.30 mm blind via
        ls = pcbnew.LSET()
        for lay in layers:
            ls.AddLayer(lay)
        v.SetLayerSet(ls)
        board.Add(v)
        return v

    def _through_vip(px, py, p):
        """0.20/0.10 mm through via-in-pad (reaches every copper layer)."""
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I(int(px), int(py)))
        v.SetNetCode(p.GetNetCode())
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetDrill(BGA_VIA_DRILL_NM)
        v.SetWidth(BGA_VIA_DIAM_NM)
        board.Add(v)
        return v

    vip_net, pad_vip, vip_vias = {}, {}, {}
    # cell -> set of netcodes whose VIP barrel mark (r2 on In3/In4/B.Cu) covers
    # it. A cell in the r2 marks of TWO DIFFERENT nets -- the 0.3 mm overlap in
    # the gap between two 0.5 mm-pitch neighbour balls -- is a cell the per-net
    # vip_net allowed override must NEVER relax: a track or via-up centred there
    # is ~0.3 mm from the neighbour's VIP barrel -> 0.10 mm clearance (the
    # CONF_DONE via vs CONF_INIT track on B.Cu; TIA_ADC vs VREFP). This is the
    # VIP-side twin of multi_keepout: both stop a per-net override from
    # relaxing a cell a neighbour already owns.
    vip_cell_nets: dict[tuple, set] = {}
    for y, rowpads in by_row.items():
        for p in rowpads:
            nm = p.GetNetname()
            x = p.GetPosition().x
            ix = int((x - x0) // GRID_NM)
            iy = int((y - y0) // GRID_NM)

            # --- PLANE nets: microvia / blind via, NO signal-layer keepout ---
            if nm == "GND":
                v = _microvia(x, y, p, pcbnew.F_Cu, pcbnew.In1_Cu)
                vip_vias.setdefault(nm, []).append(v)
                continue
            if nm == "+3V3":
                v = _blind_via(x, y, p, (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu))
                vip_vias.setdefault(nm, []).append(v)
                continue

            # --- power rails (+1V0/+1V8): through via tying to a fixed rail layer
            if nm in POWER_RAIL_LAYER:
                esc_lay = POWER_RAIL_LAYER[nm]
                v = _through_vip(x, y, p)
            else:                                 # signal / config / JTAG / VREFP
                # EVERY netted signal ball gets a through via-in-pad escaping on
                # In3.Cu (even column) / In4.Cu (odd column). The old code skipped
                # "unshadowed" rightmost-column balls (no netted ball 0.8 mm to the
                # right in the same row) and left them for an F.Cu escape -- but an
                # F.Cu escape is impossible under the dense 0.8 mm CSG324 pad field,
                # so FreeRouting could never close those nets (16 stuck FPGA escape
                # nets: TMS/TDO/adc_cs2/adc_miso/...). Giving every signal ball a
                # VIP + keepout UP FRONT means the rail router (Task 7) and the
                # signal router route AROUND it. Adding the vias post-hoc (the
                # bga_fanout_fix.py approach) shorted the +1V8/+1V0 rail tracks on
                # In3/In4 because those rails were routed before the via existed --
                # the keepout grid only protects a via placed BEFORE routing.
                col = (x - x_min) // BGA_PITCH_NM
                esc_lay = IN3_CU if (col % 2 == 0) else IN4_CU
                v = _through_vip(x, y, p)

            # THROUGH barrel keepout on the inner signal layers + B.Cu. F.Cu is
            # NOT marked (BGA pad 0.3 mm PAD_KEEPOUT already protects the barrel
            # there; marking it needlessly blocks non-BGA F.Cu routes past the
            # BGA). ALL non-F.Cu signal layers get the tight BGA_VIP_MARK_RADIUS
            # (2 = 0.4 mm): the VIP barrel is 0.20 mm, and on the 0.2 mm grid the
            # closest free cell to a radius-2 mark is 0.6 mm from the barrel
            # centre -- >= the 0.5 mm a 0.5 mm power track needs (w/2 0.25 +
            # clearance 0.15 + barrel 0.10), so even the widest rail stays clear.
            # The old MARK_RADIUS (3 = 0.6 mm) on non-escape layers over-blocked:
            # an opposite-parity neighbour ball (1.0 mm away on the OTHER inner
            # layer) marked THIS layer at radius 3 and reached into the escape
            # column of the ball between them, defeating the 1 mm edge escape.
            for lay in _cfg.SIGNAL_LAYERS:
                if lay == F_CU:
                    continue
                _mark(routed, ix, iy, lay, BGA_VIP_MARK_RADIUS)
            r = BGA_VIP_MARK_RADIUS
            # The VIP is a THROUGH via -- its barrel ties a's pad to EVERY signal
            # layer, not just esc_lay. Seed the net's `allowed` override on all
            # non-F.Cu signal layers (F.Cu is not marked in `routed` -- the BGA
            # pad's own-cells handle it) so the net's own track can ESCAPE its VIP
            # on B.Cu or the other inner layer when esc_lay is congested (Task 5).
            cells = {(ix + dx, iy + dy, lay)
                     for lay in _cfg.SIGNAL_LAYERS if lay != F_CU
                     for dx in range(-r, r + 1) for dy in range(-r, r + 1)}
            vip_net.setdefault(nm, set()).add((ix, iy, esc_lay))
            vip_net[nm] |= cells
            nc = p.GetNetCode()
            key = nc if nc is not None and nc >= 0 else -1
            for c in cells:
                vip_cell_nets.setdefault(c, set()).add(key)
            # Both signals AND the +1V0/+1V8 power rails get an escape pad on
            # their rail layer: the rail's 0.5 mm track (Task 7) daisy-chains the
            # through-via barrels on In3.Cu (+1V0) / In4.Cu (+1V8) and reaches the
            # regulator output via a through-via hop. Without this entry the rail
            # balls would be TIE-only (no escape) and the rail could never connect
            # to the regulator -> +1V0/+1V8 would stay unconnected.
            pad_vip[(int(x), int(y))] = (esc_lay, cells)
            vip_vias.setdefault(nm, []).append(v)
    # Strip the contested overlap cells from every net's allowed override: a
    # VIP cell two nets' marks share is never safe for either's own track/via.
    multi_vip = {c for c, nets in vip_cell_nets.items() if len(nets) > 1}
    for nm in vip_net:
        vip_net[nm] -= multi_vip
    return vip_net, pad_vip, vip_vias




def _chip_fanout(board, x0, y0, routed, fps=None):
    """Dog-bone fanout for a QSSC_Chip die FACE.  The die carries an 8x8 GRID of
    64 electrodes across the 20x20 mm face at 2.54 mm pitch (1.7 mm THT pads ->
    0.84 mm inter-pad gaps, 17.78 mm span).  The main board carries
    ChipSocket_2.54_EM (64 EM cathodes + EM_ANODE on a 2.54 mm female-header
    socket on F.Cu); the detector head carries ChipSocket_2.54_DET (64 DET
    electrodes on the matching socket on B.Cu).

    Each netted die pad gets a short 0.15 mm stub on the pad's OWN layer (F.Cu
    for EM, B.Cu for DET) outward toward the nearest die edge, to a 0.20 mm
    through-via 1.0 mm out (in the 0.84 mm inter-pad gap).  At 2.54 mm pitch the
    via clears the adjacent-row/col pad by ~0.54 mm (>> 0.15 mm netclass), so NO
    shorting -- the 68/98 shorting_items seen in the OLD 0.30 mm-pitch design
    (where a 1.0 mm stub blew PAST the 0.30 mm-pitch neighbour) cannot occur
    here.  The through-via barrel ties the pad to the OPPOSITE surface (B.Cu for
    EM, F.Cu for DET), which is CLEAR of die pads (the die sits on one layer per
    board), so the line router escapes via->target (TLC5947 sink / TIA / ADC
    input) on that clear layer.  NO fixed 3 mm escape is
    pre-routed: that fixed escape was the short source (adjacent-row escapes on
    the same column overlapped on the escape layer).  The line router (_route_pair,
    via the via_pads pseudo-pad + pad_vip entry) routes from the via on the clear
    opposite layer, marking keepout so no two nets' escapes overlap.

    EM_ANODE (+12V_EMIT, a POWER_NET) is NOT fanned out here -- the power router
    (POWER_ROUTER_LAYER[+12V_EMIT] = B.Cu) ties it.  Fanouting it would add it to
    chip_pad_centers and hide it from the power router.

    Returns (chip_pad_centers, via_pads, vip_net, pad_vip, vip_vias):
      chip_pad_centers: set of (x,y) chip-pad centres to EXCLUDE from net_pads
        (the stub already connects the pad to its via; routing from the pad
        itself would duplicate the stub and hit the keepout overlap).
      via_pads: net_name -> [_PadProxy at the via position on the escape layer]
        -- a routing endpoint the line router connects to the TLC5947/TIA/ADC
        pad, registered in pad_vip so _route_pair escapes it on the opposite
        surface.
      vip_net / pad_vip / vip_vias: same shape as _bga_fanout's, merged by the
        caller with the BGA fanout dicts.
    """
    chip = next((fp for fp in (fps if fps is not None else board.GetFootprints())
                 if fp.GetReference() == "Uchip"), None)
    if chip is None:
        return set(), {}, {}, {}, {}
    pads = list(chip.Pads())
    if len(pads) < 60:                   # not a die face (64 DET / 65 EM+ANODE)
        return set(), {}, {}, {}, {}
    cx0, cy0 = chip.x, chip.y            # die centre = footprint origin (20x20 body)

    _skip = PLANE_NETS | POWER_NETS
    all_pads = [p for p in pads
                if p.GetNetname() and p.GetNetname() not in _skip]
    chip_pad_centers = set()
    via_pads = {}
    vip_net, pad_vip, vip_vias = {}, {}, {}
    STUB = 1_000_000                     # 1.0 mm: lands the via mid-gap (0.80 mm clr)

    def _stub(px, py, vx, vy, nc, lay):
        t = pcbnew.PCB_TRACK(board)
        t.SetStart(pcbnew.VECTOR2I(int(px), int(py)))
        t.SetEnd(pcbnew.VECTOR2I(int(vx), int(vy)))
        t.SetNetCode(nc)
        t.SetWidth(150_000)              # 0.15 mm Chip-class stub
        t.SetLayer(lay)                  # pad's own layer (F.Cu EM / B.Cu DET)
        board.Add(t)

    def _vip(vx, vy, nc):
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pcbnew.VECTOR2I(int(vx), int(vy)))
        v.SetNetCode(nc)
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetDrill(BGA_VIA_DRILL_NM)
        v.SetWidth(BGA_VIA_DIAM_NM)
        board.Add(v)
        return v

    for p in sorted(all_pads, key=lambda q: (q.GetPosition().x,
                                             q.GetPosition().y)):
        nm = p.GetNetname()
        nc = p.GetNetCode()
        px, py = p.GetPosition().x, p.GetPosition().y
        # Die-contact face from the FOOTPRINT's own layer (the socket body sits
        # on F.Cu for the main-board ChipSocket_2.54_EM, B.Cu for the head
        # ChipSocket_2.54_DET).  The pad's copper layer set cannot disambiguate
        # the new THT socket pads: a thru_hole pad is on *.Cu (every copper
        # layer), so "F_CU in cu" is true on BOTH boards and would wrongly place
        # the head's stub on F.Cu (leaving the B.Cu die pad floating and
        # inverting the escape layer).  The footprint layer is reliable here --
        # the PAD.GetLayer() flip quirk (a pad on a flipped B.Cu footprint
        # reports F_CU) does NOT affect FOOTPRINT.GetLayer(), which returns B_CU
        # for a back-side footprint.  _FpProxy stores flayer; a native FOOTPRINT
        # (fps=None path) exposes GetLayer() instead.  This also matches the old
        # ACF behaviour (F.Cu SMD pads -> F_CU; B.Cu SMD pads -> B_CU).
        fp_lay = getattr(chip, "flayer", None)
        if fp_lay is None:
            fp_lay = chip.GetLayer()
        pad_lay = B_CU if fp_lay == B_CU else F_CU
        esc_lay = B_CU if pad_lay == F_CU else F_CU  # opposite, clear surface
        # stub outward toward the nearest die edge (perpendicular to it).  For an
        # interior 8x8 pad this points toward the nearest side; the via lands in
        # the 0.84 mm gap, clear of the 2.54 mm-pitch neighbours (see docstring).
        ddx, ddy = px - cx0, py - cy0
        if abs(ddy) >= abs(ddx):
            dx, dy = 0, (1 if ddy > 0 else -1)       # top/bottom edge -> +/-y
        else:
            dx, dy = (1 if ddx > 0 else -1), 0       # left/right edge -> +/-x
        chip_pad_centers.add((int(px), int(py)))
        vx, vy = int(px + dx * STUB), int(py + dy * STUB)
        _stub(px, py, vx, vy, nc, pad_lay)
        v = _vip(vx, vy, nc)
        ix = int((vx - x0) // GRID_NM)
        iy = int((vy - y0) // GRID_NM)
        r = BGA_VIP_MARK_RADIUS
        # through-via barrel sits in the 0.84 mm gap (past the pad edge), so mark
        # its keepout on EVERY signal layer so other nets do not short to it.
        for lay in _cfg.SIGNAL_LAYERS:
            _mark(routed, ix, iy, lay, r)
        cells = {(ix + dx_, iy + dy_, lay)
                 for lay in _cfg.SIGNAL_LAYERS
                 for dx_ in range(-r, r + 1) for dy_ in range(-r, r + 1)}
        vip_net.setdefault(nm, set()).add((ix, iy, esc_lay))
        vip_net[nm] |= cells
        pad_vip[(vx, vy)] = (esc_lay, cells)
        vip_vias.setdefault(nm, []).append(v)
        # mark the 0.15 mm stub cells on the pad's own layer (other nets' routes
        # on that layer must avoid the stub).
        ixp = int((px - x0) // GRID_NM)
        iyp = int((py - y0) // GRID_NM)
        for gx_ in range(min(ixp, ix), max(ixp, ix) + 1):
            for gy_ in range(min(iyp, iy), max(iyp, iy) + 1):
                _mark(routed, gx_, gy_, pad_lay, r)
        # via_pads endpoint = the via position on the escape layer; the line
        # router (_route_pair, vip_a branch) escapes from here on esc_lay to a
        # via-up near the target, then F.Cu to the target pad.
        via_pads.setdefault(nm, []).append(_PadProxy(
            cx=vx, cy=vy, bx=vx - 100_000, by=vy - 100_000, bw=200_000, bh=200_000,
            netname=nm, netcode=nc, is_pth=False, layer=esc_lay,
            attrib=pcbnew.PAD_ATTRIB_SMD, has_hole=False, cu_layers=[esc_lay]))

    return chip_pad_centers, via_pads, vip_net, pad_vip, vip_vias


