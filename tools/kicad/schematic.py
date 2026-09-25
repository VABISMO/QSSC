"""Schematic emitter: builds the flat ERC-clean kicad_sch from COMPONENTS.

``emit_schematic`` lays out lib_symbols + instances + labels + power symbols
+ no-connects for one board; ``assign_sch_positions`` grids the Phase 1b
front-end/driver symbols out of the origin so their pin labels do not
collapse onto one net.
"""
from __future__ import annotations

from tools.sexp import A, L, S, emit, u

from .component_lib import COMPONENTS, _PINCOORDS, build_pincoords, pin_endpoint
from .pcb import ROOT_UUID
from .symbols import authored_symbols

__all__ = ['assign_sch_positions', 'emit_schematic']


def emit_schematic(cfg):
    build_pincoords()
    lib_blocks = authored_symbols()
    lib_symbols = L(A("lib_symbols"), *lib_blocks)

    instances = []
    labels = []
    no_connects = []

    for c in COMPONENTS:
        lib_id = c["lib_id"]
        is_power = lib_id.startswith("power:")
        ref = c["ref"]
        inst = L(A("symbol"),
                 L(A("lib_id"), S(lib_id)),
                 L(A("at"), A(c["x"]), A(c["y"]), A(0)),
                 L(A("unit"), A(1)),
                 L(A("exclude_from_sim"), A("no")),
                 L(A("in_bom"), A("no" if is_power else "yes")),
                 L(A("on_board"), A("no" if is_power else "yes")),
                 L(A("dnp"), A("no")),
                 L(A("uuid"), S(u())),
                 L(A("property"), S("Reference"), S(ref), L(A("at"), A(c["x"]), A(c["y"] - 2 if not is_power else c["y"] - 1.27), A(0)),
                   L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))),
                 L(A("property"), S("Value"), S(c["value"]), L(A("at"), A(c["x"]), A(c["y"] + 2 if not is_power else c["y"] + 1.27), A(0)),
                   L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))))),
                 L(A("property"), S("Footprint"), S(c["fp"]), L(A("at"), A(c["x"]), A(c["y"]), A(0)),
                   L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))), L(A("hide"), A("yes")))))
        # pin uuids
        coords = _PINCOORDS[lib_id]
        for pnum in coords:
            inst.append(L(A("pin"), S(pnum), L(A("uuid"), S(u()))))
        inst.append(L(A("instances"),
                      L(A("project"), S(cfg["project"]),
                        L(A("path"), S("/" + ROOT_UUID),
                          L(A("reference"), S(ref)), L(A("unit"), A(1))))))
        instances.append(inst)
        # A local net label at every netted pin endpoint names the net.
        # We use labels (not power symbols) for power nets too: the `power`
        # symbol library is not configured in this headless-generated project,
        # so power:+X symbols are not recognised as power symbols and do NOT
        # name nets by value — every rail came up power_pin_not_driven.  Local
        # labels connect by name within the sheet and DO drive power_in pins
        # (verified empirically against kicad-cli ERC).  Each rail that lacks a
        # power_out driver (LDO VO) gets a PWR_FLAG component below, whose
        # power_out pin sits on the same labelled rail net.
        for pnum, net in c["pins"].items():
            if pnum not in coords:
                continue
            px, py, _ = pin_endpoint(c, pnum)
            lab = L(A("label"), S(net),
                    L(A("at"), A(round(px, 4)), A(round(py, 4)), A(0)),
                    L(A("fields_autoplaced"), A("yes")),
                    L(A("effects"), L(A("font"), L(A("size"), A(1.27), A(1.27))),
                      L(A("justify"), A("left"), A("bottom"))),
                    L(A("uuid"), S(u())))
            labels.append(lab)
        # (no_connect) markers at every documented-unused pin.  The schematic is
        # 100% net-label (0 wires), so without these the intentionally-floating /
        # unused pins render as dangling and ERC reports pin_not_connected.  A
        # no_connect at the pin endpoint is the KiCad-correct way to declare "this
        # pin is deliberately left open per the datasheet" — it silences
        # pin_not_connected for that pin without adding copper.  Keyed off the
        # per-component `nc_pins` list (FT232 modem/crystal/LED pins, ADF4351 unused
        # RF outputs + SW + CE, AD7616 parallel-DB bus in 1-wire HW mode, TLC5947
        # chip2 unused OUT16-23 + SOUT, FPGA spare/XADC balls).
        for pnum in c.get("nc_pins", []):
            if pnum not in coords:
                continue
            px, py, _ = pin_endpoint(c, pnum)
            nc = L(A("no_connect"),
                   L(A("at"), A(round(px, 4)), A(round(py, 4))),
                   L(A("uuid"), S(u())))
            no_connects.append(nc)

    sch = L(A("kicad_sch"),
            L(A("version"), A(20231120)),
            L(A("generator"), S("gen_qssc_kicad")),
            L(A("generator_version"), S("8.0")),
            L(A("uuid"), S(ROOT_UUID)),
            L(A("paper"), S("A1")),
            L(A("title_block"), L(A("title"), S(cfg["title"])), L(A("company"), S("QSSC"))),
            lib_symbols,
            *instances,
            *labels,
            *no_connects,
            L(A("sheet_instances"), L(A("path"), S("/"), L(A("page"), S("1")))))
    return emit(sch)


def assign_sch_positions():
    """Schematic (x,y) for the Phase 1b front-end + driver symbols.  They were
    add()-ed at (0,0); left there, every symbol stacks at the origin and its pin
    labels land on coincident points -- KiCad then merges those points into one
    net (verified: 19k 'Output+Output' ERC errors).  Grid them out on the A1
    sheet right of the existing (x<350) schematic content.  Spacing > 2x the
    symbol pin reach (~10mm) so no two distinct nets share a pin endpoint."""
    new = [c for c in COMPONENTS if c["ref"] in ("Rref1", "Rref2", "Cref",
                                                  "FB_A", "C_Abulk", "C_ahf")
           or any(c["ref"].startswith(p) and c["ref"][len(p):].isdigit()
                  for p in ("UT", "UAD", "Cua", "CuaB", "Cud", "Cur", "Crio",
                            "Crgc", "Crgd", "Rf", "Cf", "Cut",
                            "UE", "Riref", "Cem"))]
    # Gradient parts (qssc-grad board only): gridded into the same
    # x>=420 free area so they cannot collide with the x<410 RF/USB/FPGA content.
    grad_refs = ("Ugrad", "Udrv", "Qgrad", "Rsense", "Rgrad_feed", "Lgrad",
                 "C_grad1", "C_grad2")
    new += [c for c in COMPONENTS if c["ref"] in grad_refs]
    cols, step_x, step_y = 19, 22.0, 20.0
    for idx, c in enumerate(new):
        r, col = idx // cols, idx % cols
        c["x"] = 420 + col * step_x        # clear right of existing x[20,410]
        c["y"] = 20 + r * step_y
        c["rot"] = 0


