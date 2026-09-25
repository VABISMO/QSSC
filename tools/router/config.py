"""Shared config for the QSSC signal router: layer sets, grid/via sizes, net
classifiers + the per-board mutable layer tuples.

``SIGNAL_LAYERS`` / ``PLANE_LAYERS`` / ``JUMP_LAYERS`` are reassigned per board
(6-layer main vs 4-layer head) by :func:`set_layer_count`, so every other module
reads them as ``_cfg.SIGNAL_LAYERS`` (attribute access on this module object)
rather than importing the value at module load. ``_FAIL_REASON`` is the per-net
failure diagnostic string written by the pair/power routers and read by the
orchestrator; it is also accessed via ``_cfg``.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

from route_qssc_pcb import F_CU, IN1_CU, IN2_CU, IN3_CU, IN4_CU, B_CU, VIP_MAX_NM  # noqa: F401

# 6-layer HDI stackup (F.Cu sig / In1.Cu GND / In2.Cu +3V3 / In3.Cu sig /
# In4.Cu sig / B.Cu sig). Reassigned per board by set_layer_count().
SIGNAL_LAYERS = (F_CU, IN3_CU, IN4_CU, B_CU)
PLANE_LAYERS = (IN1_CU, IN2_CU)
JUMP_LAYERS = (B_CU, IN3_CU, IN4_CU)

PCB_PATH = Path(
    os.environ.get(
        "QSSC_PCB_PATH",
        Path(__file__).resolve().parents[2] / "hardware" / "pcb" / "qssc.kicad_pcb",
    )
)

_DEBUG = os.environ.get("QSSC_ROUTE_DEBUG")        # per-net failure reason
_FAIL_REASON = "init"

GRID_NM = 200_000                  # 0.2 mm grid: fine enough to thread 1.27 mm
TRACK_W_NM = 200_000
PAD_KEEPOUT_NM = 300_000          # clearance + half-track
VIA_KEEPOUT_NM = 600_000          # via radius 0.3 + clearance 0.2 + margin
ROUTING_VIA_DIAM_NM = 400_000
ROUTING_VIA_DRILL_NM = 200_000
MARK_RADIUS = 3
WMAX_NM = 500_000
CLEARANCE_NM = 150_000

EXCLUDE_NETS = {"USB_DP", "USB_DM"}
_RF_NETS = {"RF_OUT", "RF_OUT_N", "RF_IN", "RF_AMP_OUT", "J3_SIG", "LF_CP", "LF_MID",
            "LF_OUT", "CPout", "VTUNE", "refclk_25m"}
_USB_NETS = {"USB_DP", "USB_DM"}
_ANALOG_NETS = {"VREFP", "VREFN"}
_NETCLASS_WIDTH_NM = {"Default": 200_000, "RF": 300_000, "Power": 500_000,
                      "USB": 200_000, "Analog": 200_000, "Chip": 150_000}

BGA_REF = "U1"
BGA_PITCH_NM = 800_000
BGA_VIA_DIAM_NM = 200_000
BGA_VIA_DRILL_NM = 100_000
BGA_VIP_MARK_RADIUS = 2

PLANE_NETS = {"GND", "+3V3"}
POWER_NETS = {"+1V0", "+1V8", "+5V", "+5V_RF", "+5V_IN",
              "+12V_EMIT", "+12V_EMIT_IN"}
POWER_RAIL_LAYER = {"+1V0": IN3_CU, "+1V8": IN4_CU}
POWER_ROUTER_LAYER = {"+1V0": IN3_CU, "+1V8": IN4_CU,
                      "+5V": B_CU, "+5V_RF": B_CU, "+5V_IN": B_CU,
                      "+12V_EMIT": B_CU, "+12V_EMIT_IN": B_CU}


def set_layer_count(copper: int) -> None:
    """Reassign the signal/plane/jump layer tuples for this board's copper count.

    The main control board is 6-layer HDI (signal F.Cu/In3.Cu/In4.Cu/B.Cu, planes
    In1.Cu/In2.Cu); the detector head is 4-layer (signal F.Cu/B.Cu, planes
    In1.Cu/In2.Cu). JUMP layers lose In3/In4 on the head (only B.Cu remains);
    the per-via escape layer is still added separately in _route_pair, so
    F.Cu<->B.Cu hops work.
    """
    global SIGNAL_LAYERS, PLANE_LAYERS, JUMP_LAYERS
    if copper <= 4:
        SIGNAL_LAYERS = (F_CU, B_CU)
        PLANE_LAYERS = (IN1_CU, IN2_CU)
        JUMP_LAYERS = (B_CU,)
    else:
        SIGNAL_LAYERS = (F_CU, IN3_CU, IN4_CU, B_CU)
        PLANE_LAYERS = (IN1_CU, IN2_CU)
        JUMP_LAYERS = (B_CU, IN3_CU, IN4_CU)


def _is_chip_net(net):
    return net.startswith("DET_IN") or net.startswith("emitter_cat")


def _net_class(net):
    if _is_chip_net(net):
        return "Chip"
    if net in _RF_NETS:
        return "RF"
    if net in _USB_NETS:
        return "USB"
    if net in _ANALOG_NETS:
        return "Analog"
    return "Default"


def _net_width_nm(net):
    return _NETCLASS_WIDTH_NM[_net_class(net)]


# Widest track on the board (Power 0.5 mm). The mark radius for a feature of
# width w is ceil((w/2 + CLEARANCE + WMAX/2)/GRID): the feature's mark covers its
# own half-width + clearance + the WIDEST possible neighbour's half-width, so
# when a later feature of ANY width (<= WMAX) checks _free against this mark its
# centre is provably >= w/2 + clearance + w_new/2 away -- the no-short-by-
# construction proof holds for mixed widths without needing the neighbour's
# width at check time.  w=0.2/0.3 -> r3, w=0.5 -> r4; a 0.4 mm via (w=0.4) -> r3.
WMAX_NM = 500_000
CLEARANCE_NM = 150_000


def _mark_radius_for_width(w_nm):
    return math.ceil((w_nm / 2 + CLEARANCE_NM + WMAX_NM / 2) / GRID_NM)

# ---- FPGA BGA fanout (HDI-lite via-in-pad, 6-layer) ----
