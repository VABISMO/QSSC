"""PCB emitter: net classes, layer stacks, per-board config + the
kicad_pcb / kicad_pro writers.

``emit_pcb`` walks ``COMPONENTS`` (restored into the shared buffer by
``KicadGenerator``) and emits a placed + netted board; ``emit_pro`` patches
the project template with the board's net classes + design rules. The
``*_CFG`` dicts are the per-board configurations consumed by the emitters.
"""
from __future__ import annotations

from tools.sexp import A, L, S, emit, u

from ._paths import PCB_DIR
from .component_lib import (
    COMPONENTS, fp_fresh_uuids, fp_net_pads, fp_set_at, fp_set_prop,
    load_footprint_mod,
)

__all__ = ['ANALOG_NETS', 'EXT_CFG', 'HEAD_CFG', 'HEAD_LAYER_STACK', 'HEAD_STACK_UP', 'LAYER_STACK', 'MAIN_CFG', 'POWER_NETS', 'POWER_SYMS', 'RF_NETS', 'ROOT_UUID', 'STACK_UP', 'USB_NETS', 'all_nets', 'emit_pcb', 'emit_pro', 'net_class']


# --------------------------------------------------------------------------
# Net classification (for PCB net classes)
# --------------------------------------------------------------------------
RF_NETS = {"RF_OUT", "RF_OUT_N", "RF_IN", "RF_AMP_OUT", "J3_SIG", "LF_CP", "LF_MID",
           "LF_OUT", "CPout", "VTUNE", "refclk_25m"}
USB_NETS = {"USB_DP", "USB_DM", "USB_DP_F", "USB_DM_F"}
# Analog nets: the 64 TIA outputs (DET_TIA{i}) carry the photoluminescence
# intensity waveform to the AD7616 inputs; DET_REF is the 1.65 V TIA virtual
# ground.  These get the Analog netclass (wider clearance, no plane pour under
# the TIA->ADC run).  The XADC/VREFP/VREFN/TIA_OUT/DET_BIAS nets are GONE
# (single-board + ADC pivot).
ANALOG_NETS = {"DET_REF", "VREFP", "VREFN"}
POWER_NETS = {"+3V3", "+1V8", "+1V0", "+5V", "+5V_A", "+5V_RF", "+5V_IN", "+5V_TIA",
              "+12V_EMIT", "+12V_EMIT_IN", "GND", "3V3OUT", "VBUS_USB"}


def _is_chip_net(net):
    """The 128 QSSC_Chip die-pad signal nets: DET_IN0..63 (top edge -> TIA inputs)
    and emitter_cat0..63 (bottom edge -> TLC5947 constant-current sink outputs).
    (The two common electrodes are separate nets: EM_ANODE -> +12V_EMIT power,
    DET_COMMON -> DET_REF analog.) At the 2.5 mm die pitch / 1.2 mm pads the
    dog-bone (0.15 mm stub + 0.20 mm through-via a uniform 1.0 mm out) lands
    0.80 mm clear of the neighbour pad -- well above the 0.15 mm Default
    clearance, so no shorting items occur. The Chip netclass (0.10 mm clearance)
    was introduced for an earlier 0.30 mm-pitch die whose 0.125 mm neighbour
    clearance needed the relaxation; at 2.5 mm it is retained as the board's
    0.10 mm HDI capability floor. KiCad DRC takes the MAX of the two nets'
    netclass clearances, so the Chip class at 0.10 mm relaxes ONLY chip<->chip
    while chip<->everything-else stays 0.15 mm (max 0.10, 0.15)."""
    return net.startswith("DET_IN") or net.startswith("emitter_cat")


def net_class(net):
    if _is_chip_net(net):
        return "Chip"
    if net in RF_NETS:
        return "RF"
    if net in USB_NETS:
        return "USB"
    if net in ANALOG_NETS or net.startswith("DET_TIA"):
        return "Analog"
    if net in POWER_NETS:
        return "Power"
    return "Default"


def all_nets():
    nets = set()
    for c in COMPONENTS:
        nets.update(c["pins"].values())
    return sorted(nets)


# --------------------------------------------------------------------------
# Schematic emitter
# --------------------------------------------------------------------------
ROOT_UUID = "00000000-0000-0000-0000-000000000001"
POWER_SYMS = {"+3V3", "+1V8", "+1V0", "+5V", "+5V_A", "+5V_RF", "+12V_EMIT", "GND"}




def emit_pcb(cfg):
    nets = all_nets()
    net_index = {n: i + 1 for i, n in enumerate(nets)}  # 0 reserved for unconnected

    # board outline (cfg-driven dimensions).  board_x0/board_y0 (default 0)
    # anchor the outline away from the origin so a board can be trimmed to fit
    # its components WITHOUT shifting the component coordinates (the user's
    # manually-placed components stay at their exact saved positions; only the
    # Edge.Cuts rectangle moves).  Main board uses the default (0,0) anchor.
    W, H = cfg["board_w"], cfg["board_h"]
    X0, Y0 = cfg.get("board_x0", 0), cfg.get("board_y0", 0)
    outline = [
        L(A("gr_line"), L(A("start"), A(X0), A(Y0)), L(A("end"), A(X0 + W), A(Y0)),
          L(A("stroke"), L(A("width"), A(0.05)), L(A("type"), A("solid"))), L(A("layer"), S("Edge.Cuts")), L(A("uuid"), S(u()))),
        L(A("gr_line"), L(A("start"), A(X0 + W), A(Y0)), L(A("end"), A(X0 + W), A(Y0 + H)),
          L(A("stroke"), L(A("width"), A(0.05)), L(A("type"), A("solid"))), L(A("layer"), S("Edge.Cuts")), L(A("uuid"), S(u()))),
        L(A("gr_line"), L(A("start"), A(X0 + W), A(Y0 + H)), L(A("end"), A(X0), A(Y0 + H)),
          L(A("stroke"), L(A("width"), A(0.05)), L(A("type"), A("solid"))), L(A("layer"), S("Edge.Cuts")), L(A("uuid"), S(u()))),
        L(A("gr_line"), L(A("start"), A(X0), A(Y0 + H)), L(A("end"), A(X0), A(Y0)),
          L(A("stroke"), L(A("width"), A(0.05)), L(A("type"), A("solid"))), L(A("layer"), S("Edge.Cuts")), L(A("uuid"), S(u()))),
    ]

    # footprints
    footprints = []
    for c in COMPONENTS:
        if not c["fp"]:
            continue  # power symbols have no footprint
        fp = load_footprint_mod(c["fp"])
        fp = fp_set_at(fp, c["pcb_x"], c["pcb_y"], c.get("pcb_rot", 0))
        fp = fp_set_prop(fp, "Reference", c["ref"])
        fp = fp_set_prop(fp, "Value", c["value"])
        # The .kicad_mod template carries fixed uuids (footprint + each pad +
        # each property). Placing it N times without this pass yields N
        # identical-uuid pads across different footprints, which corrupts
        # KiCad's DRC spatial index (phantom cross-net clearance violations).
        # Regenerate every uuid in this instance; connectivity is by NET +
        # endpoint coincidence, not uuid. Falls back to adding a footprint
        # uuid for templates that ship without one.
        fp = fp_fresh_uuids(fp)
        if not any(isinstance(x, list) and x and x[0] == ("atom", "uuid") for x in fp[1:]):
            fp.insert(2, L(A("uuid"), S(u())))
        fp = fp_net_pads(fp, c["pins"], net_index)
        footprints.append(fp)

    # Copper layer stack is cfg-driven: the main board is 6-layer HDI
    # (F.Cu sig / In1.Cu GND / In2.Cu PWR / In3.Cu sig / In4.Cu sig / B.Cu sig)
    # and the detector head is also 6-layer (same stack: the 128-net die bus
    # needs the In3.Cu/In4.Cu escape layers a 4-layer stack could not provide).
    # Each entry: (kicad_id, name, type, user_label_or_None).
    copper = []
    for idx, name, typ, label in cfg["layer_stack"]:
        if label is not None:
            copper.append(L(A(idx), S(name), A(typ), S(label)))
        else:
            copper.append(L(A(idx), S(name), A(typ)))
    layers = L(A("layers"),
               *copper,
               L(A(32), S("B.Adhes"), A("user"), S("B.Adhesive")),
               L(A(33), S("F.Adhes"), A("user"), S("F.Adhesive")),
               L(A(34), S("B.Paste"), A("user")),
               L(A(35), S("F.Paste"), A("user")),
               L(A(36), S("B.SilkS"), A("user"), S("B.Silkscreen")),
               L(A(37), S("F.SilkS"), A("user"), S("F.Silkscreen")),
               L(A(38), S("B.Mask"), A("user")),
               L(A(39), S("F.Mask"), A("user")),
               L(A(44), S("Edge.Cuts"), A("user")),
               L(A(45), S("Margin"), A("user")),
               L(A(46), S("B.CrtYd"), A("user"), S("B.Courtyard")),
               L(A(47), S("F.CrtYd"), A("user"), S("F.Courtyard")),
               L(A(48), S("B.Fab"), A("user")),
               L(A(49), S("F.Fab"), A("user")))

    net_sexp = [L(A("net"), A(0), S(""))]
    for n in nets:
        net_sexp.append(L(A("net"), A(net_index[n]), S(n)))

    setup = L(A("setup"),
              L(A("pad_to_mask_clearance"), A(0)),
              L(A("allow_soldermask_bridges_in_footprints"), A("yes")),
              L(A("pcbplotparams"),
                L(A("layerselection"), S("0x00010fc_ffffffff")),
                L(A("plot_on_all_layers_selection"), S("0x0000000_00000000")),
                L(A("disableapertmacros"), A("no")),
                L(A("usegerberextensions"), A("no")),
                L(A("usegerberattributes"), A("yes")),
                L(A("usegerberadvancedattributes"), A("yes")),
                L(A("creategerberjobfile"), A("yes")),
                L(A("dashed_line_dash_ratio"), A(12.0)),
                L(A("dashed_line_gap_ratio"), A(3.0)),
                L(A("svgprecision"), A(4)),
                L(A("plotframeref"), A("no")),
                L(A("viasonmask"), A("no")),
                L(A("mode"), A(1)),
                L(A("useauxorigin"), A("no")),
                L(A("psnegative"), A("no")),
                L(A("psa4output"), A("no"))))

    # Explicit (stackup) dielectric/copper block, inserted as the FIRST child
    # of (setup ...) — KiCad's PCB parser token is `stackup` (no underscore).
    # Documents the HDI controlled-impedance intent: 0.15 mm prepreg F.Cu->In1
    # GND -> single-dielectric microvia for GND balls AND ~50ohm microstrip at
    # ~0.30 mm track width (RF netclass). DRC does not enforce impedance; this
    # is fab/impedance guidance. Omitted when cfg["stack_up"] is empty.
    if cfg["stack_up"]:
        su_children = [A("stackup")]
        for name, typ, thk, mat in cfg["stack_up"]:
            layer_items = [A("layer"), S(name), L(A("type"), S(typ)),
                           L(A("thickness"), A(thk))]
            if mat is not None:
                layer_items.append(L(A("material"), S(mat)))
            su_children.append(L(*layer_items))
        setup.insert(1, L(*su_children))

    pcb = L(A("kicad_pcb"),
             L(A("version"), A(20240108)),
             L(A("generator"), S("gen_qssc_kicad")),
             L(A("generator_version"), S("8.0")),
             L(A("general"), L(A("thickness"), A(1.6)), L(A("legacy_teardrops"), A("no"))),
             L(A("paper"), S("A4")),
             layers,
             setup,
             *net_sexp,
             *footprints,
             *outline,
             )
    return emit(pcb)


# --------------------------------------------------------------------------
# Project file (.kicad_pro) net classes + design rules
# --------------------------------------------------------------------------
# The project (.kicad_pro) template is shared by both boards: the design rules
# + net classes live here and are board-agnostic (copper layers / dimensions
# come from the .kicad_pcb, not the .kicad_pro). qssc.kicad_pro is git-tracked
# so it always exists on a real checkout; the try/except is a safety net for a
# pristine tree that has not yet generated it.
try:
    PRO_TEMPLATE = (PCB_DIR / "qssc.kicad_pro").read_text()
except FileNotFoundError:
    PRO_TEMPLATE = None


def emit_pro(cfg=None):
    import json as _j
    # Default/Analog/Power netclass clearance is per-board (cfg["analog_clearance"]):
    # the head's dense 4x4 TIA feedback grid routes at 0.10 mm and needs the 0.10 mm
    # board-min floor; the main board's sparser analog keeps the 0.15 mm design
    # intent. Chip is always 0.10 mm; RF/USB are always 0.15 mm. See MAIN_CFG /
    # HEAD_CFG and the per-class comments below.
    analog_clearance = (cfg or {}).get("analog_clearance", 0.15)
    if PRO_TEMPLATE is not None:
        proj = _j.loads(PRO_TEMPLATE)
        # meta.filename must follow THIS board's stem: the template is the
        # shipped main project, so a head emit would otherwise stamp
        # "qssc.kicad_pro" into chip_module.kicad_pro (round-7 audit).
        if cfg and cfg.get("out_stem"):
            proj.setdefault("meta", {})["filename"] = f"{cfg['out_stem']}.kicad_pro"
    else:
        # Minimal fallback so a pristine tree can still emit a .kicad_pro.
        proj = {
            "board": {"design_settings": {"rule_severities": {}, "rules": {}}},
            "erc": {"rule_severities": {}},
            "net_settings": {"classes": [], "netclass_patterns": []},
        }
        proj["board"]["design_settings"].setdefault("rule_severities", {})
    # ERC severities: the headless skeleton intentionally leaves many pins
    # unconnected (FPGA BGA balls, ADF4351/FT232 status & control lines,
    # op-amp unused sections).  Per the design brief these are acceptable as
    # warnings.  power_pin_not_driven is kept as an error and fixed for real
    # (every rail has a power_out driver).  lib_symbol_issues (the `power`/
    # `qssc` libs are not in the project lib table) and endpoint_off_grid (the
    # authored symbols use a non-1.27mm pin grid) are noise, not defects.
    proj.setdefault("erc", {})
    rs = proj["erc"].setdefault("rule_severities", {})
    rs["pin_not_connected"] = "warning"
    rs["pin_not_driven"] = "warning"
    rs["label_dangling"] = "warning"
    rs["endpoint_off_grid"] = "warning"
    rs["lib_symbol_issues"] = "ignore"
    ds = proj["board"]["design_settings"]
    # unconnected_items = error: BOTH committed projects carry the
    # real-DRC-gate setting (every split reported by the GUI and by a plain
    # kicad-cli run, not only under --severity-all), so a regeneration must
    # reproduce it. The old "ignore" here silently reverted that hand-applied
    # gate on every fresh emit -- the same artifact-vs-generator class as
    # issue #168-B (round-7 audit; pinned by tests/test_generator_freshness).
    ds["rule_severities"]["unconnected_items"] = "error"
    # via_dangling / track_dangling fire on the partially-routed design-artifact
    # board's unrouted and partially-routed nets: an orphan via layer (e.g. a
    # BGA via-in-pad on a net that never escaped, or a power ball whose plane
    # pour is a deferred manual/GUI step) and stub track ends. They are the same
    # incompleteness class as unconnected_items -- the board is synth-placed +
    # first-pass-routed, not fabricated -- so demote them so they don't mask the
    # real electrical errors (shorts, clearance, courtyard) that ARE still caught.
    ds["rule_severities"]["via_dangling"] = "ignore"
    ds["rule_severities"]["track_dangling"] = "ignore"
    ds["rule_severities"]["missing_courtyard"] = "ignore"
    ds["rule_severities"]["lib_footprint_issues"] = "ignore"
    ds["rule_severities"]["lib_footprint_mismatch"] = "ignore"
    # Silk and edge clearances are cosmetic on a placed-but-unrouted board.
    # silk_over_copper / silk_overlap fire constantly on dense SMD clusters;
    # silk_edge_clearance and copper_edge_clearance fire on the edge-mount
    # connectors (barrel jack J2, USB-C J10, SMA J3) whose pads are deliberately
    # at the board edge.  Demote them so they don't mask real electrical errors.
    ds["rule_severities"]["silk_over_copper"] = "ignore"
    ds["rule_severities"]["silk_overlap"] = "ignore"
    ds["rule_severities"]["silk_edge_clearance"] = "ignore"
    ds["rule_severities"]["copper_edge_clearance"] = "ignore"
    ds["rule_severities"]["text_height"] = "ignore"
    ds["rule_severities"]["text_thickness"] = "ignore"
    # holes_co_located fires on the headless line router's occasional duplicate
    # via of the SAME net at the same grid cell (e.g. two DET_TIA tie vias that
    # both snapped to one 0.2 mm grid point). Two same-net drills at one spot are
    # benign -- the fab drills the hole once -- so demote it as a manufacturing
    # warning, not an electrical error. (Different-net co-locations would still
    # be caught as shorting_items / clearance, which stay hard errors.)
    ds["rule_severities"]["holes_co_located"] = "ignore"
    ds["rules"]["min_clearance"] = 0.10
    # 0.10 mm (4 mil) board min clearance -- standard HDI capability, same class
    # as the 0.10 mm laser drills already used. The netclass clearances stay at
    # 0.15 mm (see below), so every net-pair is governed by its 0.15 mm netclass
    # EXCEPT chip<->chip: the "Chip" netclass is 0.10 mm. At the current 2.5 mm
    # die pitch / 1.2 mm pads the dog-bone vias (0.20 mm via 1.0 mm out) clear
    # the neighbour by 0.80 mm -- far above 0.15 mm -- so the Chip class is not a
    # routing necessity there; it is retained as the board's 0.10 mm HDI floor
    # (it was a necessity for the earlier 0.30 mm-pitch die whose 0.125 mm
    # neighbour clearance needed < 0.15 mm). The board min_clearance is the
    # floor; 0.10 mm only unleashes the chip dog-bones -- all other pairs keep
    # 0.15 mm (their netclass > board min).
    # min_track_width 0.10 mm = the HDI 4-mil floor.
    ds["rules"]["min_track_width"] = 0.10
    # HDI via floors -- these MUST agree with route_signals._relax_board_for_hdi
    # (m_ViasMinSize = BGA_VIA_DIAM_NM = 0.20 mm, m_ViasMinAnnularWidth = 0.05 mm).
    # The board uses 0.20 mm / 0.10 mm drill via-in-pad (filled+capped) microvias for
    # the 0.5 mm-pitch CSG324 BGA and 0.30 mm-pitch QSSC_Chip dog-bone fanout, and
    # 0.40 mm / 0.20 mm drill routing vias for general signal escape. A 0.45 mm /
    # 0.10 mm floor (a non-HDI assumption) would flag every one of those as a
    # via_diameter / annular_width violation. 0.20 mm / 0.05 mm is the HDI
    # floor: the 0.40 mm routing vias clear it with margin, the 0.20 mm VIPs sit
    # exactly on it (KiCad uses >=), and the 0.05 mm annular is the via-in-pad
    # figure (the pad copper, not the via ring, provides the annular -- VIPs are
    # filled+capped). This keeps the pro design rules and the router's in-memory
    # relaxation in lockstep so DRC is consistent whichever authority is consulted.
    ds["rules"]["min_via_diameter"] = 0.20
    ds["rules"]["min_via_annular_width"] = 0.05
    ds["rules"]["min_hole_clearance"] = 0.15
    # 0.20 mm hole-to-hole matches the hand-routed main board (qssc.kicad_pro),
    # whose dense 0.10 mm-drill HDI vias (e.g. adjacent emitter_cat dog-bones at
    # ~0.20-0.25 mm edge-to-edge) were DRC'd to 0/0/0 at this value.  The old
    # 0.25 mm default tripped 3 hole_near_hole errors on those same vias when
    # re-injected into qssc-grad, so both boards must share the main board's
    # validated floor.
    ds["rules"]["min_hole_to_hole"] = 0.20
    # 0.15 mm (6 mil) board min hole_clearance -- the floor for drill-hole-to-copper
    # of a DIFFERENT net. At the current 2.5 mm die pitch the QSSC_Chip dog-bone
    # vias (0.20 mm via 1.0 mm out) clear the neighbour pad by 0.80 mm, so 0.15 mm
    # is met with large margin. (The earlier 0.30 mm-pitch die had 0.175 mm
    # drill-to-neighbour-stub, under the old 0.18 mm floor but clearing 0.15 mm
    # by 0.025 mm -- the floor was lowered to 0.15 mm for it; at 2.5 mm the margin
    # is wide.) Non-chip vias (0.8 mm-pitch CSG324 BGA VIPs, GND/+3V3 stitching)
    # are on much coarser pitches so 0.15 mm is safe board-wide. Same
    # floor-unleashes-chip pattern as min_clearance above.
    ds["rules"]["min_copper_edge_clearance"] = 0.3
    nc = proj["net_settings"]["classes"]
    nc.clear()
    # NOTE: netclass *clearance* for Default/Analog/Power is the per-board
    # ``analog_clearance`` (0.15 mm main, 0.10 mm head -- see cfg); RF/USB are
    # 0.15 mm and Chip is 0.10 mm on both.  KiCad applies a netclass clearance
    # override between ANY two pads of that net -- INCLUDING adjacent pads inside
    # the same fine-pitch footprint (QFN-32 0.5 mm pitch, USB-C, SSOP-28).  A
    # larger per-class clearance (e.g. 0.30 for Power) is therefore unsatisfiable:
    # the QFN pad gap is ~0.25 mm, so every Power/RF pad next to a Default pad
    # inside the ADF4351 or GVA-84+ flags a clearance violation that no placement
    # can fix.  The impedance intent for RF/USB/Power is instead carried by the
    # per-class track_width (0.30/0.20/0.50 mm) and is enforced during copper
    # routing (one GUI pcbnew session), not by a pad-clearance rule.  The head
    # drops Default/Analog/Power to the 0.10 mm board-min floor because its 4x4
    # TIA feedback grid routes at 0.10 mm; the main board's sparser analog clears
    # 0.15 mm.  See HARDWARE_DESIGN.md §10/§15.
    nc.append({"bus_width": 12, "clearance": analog_clearance, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
               "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.2, "microvia_drill": 0.1,
               "name": "Default", "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
               "track_width": 0.2, "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6})
    nc.append({"bus_width": 12, "clearance": 0.15, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
               "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.2, "microvia_drill": 0.1,
               "name": "RF", "pcb_color": "rgba(255, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
               "track_width": 0.3, "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6})
    nc.append({"bus_width": 12, "clearance": analog_clearance, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
               "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.2, "microvia_drill": 0.1,
               "name": "Power", "pcb_color": "rgba(0, 0, 255, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
               "track_width": 0.5, "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6})
    nc.append({"bus_width": 12, "clearance": 0.15, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
               "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.2, "microvia_drill": 0.1,
               "name": "USB", "pcb_color": "rgba(0, 255, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
               "track_width": 0.2, "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6})
    nc.append({"bus_width": 12, "clearance": analog_clearance, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
               "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.2, "microvia_drill": 0.1,
               "name": "Analog", "pcb_color": "rgba(255, 255, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
               "track_width": 0.2, "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6})
    # Chip netclass: the 128 QSSC_Chip die-pad signal nets (DET_IN0..63,
    # emitter_cat0..63). At the current 2.5 mm die pitch / 1.2 mm pads the
    # dog-bone vias (0.20 mm via 1.0 mm out) clear the neighbour by 0.80 mm, so
    # this 0.10 mm clearance (vs the 0.15 mm board default) is NOT a routing
    # necessity -- it is retained as the board's HDI capability floor (it was a
    # necessity for the earlier 0.30 mm-pitch die, whose 0.125 mm neighbour
    # clearance needed < 0.15 mm and whose 0.15 mm stub did not clear a
    # 0.30 mm-pitch neighbour). It relaxes ONLY chip<->chip; chip<->other nets
    # keep 0.15 mm (KiCad takes the max of the two netclass clearances).
    # Track width 0.10 mm = the HDI floor (no auto-narrowing needed at 2.5 mm).
    nc.append({"bus_width": 12, "clearance": 0.10, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
               "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.2, "microvia_drill": 0.1,
               "name": "Chip", "pcb_color": "rgba(0, 255, 255, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
               "track_width": 0.10, "via_diameter": 0.2, "via_drill": 0.1, "wire_width": 6})
    # netclass patterns: assign nets by prefix
    patterns = []
    for n in all_nets():
        cls = net_class(n)
        if cls != "Default":
            patterns.append({"netclass": cls, "pattern": n})
    proj["net_settings"]["netclass_patterns"] = patterns
    return _j.dumps(proj, indent=2)


# --------------------------------------------------------------------------
# PCB placement plan (mm) — assigns pcb_x/pcb_y to each component
# --------------------------------------------------------------------------
# Board dimensions live in MAIN_CFG / HEAD_CFG below.  Main board 210x140
# (FPGA + emitter + RF + USB + power + die emitter face); head board 150x140
# (TIA + AD7616 + die detector face).  Die centre, FPGA right (main), front-end
# band (head), TLC5947 below the die (main).

# Copper layer stack for the MAIN control board (6-layer HDI). Each entry:
# (kicad_layer_id, name, type, user_label_or_None). Stackup:
#   F.Cu  sig | In1.Cu GND | In2.Cu +3V3 | In3.Cu sig | In4.Cu sig | B.Cu sig
# The two planes (In1 GND, In2 +3V3) are ADJACENT to F.Cu so the 117 FPGA power
# balls tie to them with SINGLE-dielectric microvias / short blind vias that do
# NOT reach In3.Cu/In4.Cu/B.Cu -- those inner signal layers stay clear for BGA
# signal escapes. (A through-via on every power ball -- the original "HDI-lite"
# plan -- blocked the inner layers solid at 0.5 mm pitch: 117 barrels overlap.)
# The detector HEAD board uses a separate 6-layer stack (HEAD_LAYER_STACK) -- the
# same 6-layer schedule as the main board (the 128-net die bus needs the
# In3.Cu/In4.Cu escape layers a 4-layer stack could not provide).
LAYER_STACK = [
    (0,  "F.Cu",   "signal", None),
    (1,  "In1.Cu", "signal", "GND"),
    (2,  "In2.Cu", "power",  "PWR"),
    (3,  "In3.Cu", "signal", None),
    (4,  "In4.Cu", "signal", None),
    (31, "B.Cu",   "signal", None),
]

# Explicit dielectric/copper (stack_up) block for the MAIN board: 6-layer 1.6mm
# FR4 HDI. Thin 0.15 mm prepreg F.Cu->In1.Cu (GND) gives a single-dielectric
# microvia for the 74 GND balls AND ~50ohm microstrip at ~0.30 mm track width
# (RF netclass) over the GND plane. The 0.15 mm dielectric 2 (In1->In2) lets the
# ~30 +3V3 balls use a 2-layer blind via F.Cu->In2 (anti-pad in the GND plane).
# In3.Cu/In4.Cu/B.Cu are the pad-free inner signal escape layers. Each entry:
# (layer_name, type, thickness_mm, material). Empty -> (stack_up) block omitted
# (the chip module clears it).
STACK_UP = [
    ("F.Cu",         "copper",  0.035,  None),
    ("dielectric 1", "prepreg", 0.15,   "FR4"),
    ("In1.Cu",       "copper",  0.0175, None),
    ("dielectric 2", "prepreg", 0.15,   "FR4"),
    ("In2.Cu",       "copper",  0.0175, None),
    ("dielectric 3", "core",    0.50,   "FR4"),
    ("In3.Cu",       "copper",  0.0175, None),
    ("dielectric 4", "prepreg", 0.26,   "FR4"),
    ("In4.Cu",       "copper",  0.0175, None),
    ("dielectric 5", "core",    0.40,   "FR4"),
    ("B.Cu",         "copper",  0.035,  None),
]

# Copper layer stack for the DETECTOR HEAD board (6-layer). The head carries
# the 64-channel TIA + 4xAD7616 analog front-end AND the dense 64-net die
# detector bus (DET_IN die->TIA + DET_TIA TIA->ADC). On 4 layers that bus was
# at the autorouter's density ceiling (only F.Cu/B.Cu signal, 2 layers); a
# 6-layer stack adds In3.Cu/In4.Cu signal layers so route_signals' die
# dog-bone fanout + the line router have 4 signal layers to spread the bus.
# In1.Cu stays a solid GND plane (return for the TIA->ADC analog runs +
# reference for the DET_REF bias), In2.Cu the PWR plane. B.Cu carries the 64
# DET contact pads that face the die top face (head sits above the die, B.Cu
# down). route_signals.build() auto-selects the 4-signal-layer set from
# GetCopperLayerCount() (>4 -> F.Cu/In3.Cu/In4.Cu/B.Cu), no router edit needed.
HEAD_LAYER_STACK = [
    (0,  "F.Cu",   "signal", None),
    (1,  "In1.Cu", "signal", "GND"),
    (2,  "In2.Cu", "power",  "PWR"),
    (3,  "In3.Cu", "signal", None),
    (4,  "In4.Cu", "signal", None),
    (31, "B.Cu",   "signal", None),
]

# 6-layer 1.6 mm FR4 stackup for the head board. In1.Cu (GND) is one 0.15 mm
# prepreg below F.Cu so the TIA->ADC analog runs on F.Cu have a tight GND
# reference (low-impedance return, minimal crosstalk); In2.Cu is the PWR
# plane (+3V3 / +5V / +5V_A poured by net label). In3.Cu/In4.Cu are the extra
# inner signal escape layers for the 64-net die bus. Symmetric-ish build.
HEAD_STACK_UP = [
    ("F.Cu",         "copper",  0.035,  None),
    ("dielectric 1", "prepreg", 0.15,   "FR4"),
    ("In1.Cu",       "copper",  0.0175, None),
    ("dielectric 2", "prepreg", 0.15,   "FR4"),
    ("In2.Cu",       "copper",  0.0175, None),
    ("dielectric 3", "core",    0.50,   "FR4"),
    ("In3.Cu",       "copper",  0.0175, None),
    ("dielectric 4", "prepreg", 0.26,   "FR4"),
    ("In4.Cu",       "copper",  0.0175, None),
    ("dielectric 5", "core",    0.40,   "FR4"),
    ("B.Cu",         "copper",  0.035,  None),
]

# Per-board configuration consumed by emit_schematic / emit_pcb / main.
MAIN_CFG = dict(out_stem="qssc", title="QSSC Main Control Board", project="qssc",
                board_w=150, board_h=118, layer_stack=LAYER_STACK, stack_up=STACK_UP,
                # Default/Analog/Power netclass clearance. The main board's
                # analog routing (emitter cathode drivers, RF section) is sparse
                # enough to clear the 0.15 mm board-default netclass, so it keeps
                # the 0.15 mm design-intent value (netclass > 0.10 mm board min).
                analog_clearance=0.15)
# Legacy extended-main config: the separate qssc-grad board is NO LONGER
# emitted (the published main IS the extended build -- build_gradient_board()
# emits with MAIN_CFG); kept only because the legacy shim re-exports EXT_CFG.
# Outline/layer stack were always identical to MAIN (the coil trace + gradient
# SMD parts fit inside the 150x118 outline), so no geometry is lost.
EXT_CFG = dict(out_stem="qssc-grad", title="QSSC Main Control Board + Gradient",
               project="qssc-grad", board_w=150, board_h=118,
               layer_stack=LAYER_STACK, stack_up=STACK_UP,
               analog_clearance=0.15)
# Head board outline anchored at (37,12) so the manually-placed components keep
# their exact saved coordinates and only the Edge.Cuts rectangle shrinks around
# them (~3.5 mm margin past the furthest component courtyard on each side).
HEAD_CFG = dict(out_stem="chip_module", title="QSSC Detector Head", project="chip_module",
                board_x0=37, board_y0=12, board_w=110, board_h=102,
                layer_stack=HEAD_LAYER_STACK, stack_up=HEAD_STACK_UP,
                # The head carries the dense 4x4 TIA grid (16x TLV9004 + 64x Rf/Cf
                # clustered at each TIA, ~5 mm feedback loops routed at 0.10 mm
                # clearance). A 0.15 mm Analog netclass flags all 499 feedback
                # traces; the head therefore drops Default/Analog/Power to the
                # 0.10 mm board-min floor (Chip was already 0.10). RF/USB stay
                # 0.15 mm (no dense RF routing on the head). Verified DRC 0/0/0.
                analog_clearance=0.10)


