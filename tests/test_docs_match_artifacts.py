# tests/test_docs_match_artifacts.py
"""Docs-vs-artifacts gate: every number the README/docs claim about the
hardware must be the number the committed artefact carries.

Every previous audit round found the same failure mode: the boards moved (or
the socket changed, or the synthesis shrank) and the prose did not. This file
pins the doc strings to the artefacts themselves, so the next drift fails CI
instead of shipping a fiction:

* the repository publishes exactly the two sandwich boards (git index);
* board geometry, stackup and the two die sockets (pcbnew);
* the XDC ball names the hardware docs quote;
* the README's three test-count lines agree with each other;
* the README's synthesis figures are SYNTH_REPORT.md's figures;
* every UART verb the README claims exists in ``command_parser.v``.

pcbnew parts skip where the system KiCad bindings are absent (CI).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HARDWARE_MD = REPO / "hardware" / "HARDWARE_DESIGN.md"
DATASHEETS_MD = REPO / "hardware" / "datasheets" / "DATASHEETS.md"
README = REPO / "README.md"
XDC = REPO / "fpga" / "constraints.xdc"
PARSER = REPO / "fpga" / "rtl" / "command_parser.v"
SYNTH = REPO / "fpga" / "synth" / "SYNTH_REPORT.md"


# --------------------------------------------------------------------------
# 1. exactly two published boards, tracked in git
# --------------------------------------------------------------------------
def test_git_publishes_exactly_the_two_sandwich_boards():
    out = subprocess.run(
        ["git", "ls-files", "hardware/pcb/*.kicad_pcb"],
        capture_output=True, text=True, cwd=REPO, check=True,
    )
    boards = sorted(p.strip() for p in out.stdout.splitlines() if p.strip())
    assert boards == ["hardware/pcb/chip_module.kicad_pcb",
                      "hardware/pcb/qssc.kicad_pcb"], (
        f"the public repository must contain exactly the two sandwich "
        f"boards, found: {boards}")


# --------------------------------------------------------------------------
# 2. pcbnew facts <-> doc strings
# --------------------------------------------------------------------------
# The text/git gates above must run everywhere (CI has no KiCad); only the
# geometry/socket checks need the bindings, so they skip individually.
try:
    import pcbnew
except ImportError:            # pragma: no cover - CI / venv without KiCad
    pcbnew = None


def _require_pcbnew():
    if pcbnew is None:
        pytest.skip("pcbnew not importable (system KiCad required)")


def _board(rel):
    return pcbnew.LoadBoard(str(REPO / rel))


def _edge_bbox_mm(board):
    xs, ys = [], []
    for d in board.GetDrawings():
        if d.GetLayer() == pcbnew.Edge_Cuts:
            bb = d.GetBoundingBox()
            xs += [bb.GetLeft(), bb.GetRight()]
            ys += [bb.GetTop(), bb.GetBottom()]
    return (round(pcbnew.ToMM(max(xs) - min(xs))),
            round(pcbnew.ToMM(max(ys) - min(ys))))


def test_board_geometry_matches_the_docs():
    """150x118 main and 110x102 head, as the docs state."""
    _require_pcbnew()
    assert _edge_bbox_mm(_board("hardware/pcb/qssc.kicad_pcb")) == (150, 118)
    assert _edge_bbox_mm(_board("hardware/pcb/chip_module.kicad_pcb")) == (110, 102)
    for doc, needles in (
        (HARDWARE_MD, ["150 × 118", "110 × 102"]),
        (DATASHEETS_MD, ["150×118", "110×102"]),
    ):
        text = doc.read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, f"{doc.name} does not mention {needle}"


def test_die_sockets_match_the_docs():
    """Uchip is the through-hole socket on BOTH boards, with no paste."""
    _require_pcbnew()
    for rel, name in (("hardware/pcb/qssc.kicad_pcb", "ChipSocket_2.54_EM"),
                      ("hardware/pcb/chip_module.kicad_pcb", "ChipSocket_2.54_DET")):
        board = _board(rel)
        uchip = next((f for f in board.GetFootprints()
                      if f.GetReference() == "Uchip"), None)
        assert uchip is not None, f"{rel}: no Uchip footprint"
        assert uchip.GetFPID().GetLibItemName() == name, (
            f"{rel}: Uchip is {uchip.GetFPID().GetLibItemName()}, docs say {name}")
        pads = list(uchip.Pads())
        paste = [p for p in pads
                 if p.GetLayerSet().Contains(pcbnew.F_Paste)
                 or p.GetLayerSet().Contains(pcbnew.B_Paste)]
        assert not paste, f"{rel}: the socket must carry no paste ({len(paste)} do)"
    hd = HARDWARE_MD.read_text(encoding="utf-8")
    ds = DATASHEETS_MD.read_text(encoding="utf-8")
    for doc, name in ((hd, "ChipSocket_2.54_EM"), (ds, "ChipSocket_2.54_EM"),
                      (hd, "ChipSocket_2.54_DET"), (ds, "ChipSocket_2.54_DET")):
        assert name in doc, f"a doc never mentions {name}"
    # and the replaced design must be gone from the living docs
    for doc in (hd, ds):
        assert "Ironwood SBT spring-pin socket** (" not in doc


def test_registration_caveat_is_the_measured_one():
    """The docs must state the measured non-mating geometry, not a fiction."""
    hd = HARDWARE_MD.read_text(encoding="utf-8")
    ds = DATASHEETS_MD.read_text(encoding="utf-8")
    assert "do not mate" in hd and "(+30, −41)" in hd
    assert "do not mate as-is" in ds
    # ground truth stays in placement.py
    placement = (REPO / "tools" / "kicad" / "placement.py").read_text(
        encoding="utf-8")
    assert "NOT mate as-is" in placement


# --------------------------------------------------------------------------
# 3. XDC balls <-> the hardware docs
# --------------------------------------------------------------------------
def test_xdc_balls_match_the_docs():
    xdc = XDC.read_text(encoding="utf-8")
    ds = DATASHEETS_MD.read_text(encoding="utf-8")
    for ball, doc_string in (("A8", "REFIN"), ("C1", "UART_RX"), ("B1", "UART_TX")):
        assert re.search(rf"\b{ball}\b", xdc), f"constraints.xdc lost ball {ball}"
    assert "REFIN from FPGA ball A8" in ds
    assert "FPGA rx (C1)" in ds
    assert "FPGA tx (B1)" in ds
    assert "ball A14" not in ds


# --------------------------------------------------------------------------
# 4. README counts agree with each other (no execution: pure cross-check)
# --------------------------------------------------------------------------
def test_readme_count_lines_agree():
    readme = README.read_text(encoding="utf-8")
    contributing = (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")
    badge = re.search(r"tests-(\d+)%20passed", readme)
    assert badge, "README badge lost"
    badge_n = badge.group(1)
    # The badge number must match the headline pass count in both files. The
    # exact skip count is NOT pinned here (it drifts as env guards are added
    # -- 42 -> 43 when the doc-reality gate itself landed); it is instead
    # verified against the CI collected total by tests/test_doc_reality.py's
    # pass+skip==CI gate, so this stays a pure badge<->headline cross-check.
    assert re.search(rf"{badge_n} passed, \d+ skipped", readme), (
        f"README badge ({badge_n} passed) not matched by its headline line")
    assert re.search(rf"{badge_n} passed, \d+ skipped", contributing), (
        f"CONTRIBUTING baseline not matched to badge ({badge_n} passed)")
    ci = re.search(r"(\d+) tests, no KiCad required", readme)
    total = re.search(r"python -m pytest\s+# (\d+) collected", readme)
    assert ci and total, "README CI/collected lines lost"
    # the Testing table's "Python suite" row is the CI (non-PCB) count
    row = re.search(r"Python suite — (\d+) collected", readme)
    assert row and row.group(1) == ci.group(1)
    # the CI job runs the non-PCB set: collected total > CI >= badge
    assert int(total.group(1)) > int(ci.group(1)) >= int(badge_n)


# --------------------------------------------------------------------------
# 5. README synthesis figures are SYNTH_REPORT.md's figures
# --------------------------------------------------------------------------
def test_readme_synthesis_numbers_match_the_report():
    readme = README.read_text(encoding="utf-8")
    report = SYNTH.read_text(encoding="utf-8")
    lut = re.search(r"\| LUT \(6-input\) \| (\d+) \|", report)
    carry = re.search(r"\| CARRY4 \| (\d+) \|", report)
    assert lut and carry, "SYNTH_REPORT table changed shape"
    assert lut.group(1) in readme and carry.group(1) in readme, (
        f"README quotes other synthesis numbers than the report "
        f"(LUT {lut.group(1)}, CARRY4 {carry.group(1)})")
    assert "Fits XC7A35T:** ✅ yes" in report


# --------------------------------------------------------------------------
# 6. every README firmware verb exists in the RTL parser
# --------------------------------------------------------------------------
def test_readme_firmware_verbs_exist_in_the_parser():
    readme = README.read_text(encoding="utf-8")
    parser = PARSER.read_text(encoding="utf-8")
    # the README firmware-status paragraph enumerates all 19 backticked verbs
    m = re.search(r"\*\*Firmware status\.\*\* All 19 verbs are implemented "
                  r"in the RTL: (.+?`SET_GRADIENT`)\.", readme, re.S)
    assert m, "README firmware-status paragraph lost or reworded"
    verbs = re.findall(r"`([A-Z_]+)`", m.group(1))
    assert len(verbs) == 19, verbs
    for verb in verbs:
        assert f'"{verb}"' in parser or verb in parser, (
            f"README claims firmware verb {verb} but command_parser.v "
            f"never mentions it")


# --------------------------------------------------------------------------
# Every env knob the code reads must be documented somewhere the user looks
# (README, docs/, hardware/, or the deploy manifests). Guards finding B of
# the round-10 audit: eleven runtime/security knobs shipped undocumented.
# --------------------------------------------------------------------------
def test_every_env_knob_is_documented():
    import re as _re

    code_roots = [REPO / "api", REPO / "qssc", REPO / "tools", REPO / "fpga"]
    knobs = set()
    for root in code_roots:
        for f in root.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            knobs.update(_re.findall(
                r"QSSC_[A-Z0-9_]+", f.read_text(encoding="utf-8", errors="ignore")))

    doc_files = [REPO / "README.md", REPO / "Dockerfile", REPO / "render.yaml"]
    for d in ("docs", "hardware", ".github"):
        p = REPO / d
        if p.exists():
            doc_files.extend(p.rglob("*"))
    documented = set()
    for f in doc_files:
        if f.is_file() and f.suffix in (".md", ".yaml", ".yml", ".txt", ""):
            try:
                documented.update(_re.findall(
                    r"QSSC_[A-Z0-9_]+", f.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                continue

    undocumented = sorted(k for k in knobs if k not in documented)
    assert not undocumented, (
        f"env knobs read in code but never documented in README/docs/deploy: "
        f"{undocumented} — add them to the README 'Environment variables' "
        f"table (or the router doc for dev-only knobs)")
