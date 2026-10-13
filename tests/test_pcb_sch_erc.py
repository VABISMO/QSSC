# tests/test_pcb_sch_erc.py
"""ERC gate test for all three schematics (``qssc.kicad_sch``,
``chip_module.kicad_sch``, ``qssc-grad.kicad_sch``).

``test_pcb_board_integrity.py`` pins the board-file integrity and the DRC
tests pin the copper-level geometry of the three routed boards -- but the
schematics themselves had NO gate at all: an ERC error (floating output,
unconnected power pin, ERC-matrix conflict) could be committed and nothing
would notice it until a human opened the sheet in eeschema.

The gate runs the real ``kicad-cli sch erc`` IN-PLACE on the committed
schematic so the sibling ``.kicad_pro`` settings load, and then pins the
violation state in three tiers -- because a naive ``violations == []`` pin is
NOT achievable and would be wrong anyway (the sheets carry 270/404/287
warnings at gate-creation time):

1. **Zero errors -- hard.** Any violation with severity ``error`` fails,
   regardless of class. The error tier is what actually kills a sheet.
2. **Class allowlist.** Every warning must come from a known, documented
   class:
   * ``footprint_link_issues`` -- environment noise: footprint libraries not
     resolvable in the *current* configuration (a bare KiCad install without
     the kicad-footprints package, and the project ships no ``fp-lib-table``
     so the custom ``qssc`` library can never resolve). Class count varies
     with the KiCad install and is deliberately NOT pinned.
   * ``endpoint_off_grid`` -- pin/wire endpoints off the connection grid: the
     locked cosmetic state of the hand-drawn sheets.
   * ``isolated_pin_label`` / ``pin_to_pin`` -- the tolerated wiring warnings
     on the main and grad sheets, each traced to a documented design state:
     ``isolated_pin_label`` is the ``mod_sync`` label (grad adds ``grad_en``),
     both documented intentional 1-pin-label nets (see RESERVED_NETS in
     test_pcb_component_audit); ``pin_to_pin`` is J10's unused USB-C SBU1/SBU2
     pins, which the schematic ties to GND via labels (the PCB pads sit on the
     GND net) -- the KiCad matrix dislikes driving that label net's PWR_FLAG
     (power output) against a bidirectional pin, which is an opinion about
     connectivity this gate does not second-guess (removing it would mean
     changing the netlist). The formerly-8-per-sheet ``pin_not_connected``
     warnings (USB-C SuperSpeed pins with no label/wire and no pads in the
     generated PCB footprint) were REPAIRED with ``no_connect`` markers, so
     the class is out of the allowlist: any recurrence now fails as an
     unknown-class warning.
   A warning from ANY other class fails -- new KiCad check classes or new
   wiring issues must be reviewed consciously, not absorbed silently.
3. **Exact semantic counts.** For the tolerated semantic classes the per-class
   count is pinned exactly per sheet. This keeps the tolerated classes from
   becoming a growing bin: a newly introduced unconnected pin shifts the
   count and fails the gate.

Skips automatically where ``kicad-cli`` is not on PATH. Upstream's
``pcb.yml`` workflow installs KiCad and would run this gate; upstream
Actions are currently disabled (billing), so pins are enforced locally.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PCB_DIR = REPO / "hardware" / "pcb"
SCHEMATICS = {
    "main": PCB_DIR / "qssc.kicad_sch",
    "head": PCB_DIR / "chip_module.kicad_sch",
    "grad": PCB_DIR / "qssc-grad.kicad_sch",
}

# Warning classes tolerated per tier-2 -- the two environment/cosmetic classes
# are NOT count-pinned (their counts depend on the KiCad installation and on
# the frozen drawing style); the tolerated semantic classes are in
# SEMANTIC_WARNING_COUNTS and ARE pinned.
ENV_COSMETIC_CLASSES = {"footprint_link_issues", "endpoint_off_grid"}
SEMANTIC_CLASSES = {"isolated_pin_label", "pin_to_pin"}

# Exact per-class warning counts for the semantic classes, measured at
# gate-creation time (KiCad 10.0.6) and re-measured after the branch's
# schematic repair (8 NC markers on J10's unused USB-C SuperSpeed pins moved
# pin_not_connected to 0; #FLG3 / the GND labels were left untouched). head
# carries none of the semantic classes.
SEMANTIC_WARNING_COUNTS = {
    "main": {"isolated_pin_label": 1, "pin_to_pin": 2},
    "grad": {"isolated_pin_label": 2, "pin_to_pin": 2},
    "head": {},
}


def _have_kicad_cli() -> bool:
    return shutil.which("kicad-cli") is not None


pytestmark = pytest.mark.skipif(not _have_kicad_cli(),
                                reason="kicad-cli not on PATH")


@pytest.mark.parametrize("name,path", sorted(SCHEMATICS.items()))
def test_sch_erc_gate(name: str, path: Path, tmp_path):
    # Run ERC IN-PLACE on the committed schematic so the sibling .kicad_pro
    # loads (a temp-dir copy falls back to default ERC settings).
    out = tmp_path / "erc.json"
    res = subprocess.run(
        ["kicad-cli", "sch", "erc", "--severity-all", "--format", "json",
         "--output", str(out), str(path)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, f"kicad-cli sch erc failed:\n{res.stderr}"

    data = json.loads(out.read_text())
    # ERC nests violations per sheet (sheets[].violations) -- there is no
    # top-level violations key.
    sheets = data.get("sheets", [])
    assert sheets, f"{name}: ERC reported no sheet inventory for {path.name}"
    violations = [v for s in sheets for v in s.get("violations", [])]

    errors = [v for v in violations if v.get("severity") == "error"]
    assert not errors, (
        f"{name}: {len(errors)} ERC ERROR(s) in {path.name} (hard gate -- the "
        f"sheets must stay error-free):\n"
        + json.dumps(errors[:10], indent=2)
    )

    by_class = Counter(v.get("type") for v in violations)
    allowed = ENV_COSMETIC_CLASSES | SEMANTIC_CLASSES
    unknown = {t: n for t, n in by_class.items() if t not in allowed}
    assert not unknown, (
        f"{name}: ERC warning class(es) unknown to this gate -- review them "
        f"and either fix the sheet or document + pin the class here: {unknown}"
    )

    semantic = {t: n for t, n in by_class.items() if t in SEMANTIC_CLASSES}
    expected = SEMANTIC_WARNING_COUNTS[name]
    assert semantic == expected, (
        f"{name}: semantic ERC warning counts changed (pinned "
        f"{expected}, now {semantic}) -- a wiring warning was added or "
        f"removed; repair the sheet or consciously re-pin the counts here."
    )