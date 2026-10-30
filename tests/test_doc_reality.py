"""Doc numeric claims must match a REAL measurement (round-15 structural fix).

Root-cause fix for the recurring defect family found in rounds 10, 14 and 15:
numeric claims in README / CONTRIBUTING / INVENTIONS.md / HARDWARE_DESIGN.md
drifted from what the commands actually produce, because the existing doc
gates only cross-check the docs against THEMSELVES (``test_readme_count_lines_
agree`` is explicitly "no execution: pure cross-check") -- never against a
measurement.  Every gate here MEASURES reality and asserts the doc agrees, so
the family cannot silently reopen.

Environment facts these gates are calibrated against (measured 2026-10-30):
  * the non-PCB collected count is **1139** and is identical in the default
    venv and the pcbnew env (non-PCB tests never depend on pcbnew) -- this is
    what CI runs, so it is the one number that is stable everywhere;
  * ``tests/test_pcb_*.py`` collects **53** with pcbnew and **38** without
    (15 tests are pcbnew-definition-gated) -- the README's "system KiCad"
    rows therefore use 53 / full 1192 = 1139 + 53;
  * the badge headline (``1097 passed, 42 skipped``) is the CI / no-KiCad run
    and sums to the CI collected count.

Env-dependent gates SKIP when their tool is missing (qiskit / pcbnew /
kicad-cli) instead of failing, so the file is green in every suite entry
point while still verifying what each environment can actually measure.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
INVENTIONS = REPO / "docs" / "INVENTIONS.md"
HARDWARE_MD = REPO / "hardware" / "HARDWARE_DESIGN.md"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _collect_count(pattern_args: list[str], env: dict | None = None) -> int | None:
    """Number of tests a fresh ``pytest --collect-only`` finds.

    Raises when no "N tests collected" line appeared (the set could not be
    collected at all), so a real breakage is legible.  ``env`` lets callers
    pass ``_pcb_env.child_env()`` so a subprocess sees the same ``pcbnew``
    binding ``ensure_pcbnew()`` made importable in-process (a bare child does
    NOT inherit that ``sys.path`` edit).
    """
    base = env if env is not None else os.environ
    res = subprocess.run(
        [sys.executable, "-m", "pytest", *pattern_args,
         "--collect-only", "-q", "-o", "addopts=", "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, timeout=600,
        env={**base, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    m = re.search(r"(\d+) tests collected", res.stdout)
    if m:
        return int(m.group(1))
    # Surface why nothing was collected so a real breakage is legible.
    tail = (res.stdout + res.stderr)[-800:]
    raise AssertionError(
        f"pytest --collect-only produced no count (exit {res.returncode}):\n{tail}")


def _has_module(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _norm_blockquotes(path: Path) -> str:
    """Markdown with blockquote markers and runs of whitespace flattened, so
    claims that wrap across ``>`` lines can be matched with plain regex."""
    txt = path.read_text(encoding="utf-8")
    txt = re.sub(r"^\s*>\s?", "", txt, flags=re.M)
    return re.sub(r"\s+", " ", txt)


# --------------------------------------------------------------------------
# 1. README: the CI / non-PCB collected count is what a live collect finds
# --------------------------------------------------------------------------
def test_readme_ci_count_matches_live_collect():
    """The README's non-PCB (CI) count must equal a fresh collect.

    This is the load-bearing anti-recurrence gate: adding or deleting a test
    changes the live count, so a stale README number fails here and forces the
    badge/headline/CI rows to be refreshed together.
    """
    if not _has_module("qiskit"):
        pytest.skip("qiskit not importable (non-PCB suite needs it)")
    readme = _readme()
    ci = re.search(r"(\d+) tests, no KiCad required", readme)
    row = re.search(r"Python suite — (\d+) collected", readme)
    assert ci and row, "README lost its CI / 'Python suite' count lines"
    live = _collect_count(["--ignore-glob=tests/test_pcb_*"])
    assert int(ci.group(1)) == live, (
        f"README claims {ci.group(1)} CI tests but a live collect finds {live}")
    assert int(row.group(1)) == live, (
        f"README 'Python suite' claims {row.group(1)}, live collect finds {live}")


def test_readme_pass_and_skip_sum_to_ci():
    """Headline passed + skipped must sum to the CI collected count, and the
    badge must show the same pass number (keeps the three in lockstep)."""
    readme = _readme()
    badge = re.search(r"tests-(\d+)%20passed", readme)
    ps = re.search(r"(\d+) passed, (\d+) skipped", readme)
    ci = re.search(r"(\d+) tests, no KiCad required", readme)
    assert badge and ps and ci, "README lost badge / pass-skip / CI lines"
    passed, skipped = int(ps.group(1)), int(ps.group(2))
    assert passed == int(badge.group(1)), (
        f"badge says {badge.group(1)} passed but the headline says {passed}")
    assert passed + skipped == int(ci.group(1)), (
        f"{passed} passed + {skipped} skipped = {passed + skipped} != "
        f"CI collected {ci.group(1)}")


def test_readme_full_equals_ci_plus_pcb():
    """The README's full collected count must be CI + PCB, and the two 'full'
    statements (the table row and the Testing block) must agree."""
    readme = _readme()
    row = re.search(r"\*\*(\d+) collected\*\*, of which (\d+) run in CI", readme)
    block = re.search(r"python -m pytest\s+# (\d+) collected", readme)
    pcb = re.search(r"— (\d+) tests in 13 files", readme)
    ci = re.search(r"(\d+) tests, no KiCad required", readme)
    assert row and block and pcb and ci, (
        "README lost its full/CI/PCB collected lines")
    full_row, in_ci = int(row.group(1)), int(row.group(2))
    assert in_ci == int(ci.group(1)), (
        f"table says {in_ci} run in CI but the CI row says {ci.group(1)}")
    assert full_row == int(block.group(1)), (
        f"table full={full_row} but the Testing block says {block.group(1)}")
    assert full_row == in_ci + int(pcb.group(1)), (
        f"full {full_row} != CI {in_ci} + PCB {pcb.group(1)}")


def test_readme_pcb_count_matches_live_collect():
    """The 'PCB … N tests in 13 files' count must equal a live collect of the
    test_pcb_* set (only verifiable where the pcbnew binding exists).

    Uses ``ensure_pcbnew()`` + ``child_env()`` from ``tests/_pcb_env`` so the
    subprocess sees the same binding ``ensure_pcbnew`` made importable in-
    process (order-independent: ``ensure_pcbnew`` globs for it itself). Skips
    only when the system genuinely has no KiCad (CI).
    """
    from _pcb_env import child_env, ensure_pcbnew

    try:
        ensure_pcbnew()
    except ImportError:
        pytest.skip("pcbnew not importable (system KiCad required)")
    readme = _readme()
    pcb = re.search(r"— (\d+) tests in 13 files", readme)
    assert pcb, "README lost its PCB test-count row"
    # Positional globs are shell-expanded in a terminal; expand here so the
    # shell-less subprocess gets real paths (pytest does not glob arguments).
    files = sorted(f"tests/{p.name}" for p in (REPO / "tests").glob("test_pcb_*.py"))
    assert files, "no tests/test_pcb_*.py files found"
    live = _collect_count(files, env=child_env())
    assert int(pcb.group(1)) == live, (
        f"README claims {pcb.group(1)} PCB tests but a live collect finds {live}")


# --------------------------------------------------------------------------
# 2. INVENTIONS.md: each "(N tests)" claim must match that file
# --------------------------------------------------------------------------
def test_inventions_test_counts_match_files():
    txt = INVENTIONS.read_text(encoding="utf-8")
    claims = re.findall(r"tests/(test_[a-z0-9_]+\.py)`?\s*\((\d+) tests", txt)
    assert claims, "INVENTIONS.md lost its per-file test-count claims"
    problems = []
    for fname, claimed in claims:
        path = REPO / "tests" / fname
        assert path.exists(), f"INVENTIONS.md references missing tests/{fname}"
        actual = len(re.findall(r"^\s*def test_", path.read_text(encoding="utf-8"),
                                flags=re.M))
        if int(claimed) != actual:
            problems.append(f"{fname}: doc={claimed} actual={actual}")
    assert not problems, (
        f"INVENTIONS.md stale per-file test counts: {problems}")


# --------------------------------------------------------------------------
# 3. HARDWARE_DESIGN.md: board footprint/active/via counts vs the boards
# --------------------------------------------------------------------------
def _board_counts(pcb: Path) -> tuple[int, int, int]:
    """(footprints, active U/J/REG/Y/Q/F/SW refs, GND vias) of a board file."""
    txt = pcb.read_text(encoding="utf-8")
    refs = [m.group(1) for m in
            re.finditer(r'\(property "Reference" "([^"]+)"', txt)]
    refs = [r for r in refs if not r.startswith("#")]
    active = [r for r in refs if re.match(r"^(U|J|REG|Y|Q|F|SW)", r)]
    netmap = dict(re.findall(r'\(net (\d+) "([^"]*)"\)', txt))
    gnd_vias = 0
    for m in re.finditer(r"\(via\b[\s\S]{0,300}?\)\s*\)", txt):
        nm = re.search(r"\(net (\d+)", m.group(0))
        if nm and netmap.get(nm.group(1)) == "GND":
            gnd_vias += 1
    return len(refs), len(active), gnd_vias


def test_hardware_design_board_counts_match_boards():
    doc = _norm_blockquotes(HARDWARE_MD)
    fp_claims = [(int(a), int(b)) for a, b in
                 re.findall(r"(\d+) footprints, (\d+) active", doc)]
    via_claim = re.search(r"(\d+) GND stitching vias", doc)
    assert len(fp_claims) == 2, (
        f"HARDWARE_DESIGN.md should state both boards' footprint counts, found {fp_claims}")
    assert via_claim, "HARDWARE_DESIGN.md lost its GND-stitching-via claim"

    main = _board_counts(REPO / "hardware/pcb/qssc.kicad_pcb")
    head = _board_counts(REPO / "hardware/pcb/chip_module.kicad_pcb")
    # doc lists main first, then head
    assert fp_claims[0] == (main[0], main[1]), (
        f"doc says main={fp_claims[0]} but the board is footprints={main[0]}, "
        f"active={main[1]}")
    assert fp_claims[1] == (head[0], head[1]), (
        f"doc says head={fp_claims[1]} but the board is footprints={head[0]}, "
        f"active={head[1]}")
    assert int(via_claim.group(1)) == main[2], (
        f"doc claims {via_claim.group(1)} GND stitching vias, board has {main[2]}")


# --------------------------------------------------------------------------
# 4. HARDWARE_DESIGN.md: the ERC warning counts vs a live kicad-cli run
# --------------------------------------------------------------------------
def _erc_counts(sch: Path) -> dict[str, int]:
    import json
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as fh:
        out = fh.name
    try:
        subprocess.run(
            ["kicad-cli", "sch", "erc", "--severity-all", "--format", "json",
             "-o", out, str(sch)],
            cwd=REPO, capture_output=True, text=True, timeout=300, check=False)
        data = json.loads(Path(out).read_text(encoding="utf-8"))
    finally:
        Path(out).unlink(missing_ok=True)
    counts: dict[str, int] = {}
    for sheet in data.get("sheets", []):
        for v in sheet.get("violations", []):
            t = v.get("type", "?")
            counts[t] = counts.get(t, 0) + 1
            counts["__total__"] = counts.get("__total__", 0) + 1
    return counts


@pytest.mark.skipif(
    not any(os.access(os.path.join(p, "kicad-cli"), os.X_OK)
            for p in os.environ.get("PATH", "").split(os.pathsep)),
    reason="kicad-cli not on PATH (system KiCad required)",
)
def test_hardware_design_erc_counts_match_reality():
    doc = _norm_blockquotes(HARDWARE_MD)
    m = re.search(r"(\d+) warnings: (\d+)\s+`?endpoint_off_grid`?", doc)
    assert m, "HARDWARE_DESIGN.md lost its ERC warning-count claim"
    doc_total, doc_offgrid = int(m.group(1)), int(m.group(2))

    main = _erc_counts(REPO / "hardware/pcb/qssc.kicad_sch")
    head = _erc_counts(REPO / "hardware/pcb/chip_module.kicad_sch")
    total = main.get("__total__", 0) + head.get("__total__", 0)
    offgrid = main.get("endpoint_off_grid", 0) + head.get("endpoint_off_grid", 0)
    assert total == doc_total, (
        f"doc claims {doc_total} ERC warnings, kicad-cli reports {total}")
    assert offgrid == doc_offgrid, (
        f"doc claims {doc_offgrid} endpoint_off_grid, kicad-cli reports {offgrid}")
