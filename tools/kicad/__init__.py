"""tools.kicad — QSSC KiCad board generator (split package).

The board generator (component/net model, symbol library, schematic + PCB
emitters, placement) was split from the monolithic ``gen_qssc_kicad.py``
into this package. :class:`KicadGenerator` snapshots the shared
``COMPONENTS`` buffer so each board build is independent in one process.
The thin ``tools/gen_qssc_kicad.py`` shim re-exports this package's public
surface for legacy callers (``import gen_qssc_kicad as g``).
"""
from __future__ import annotations

from ._paths import ROOT, PCB_DIR, SYM_LIB, FP_LIB  # noqa: F401
from .symbols import *  # noqa: F401,F403
from .component_lib import *  # noqa: F401,F403
from .schematic import *  # noqa: F401,F403
from .pcb import *  # noqa: F401,F403
from .placement import *  # noqa: F401,F403


class KicadGenerator:
    """One QSSC board build, independent of every other build in the process.

    The ``build_*`` functions append to the module-global ``COMPONENTS``
    list in :mod:`tools.kicad.component_lib`. This class snapshots that list
    after the build (sharing the per-component dict objects by reference) and
    restores it in place (``clear``/``extend``, never reassignment) before
    placement + emission, so every binding of ``COMPONENTS`` — including the
    ``from .component_lib import COMPONENTS`` bindings in the sibling modules
    — sees the restored snapshot. Several boards can therefore be built in
    one run without the buffer being cleared between build and emit.
    """

    def __init__(self):
        self.components: list = []

    def build(self, build_fn, *args, **kwargs):
        """Run ``build_fn`` (appends to the shared COMPONENTS buffer), then
        snapshot the result into ``self.components``.

        Returns ``self`` so ``KicadGenerator().build(build_main_board)``
        chains into ``.emit(...)``.
        """
        from . import component_lib as _cl
        _cl.COMPONENTS.clear()
        build_fn(*args, **kwargs)
        self.components = list(_cl.COMPONENTS)
        return self

    def emit(self, cfg, place_fn):
        """Restore this board's snapshot, place it, and write
        ``{out_stem}.kicad_{sch,pcb,pro}`` into ``hardware/pcb/``.

        ``place_fn`` is one of the ``assign_pcb_positions*`` functions; it
        sets ``pcb_x``/``pcb_y`` on each component dict (visible in
        ``self.components`` because the dicts are shared by reference).
        """
        from . import component_lib as _cl
        _cl.COMPONENTS.clear()
        _cl.COMPONENTS.extend(self.components)
        place_fn()
        assign_sch_positions()
        stem = cfg["out_stem"]
        (PCB_DIR / f"{stem}.kicad_sch").write_text(emit_schematic(cfg) + "\n")
        print(f"wrote {stem}.kicad_sch")
        (PCB_DIR / f"{stem}.kicad_pcb").write_text(emit_pcb(cfg) + "\n")
        print(f"wrote {stem}.kicad_pcb")
        (PCB_DIR / f"{stem}.kicad_pro").write_text(emit_pro(cfg) + "\n")
        print(f"wrote {stem}.kicad_pro")
        return self


__all__ = ['KicadGenerator', 'AD7616_FP', 'BTB_PIN_NETS', 'GRID_HALF', 'GRID_N', 'GRID_PITCH', 'GVA84_NETS', 'GVA84_PINS', 'POWER_PINS', 'PWR_FLAG_def', 'SYM_NAME', 'ad7616_pin_list', 'ams1117_pins', 'ap2127k_pins', 'authored_symbols', 'chip_net_map', 'chip_net_map_det', 'chip_net_map_em', 'chip_pin_list', 'chip_pin_list_det', 'chip_pin_list_em', 'conn_02x10_pin_list', 'custom_symbol_def', 'fpga_net_map', 'fpga_pin_list', 'irlml2502_pin_list', 'lm358_pin_list', 'lmv339_pin_list', 'make_pin', 'mcp4725_pin_list', 'mcp4921_pin_list', 'power_symbol_def', 'replicated_symbol', 'simple_two_pin_vertical', 'std_pins', 'tlc5947_pin_list', 'tlv9004_pin_list', 'COMPONENTS', 'SOCKET_BODY_HALF', 'SOCKET_COMMON_X', 'SOCKET_CRTYD_HALF', 'SOCKET_MNT', 'SOCKET_MNT_DRILL', 'SOCKET_MNT_PAD', 'SOCKET_MNT_POS', 'add', 'add_btb_connector', 'build_pincoords', 'chip_footprint', 'chip_footprint_det', 'chip_footprint_em', 'chip_socket_footprint_det', 'chip_socket_footprint_em', 'conn_02x10_footprint', 'fp_fresh_uuids', 'fp_net_pads', 'fp_set_at', 'fp_set_prop', 'grad_coil_footprint', 'load_footprint_mod', 'pin_endpoint', 'assign_sch_positions', 'emit_schematic', 'ANALOG_NETS', 'EXT_CFG', 'HEAD_CFG', 'HEAD_LAYER_STACK', 'HEAD_STACK_UP', 'LAYER_STACK', 'MAIN_CFG', 'POWER_NETS', 'POWER_SYMS', 'RF_NETS', 'ROOT_UUID', 'STACK_UP', 'USB_NETS', 'all_nets', 'emit_pcb', 'emit_pro', 'net_class', 'assign_pcb_positions', 'assign_pcb_positions_gradient', 'assign_pcb_positions_head', 'build_detector_analog', 'build_detector_head', 'build_emitter_driver', 'build_gradient_board', 'build_main_board', 'ROOT', 'PCB_DIR', 'SYM_LIB', 'FP_LIB']
