# tests/test_pcb_board_integrity.py
"""Board-file integrity gates for all three routed PCBs
(``qssc.kicad_pcb``, ``chip_module.kicad_pcb``, ``qssc-grad.kicad_pcb``) --
pure sexp/JSON parsing, no pcbnew, fast in CI.

These gates exist because the boards are GENERATED artifacts
(``tools/gen_qssc_kicad.py`` + the headless routing pipeline) and several
whole-file defects shipped before they existed:

* uuid uniqueness -- the generator used to stamp library .kicad_mod files
  verbatim, so every instance of a footprint shared its pads' uuids (every
  0603 pad on the main board had 47 copies; all 16 TLV9004 TIA pads 16x).
  KiCad resolves DRC findings uuid->item, so duplicate uuids made violation
  reports name arbitrary instances ("Pad 7 [DET_TIA57] von UT14" for a pad
  nowhere near UT14).
* min track width -- the head board shipped 41 segments at 0.1124 mm against
  a 0.15 mm board rule (caught only by a full DRC run).
* BTB pinout mirror -- J12 is THE electrical interface between the two boards
  (9-wire ADC SPI + GND/+3V3/+5V); a net renamed on one board only silently
  disconnects the sandwich.
* plane zone assignment -- the GND/+3V3 pours must sit on the inner plane
  layers, not on a signal layer.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parent.parent
PCB_DIR = REPO / "hardware" / "pcb"
BOARDS = [PCB_DIR / "qssc.kicad_pcb", PCB_DIR / "chip_module.kicad_pcb",
          PCB_DIR / "qssc-grad.kicad_pcb"]


def _text(board: Path) -> str:
    return board.read_text()


def _pro(board: Path) -> dict:
    return json.loads(board.with_suffix(".kicad_pro").read_text())


def _footprint_block(t: str, lib_pattern: str) -> str:
    """SexP text of the first footprint whose lib id matches lib_pattern."""
    m = re.search(rf'(?m)^\t\(footprint "[^"]*{lib_pattern}[^"]*"', t)
    assert m, f"footprint matching {lib_pattern!r} not found"
    nxt = re.search(r'(?m)^\t\(footprint "', t[m.end():])
    end = m.end() + nxt.start() if nxt else len(t)
    return t[m.start():end]


# ---------------------------------------------------------------------------
# uuid uniqueness (DRC report reliability)
# ---------------------------------------------------------------------------

def test_main_uuids_unique():
    _assert_uuids_unique(PCB_DIR / "qssc.kicad_pcb")


def test_head_uuids_unique():
    _assert_uuids_unique(PCB_DIR / "chip_module.kicad_pcb")


def test_grad_uuids_unique():
    if not (PCB_DIR / "qssc-grad.kicad_pcb").exists():
        pytest.skip("qssc-grad board is local-only (not published here)")
    _assert_uuids_unique(PCB_DIR / "qssc-grad.kicad_pcb")


def _assert_uuids_unique(board: Path):
    uuids = re.findall(r'\(uuid "([0-9a-f-]{36})"\)', _text(board))
    dupes = {u: n for u, n in Counter(uuids).items() if n > 1}
    assert not dupes, (
        f"{board.name}: {sum(dupes.values())} duplicate uuid occurrences "
        f"({len(dupes)} distinct) -- every board item must be addressable by "
        f"uuid, or DRC findings resolve to arbitrary items. Regenerate with "
        f"tools/gen_qssc_kicad.py (fp_restamp_uuids) or re-stamp in place."
    )


# ---------------------------------------------------------------------------
# min track width (per-board design rule)
# ---------------------------------------------------------------------------

def test_main_min_track_width():
    _assert_min_track_width(PCB_DIR / "qssc.kicad_pcb")


def test_head_min_track_width():
    _assert_min_track_width(PCB_DIR / "chip_module.kicad_pcb")


def test_grad_min_track_width():
    if not (PCB_DIR / "qssc-grad.kicad_pcb").exists():
        pytest.skip("qssc-grad board is local-only (not published here)")
    _assert_min_track_width(PCB_DIR / "qssc-grad.kicad_pcb")


def _assert_min_track_width(board: Path):
    rule = _pro(board)["board"]["design_settings"]["rules"]["min_track_width"]
    # split at segment openers; each chunk runs to the next item, so a lazy
    # DOTALL search for (width ...) stays inside THIS segment
    segs = [re.search(r'\(width ([0-9.]+)\)', chunk)
            for chunk in re.split(r'\(segment\b', _text(board))[1:]]
    assert segs, f"{board.name}: no segments parsed"
    widths = [float(m.group(1)) for m in segs if m]
    assert len(widths) == len(segs), f"{board.name}: segment without (width ...)"
    thin = [w for w in widths if w < rule - 1e-9]
    assert not thin, (
        f"{board.name}: {len(thin)} segment(s) below the {rule} mm board rule "
        f"(min found {min(widths)} mm)"
    )


# ---------------------------------------------------------------------------
# BTB J12 pinout mirror (the two-board electrical interface)
# ---------------------------------------------------------------------------

def test_btb_j12_pinout_matches_across_boards():
    main = _btb_pinnet(PCB_DIR / "qssc.kicad_pcb")
    head = _btb_pinnet(PCB_DIR / "chip_module.kicad_pcb")
    assert len(main) == 20 and len(head) == 20, (
        f"J12 must have 20 pins (main {len(main)}, head {len(head)})")
    assert main == head, (
        "J12 pin->net mismatch between the boards: "
        + str({p: (main.get(p), head.get(p))
               for p in sorted(set(main) | set(head))
               if main.get(p) != head.get(p)})
    )
    # the sandwich carries the 9-wire ADC SPI + both power rails + GND
    assert set(main.values()) >= {"GND", "+3V3", "+5V", "adc_sclk", "adc_miso"}


def _btb_pinnet(board: Path) -> dict:
    block = _footprint_block(_text(board), r"Conn_02x10_BTB")
    pins = {}
    for chunk in re.split(r'\(pad "', block)[1:]:
        num = chunk[:chunk.index('"')]
        # both spellings KiCad writes: generator-style `(net "GND")` and
        # KiCad-10 canonical `(net 133 "GND")` (index outside the quotes)
        m = re.search(r'\(net (?:\d+ )?"([^"]+)"\)', chunk)
        pins[num] = m.group(1) if m else None
    return pins


# ---------------------------------------------------------------------------
# plane zones: GND on In1.Cu, +3V3 on In2.Cu (both boards)
# ---------------------------------------------------------------------------

def test_plane_zone_assignment_main():
    _assert_plane_zones(PCB_DIR / "qssc.kicad_pcb")


def test_plane_zone_assignment_head():
    _assert_plane_zones(PCB_DIR / "chip_module.kicad_pcb")


def test_plane_zone_assignment_grad():
    if not (PCB_DIR / "qssc-grad.kicad_pcb").exists():
        pytest.skip("qssc-grad board is local-only (not published here)")
    _assert_plane_zones(PCB_DIR / "qssc-grad.kicad_pcb")


def _assert_plane_zones(board: Path):
    # zone header comes in both spellings -- generator-style
    # `(zone (net "GND") (layer "In1.Cu")` and KiCad-10 canonical
    # `(zone (net 133) (net_name "GND") (layer "In1.Cu")` after a GUI re-save
    zones = []
    for chunk in re.split(r'\(zone\b', _text(board))[1:]:
        nm = (re.search(r'\(net_name "([^"]+)"\)', chunk)
              or re.search(r'\(net "([^"]+)"\)', chunk))
        ly = re.search(r'\(layer "([^"]+)"\)', chunk)
        if nm and ly:
            zones.append((nm.group(1), ly.group(1)))
    assert len(zones) >= 2, f"{board.name}: expected >=2 plane zones, got {zones}"
    by_layer = {layer: net for net, layer in zones}
    assert by_layer.get("In1.Cu") == "GND", zones
    assert by_layer.get("In2.Cu") == "+3V3", zones