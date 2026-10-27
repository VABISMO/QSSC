"""Entry point for ``python -m tools.kicad`` (and the legacy shim).

Writes the custom symbol library once, then builds + emits the two boards
(the complete main control board -- base + field-gradient coil/DAC -- and the
detector head) as independent :class:`KicadGenerator` instances.
"""
from __future__ import annotations

from tools.sexp import A, L, S, emit

from ._paths import PCB_DIR, ROOT
from .symbols import authored_symbols
from .component_lib import build_pincoords
from .pcb import HEAD_CFG, MAIN_CFG
from .placement import (
    assign_pcb_positions_gradient,
    assign_pcb_positions_head, build_detector_head, build_gradient_board,
)
from . import KicadGenerator


def main():
    PCB_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / ".tmpwork").mkdir(exist_ok=True)
    build_pincoords()

    # Custom symbol library: authored ONCE with every symbol (both chip faces,
    # the BTB connector, and all the IC/passive symbols used by either board).
    # Both projects reference qssc-lib.kicad_sym, so it must contain the union.
    lib_syms = authored_symbols()
    libfile = L(A("kicad_symbol_lib"), L(A("version"), A(20231120)),
                L(A("generator"), S("gen_qssc_kicad")),
                L(A("generator_version"), S("8.0")),
                *lib_syms)
    (PCB_DIR / "qssc-lib.kicad_sym").write_text(emit(libfile) + "\n")
    print("wrote qssc-lib.kicad_sym")

    # ---- main control board (6-layer HDI: FPGA + emitter + RF + USB + power)
    # The published main is the COMPLETE board: build_gradient_board() calls
    # build_main_board(grad=True) and appends the field-gradient block
    # (MCP4921 DAC + servo + coil), so every feature the software exposes --
    # including SET_GRADIENT -- has hardware on this one board. There is no
    # separate third board. assign_pcb_positions_gradient = the base
    # placement plus the gradient cluster's fixed coordinates, on the same
    # 150x118 outline (MAIN_CFG).
    KicadGenerator().build(build_gradient_board).emit(MAIN_CFG,
                                                       assign_pcb_positions_gradient)

    # ---- detector head board (6-layer: TIA + AD7616 + die detector face) -----
    KicadGenerator().build(build_detector_head).emit(
        HEAD_CFG, assign_pcb_positions_head)


if __name__ == "__main__":
    main()
