"""Paranoia sweep: dumb, beginner-grade, project-killing mistakes.

Every high-tech audit can miss the boring failure modes. This module hunts
them deliberately, on plain parsed artifacts (no kicad-cli, no pcbnew needed):

  * tracked-file hygiene — wrong encodings, CRLF drift, UTF-8 BOM, foreign
    absolute developer paths, credential patterns, committed junk files,
    tracked symlinks, .tmpwork contents;
  * KiCad artifact integrity — unbalanced parentheses, duplicated uuids,
    empty/duplicated refdes, schematic-refs vs board-refs drift, unresolved
    qssc: lib_ids, net-name sanity, case-collisions, critical nets missing,
    grad nets leaking onto the main/head boards, pads with no net;
  * the USB-C no_connect repair — markers on the SuperSpeed positions,
    the SBU positions (A8/B8) deliberately NOT marked;
  * BOM.csv structure — arity, sections, duplicate rows, grad refs placed
    on the grad board, C87 frozen as the one known uncovered main ref;
  * historical preservation — the repair touched nothing except 8 markers
    per board and 1 BOM row;
  * simulator constants — ordering and self-consistency invariants;
  * test-suite meta — no no-op asserts, no reference to throwaway worktrees,
    every test file actually contains tests.
"""

import csv
import hashlib
import re
import subprocess
from functools import cache
from pathlib import Path

import pytest

import qssc.simulator_constants as sc

REPO = Path(__file__).resolve().parent.parent
PCB_DIR = REPO / "hardware" / "pcb"
BOM = REPO / "hardware" / "datasheets" / "BOM.csv"

def _need_grad():
    """The gradient board is local-only: skip its gates when unpublished."""
    if "grad" not in BOARDS:
        pytest.skip("qssc-grad board is local-only (not published here)")


BOARDS = {
    "main": ("hardware/pcb/qssc.kicad_sch", "hardware/pcb/qssc.kicad_pcb"),
    "head": (
        "hardware/pcb/chip_module.kicad_sch",
        "hardware/pcb/chip_module.kicad_pcb",
    ),
}
if (REPO / "hardware/pcb/qssc-grad.kicad_pcb").exists() and \
   (REPO / "hardware/pcb/qssc-grad.kicad_sch").exists():
    BOARDS["grad"] = ("hardware/pcb/qssc-grad.kicad_sch",
                      "hardware/pcb/qssc-grad.kicad_pcb")

BASELINE = "811d8c4"

# Text files that are CRLF by convention (hand-maintained CSV written by
# gen_bom.py on this line-ending convention; both baseline and HEAD are
# fully CRLF, so the file's own convention is "consistency", not "LF").
CRLF_ALLOW = {"hardware/datasheets/BOM.csv"}

# Dead maintenance scripts that carry a foreign absolute developer path and
# are only referenced by docs/propiedad-intelectual/sloc_count.py. Allowed
# as a documented, filed hygiene finding — see the repo issue tracker.
ABS_PATH_ALLOW = {
    "tools/_audit_pins.py",
    "tools/_chip_map.py",
}

BIN_SUFFIX = {".pdf", ".png", ".jpg", ".jpeg", ".zip", ".wav", ".gz", ".7z"}

SECRET_PATTERNS = [
    re.compile(rb"ghp_[0-9A-Za-z]{36}"),
    # literal split: the committed sweep file must not self-match its own
    # credential scanners (the pattern below would otherwise match the
    # source text of this very file, since the file is a tracked scan
    # target itself)
    re.compile(rb"github_" + b"pat_"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"xox[bp]-"),
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]

ABS_PATH_PATTERNS = [
    re.compile(rb"/home/[A-Za-z0-9_.-]+"),
    re.compile(rb"/run/media/[A-Za-z0-9_.-]+"),
]

JUNK_PATTERNS = [
    re.compile(r"__pycache__/"),
    re.compile(r"\.pyc$"),
    re.compile(r"\.bak$"),
    re.compile(r"\.orig$"),
    re.compile(r"DS_Store$"),
    re.compile(r"~$"),
]

CRITICAL_NETS = {
    "main": {
        "GND",
        "+5V",
        "+3V3",
        "+1V0",
        "+1V8",
        "+12V_EMIT",
        "+12V_EMIT_IN",
        "+5V_IN",
        "+5V_RF",
        "refclk_25m",
        "mod_sync",
        "clk_100mhz",
        "UART_TX",
        "UART_RX",
        "USB_DP",
        "USB_DM",
        "VBUS_USB",
        "J3_SIG",
        "RF_IN",
        "RF_OUT",
        "em_sclk",
        "em_sin",
        "em_xlat",
        "em_blank",
    },
    "head": {"GND", "+5V", "+3V3", "+5V_A"},
}

GRAD_NETS = {
    "grad_en",
    "GRAD_COIL_N",
    "GRAD_COIL_P",
    "GRAD_DAC_OUT",
    "GRAD_SENSE",
    "GRAD_NFB",
    "GRAD_GATE",
    "grad_cs",
    "grad_mosi",
    "grad_sclk",
}

# The 8 USB-C SuperSpeed positions the repair marks; A8/B8 (SBU) must stay
# out of this set (they carry real GND connectivity).
SPEED_PINS = {"A2", "A3", "A10", "A11", "B2", "B3", "B10", "B11"}
SBU_PINS = {"A8", "B8"}


# ---------------------------------------------------------------- helpers


@cache
def _tracked_files():
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, check=True, capture_output=True, text=True
    )
    return tuple(out.stdout.splitlines())


@cache
def _tracked_symlinks():
    out = subprocess.run(
        ["git", "ls-files", "-s"], cwd=REPO, check=True, capture_output=True, text=True
    )
    return tuple(
        line.split()[3]
        for line in out.stdout.splitlines()
        if line.split()[1] == "120000"
    )


def _raw(rel):
    return (REPO / rel).read_bytes()


def _text(rel):
    return _raw(rel).decode("utf-8")


def _is_binary(data):
    return b"\x00" in data


@cache
def _baseline_blob(path):
    """Bytes of `path` at BASELINE, or None when the commit is unavailable."""
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", BASELINE + "^{commit}"],
        cwd=REPO,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        return None
    out = subprocess.run(
        ["git", "cat-file", "blob", f"{BASELINE}:{path}"],
        cwd=REPO,
        capture_output=True,
        check=False,
    )
    return out.stdout if out.returncode == 0 else None


def _strip_lib_symbols(text):
    out = re.sub(r"\(lib_symbols.*?\n\t\)\n", "", text, flags=re.DOTALL)
    return out


def _refs(text):
    """Reference designators from template-stripped KiCad text, in order."""
    return re.findall(r'\(property "Reference" "([^"]*)"', text)


def _instances(text):
    """Per-instance symbol records from a schematic (or footprint records
    from a board — both use (property "Reference" ...)."""
    insts = []
    for chunk in re.split(r"\n\t\(symbol\n", text)[1:]:
        ref = re.search(r'\(property "Reference" "([^"]*)"', chunk)
        lib = re.search(r'\(lib_id "([^"]+)"', chunk)
        at = re.search(r'\(lib_id "[^"]+"\s*\n\s*\(at ([-\d.]+) ([-\d.]+)', chunk)
        uuid = re.search(r'\(uuid "([^"]+)"', chunk)
        value = re.search(r'\(property "Value" "([^"]*)"', chunk)
        fp = re.search(r'\(property "Footprint" "([^"]*)"', chunk)
        insts.append(
            {
                "ref": ref.group(1) if ref else "",
                "lib_id": lib.group(1) if lib else "",
                "x": float(at.group(1)) if at else None,
                "y": float(at.group(2)) if at else None,
                "uuid": uuid.group(1) if uuid else "",
                "value": value.group(1) if value else "",
                "footprint": fp.group(1) if fp else "",
            }
        )
    return insts


@cache
def _sheet_instances(name):
    return _instances(_strip_lib_symbols(_text(BOARDS[name][0])))


@cache
def _board_footprints(name):
    """(ref, n_pads, n_named_pads) per footprint chunk of a board file."""
    text = _text(BOARDS[name][1])
    chunks = re.split(r"\(footprint ", text)[1:]
    fps = []
    for chunk in chunks:
        ref = re.search(r'\(property "Reference" "([^"]*)"', chunk)
        pads = re.split(r"\(pad\b", chunk)[1:]
        named = 0
        for pad in pads:
            m = re.search(r'\(net (\d+) "([^"]*)"\)', pad)
            if m and m.group(2).strip():
                named += 1
        fps.append((ref.group(1) if ref else "", len(pads), named))
    return tuple(fps)


@cache
def _board_nets(name):
    """Defined nets (definitions live in the file header, before footprints)."""
    text = _text(BOARDS[name][1])
    header = text.split("(footprint ", 1)[0]
    return re.findall(r'\(net (\d+) "([^"]*)"\)', header)


def _marker_coords(text):
    coords = set()
    for chunk in re.split(r"\(no_connect\b", text)[1:]:
        m = re.search(r"\(at ([-\d.]+) ([-\d.]+)", chunk)
        if m:
            coords.add((float(m.group(1)), float(m.group(2))))
    return coords


def _usb_unit_pins(text):
    """USB_C_Receptacle pin number -> symbol-local (x, y), from the unit
    template inside lib_symbols (instance pin entries carry no position)."""
    i = text.find('(symbol "USB_C_Receptacle_0_1"', text.find("(lib_symbols"))
    depth, j = 0, i
    while j < len(text) and j - i < 300000:
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    pins = {}
    for chunk in re.split(r"\(pin ", text[i:j])[1:]:
        at = re.search(r"\(at ([-\d.]+) ([-\d.]+)", chunk)
        nb = re.search(r'\(number "([^"]+)"', chunk)
        if at and nb:
            pins[nb.group(1)] = (float(at.group(1)), float(at.group(2)))
    return pins


def _j10_position(text):
    m = re.search(
        r'\(lib_id "qssc:USB_C_Receptacle"[\s\S]{0,80}?\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)',
        text,
    )
    return float(m.group(1)), float(m.group(2))


def _baseline_marker_coords(name):
    raw = _baseline_blob(BOARDS[name][0])
    if raw is None:
        return None
    return _marker_coords(raw.decode("utf-8"))


@cache
def _baseline_instances(name):
    raw = _baseline_blob(BOARDS[name][0])
    if raw is None:
        return None
    return _instances(_strip_lib_symbols(raw.decode("utf-8")))


# ------------------------------------------------- tracked-file hygiene


@pytest.mark.parametrize("rel", sorted(_tracked_files()))
def test_tracked_file_hygiene(rel):
    """UTF-8 decodable, no CRLF drift (outside the CSV), no BOM, no
    credential patterns, no foreign absolute paths in text artifacts."""
    data = _raw(rel)
    problems = []
    if data.startswith(b"\xef\xbb\xbf"):
        problems.append("UTF-8 BOM")
    binary = _is_binary(data)
    if not binary and not _text(rel):
        problems.append("empty tracked file")
    if not binary and rel not in CRLF_ALLOW and b"\r\n" in data:
        problems.append("CRLF line endings")
    if not binary and not rel.endswith(tuple(BIN_SUFFIX)):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            problems.append("not valid UTF-8")
    tail = data[-8192:]
    head = data[:8192]
    for pat in SECRET_PATTERNS:
        if pat.search(head) or pat.search(tail):
            problems.append(f"credential-looking pattern {pat.pattern!r}")
    if not binary and rel not in ABS_PATH_ALLOW:
        for pat in ABS_PATH_PATTERNS:
            if pat.search(data):
                problems.append(f"foreign absolute path {pat.pattern!r}")
    assert not problems, f"{rel}: {', '.join(problems)}"


def test_no_junk_tracked():
    junk = [rel for rel in _tracked_files() for pat in JUNK_PATTERNS if pat.search(rel)]
    assert not junk, f"junk tracked in git: {junk}"
    tmpwork = [rel for rel in _tracked_files() if rel.startswith(".tmpwork/")]
    # Two pinned snapshots, both read at import time by the board generators:
    # pins.json (standard-KiCad symbol pinout) and fpga_csg324_map.json (the
    # FPGA ballmap). Committing either is what makes a fresh clone runnable
    # (issue #166); nothing else may accumulate in there.
    assert tmpwork == [".tmpwork/fpga_csg324_map.json", ".tmpwork/pins.json"], (
        f".tmpwork must contain only the pinned snapshots: {tmpwork}"
    )


def test_no_tracked_symlinks():
    links = list(_tracked_symlinks())
    assert not links, f"tracked symlinks break plain clones: {links}"


# ------------------------------------------------- artifact integrity


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_parens_balanced(name):
    sch = _text(BOARDS[name][0])
    pcb = _text(BOARDS[name][1])
    for label, txt in (("schematic", sch), ("board", pcb)):
        n_open, n_close = txt.count("("), txt.count(")")
        assert n_open == n_close, (
            f"{name} {label}: unbalanced parentheses "
            f"({n_open} '(' vs {n_close} ')') — KiCad will refuse to load"
        )
        assert n_open > 100, f"{name} {label}: suspiciously small artifact"


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_uuids_unique(name):
    sch = _text(BOARDS[name][0])
    pcb = _text(BOARDS[name][1])
    for label, txt in (("schematic", sch), ("board", pcb)):
        uuids = re.findall(r'\(uuid "([^"]+)"', txt)
        dups = {u for u in uuids if uuids.count(u) > 1}
        assert not dups, f"{name} {label}: duplicated uuids {sorted(dups)[:4]}"


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_sheet_refs_nonempty_and_unique(name):
    refs = [i["ref"] for i in _sheet_instances(name)]
    empty = [r for r in refs if not r.strip()]
    assert not empty, f"{name} sheet: empty Reference fields"
    dups = {r for r in refs if refs.count(r) > 1}
    assert not dups, f"{name} sheet: duplicated refdes {sorted(dups)[:6]}"


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_board_refs_nonempty_and_unique(name):
    refs = [f[0] for f in _board_footprints(name)]
    no_ref = [i for i, r in enumerate(refs) if not r.strip()]
    assert not no_ref, f"{name} board: {len(no_ref)} footprints without Reference"
    dups = {r for r in refs if refs.count(r) > 1}
    assert not dups, f"{name} board: duplicated refdes {sorted(dups)[:6]}"


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_sheet_refs_match_board_refs(name):
    """The classic hand-edit mistake: a part annotated on the schematic but
    never placed on the board (or vice versa). Power flags stay on sheets."""
    sheet = _refs(_strip_lib_symbols(_text(BOARDS[name][0])))
    board = [f[0] for f in _board_footprints(name)]
    sheet_nonflag = sorted(r for r in sheet if not r.startswith("#"))
    board_sorted = sorted(board)
    missing_on_board = sorted(set(sheet_nonflag) - set(board_sorted))
    extra_on_board = sorted(set(board_sorted) - set(sheet_nonflag))
    assert not missing_on_board, f"{name}: on sheet, not on board: {missing_on_board}"
    assert not extra_on_board, f"{name}: on board, not on sheet: {extra_on_board}"
    assert len(sheet_nonflag) == len(board_sorted)


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_qssc_lib_ids_resolve(name):
    text = _strip_lib_symbols(_text(BOARDS[name][0]))
    lib = _text("hardware/pcb/qssc-lib.kicad_sym")
    defined = {
        n.split(":", 1)[1] if ":" in n else n
        for n in re.findall(r'\(symbol "([^"]+)"', lib)
    }
    used = set(re.findall(r'\(lib_id "([^"]+)"', text))
    qssc_used = {u.split(":", 1)[1] for u in used if u.startswith("qssc:")}
    unresolved = sorted(qssc_used - defined)
    assert not unresolved, f"{name}: qssc: lib_ids with no symbol: {unresolved}"
    for u in used:
        assert u.split(":", 1)[0] in ("qssc", "power"), (
            f"{name}: unexpected library prefix on {u}"
        )


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_net_names_sane(name):
    nets = _board_nets(name)
    names = [n for _, n in nets]
    bad = [n for n in names if n and (n != n.strip() or " " in n)]
    assert not bad, f"{name}: whitespace-polluted net names {bad[:5]}"
    lowered = [n.lower() for n in names if n]
    dups = {n for n in lowered if lowered.count(n) > 1}
    assert not dups, f"{name}: case-insensitive net collision {sorted(dups)}"
    body = [i for i, n in nets if i != "0" and not n.strip()]
    assert not body, f"{name}: numbered nets with empty names {body[:5]}"


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_critical_nets_present(name):
    have = {n for _, n in _board_nets(name) if n}
    critical = CRITICAL_NETS.get(name, set())
    if name == "grad":
        critical = CRITICAL_NETS["main"] | GRAD_NETS
    missing = sorted(critical - have)
    assert not missing, f"{name}: critical nets missing: {missing}"


@pytest.mark.parametrize("name", ["head", "main"])
def test_no_grad_nets_off_grad_board(name):
    """The gradient block lives ON the published main board (the main IS the
    extended build -- there is no third board), so main must carry every
    design gradient net while the head stays grad-free.  grad_en is the spare
    STATUS ball: NC by design (no net, netless pad), hence excluded from the
    required set."""
    have = {n for _, n in _board_nets(name) if n}
    grad_found = {n for n in have if "grad" in n.lower()}
    if name == "head":
        assert not grad_found, (
            f"head must not carry gradient-board nets: {sorted(grad_found)}"
        )
        return
    required = GRAD_NETS - {"grad_en"}
    missing = sorted(required - have)
    assert not missing, f"main must carry the gradient nets: {missing}"
    unexpected = sorted(grad_found - required)
    assert not unexpected, f"main carries unexpected grad nets: {unexpected}"


@pytest.mark.parametrize("name", sorted(BOARDS))
def test_footprints_have_netted_pads(name):
    fps = _board_footprints(name)
    zero = [(r, n) for r, _, n in fps if n == 0 and r.strip()]
    assert len(zero) <= 2, f"{name}: footprints with nothing on a net: {zero}"
    net0 = 0
    for pad in re.split(r"\(pad\b", _text(BOARDS[name][1]))[1:]:
        m = re.search(r'\(net (\d+) "([^"]*)"\)', pad)
        if m and int(m.group(1)) == 0:
            net0 += 1
    assert net0 <= 4, f"{name}: {net0} pads without a net (net 0)"


# ------------------------------------------------- USB-C repair guards


@pytest.mark.skipif(
    _baseline_blob(BOARDS["main"][0]) is None,
    reason=f"baseline {BASELINE} not present in this clone",
)
@pytest.mark.parametrize("name", ["main", "grad"])
def test_nc_markers_on_superspeed_pins(name):
    """Exactly the 8 SuperSpeed positions were NC-marked by the repair —
    no more, no fewer, none elsewhere."""
    text = _strip_lib_symbols(_text(BOARDS[name][0]))
    # pins come from the lib_symbols template, which stripping removes:
    # read the unstripped artifact for the pin geometry only
    unit_pins = _usb_unit_pins(_text(BOARDS[name][0]))
    for pin in SPEED_PINS | SBU_PINS:
        assert pin in unit_pins, f"{name}: J10 pin {pin} missing from unit template"
    added = _marker_coords(text) - _baseline_marker_coords(name)
    ix, iy = _j10_position(text)
    derived = {(ix + x, iy - y) for (x, y) in (unit_pins[p] for p in SPEED_PINS)}
    assert added == derived, (
        f"{name}: NC markers vs SuperSpeed positions differ; "
        f"unmarked={sorted(derived - added)}, stray={sorted(added - derived)}"
    )


@pytest.mark.parametrize("name", ["main", "grad"])
def test_sbu_pins_not_nc_marked(name):
    """A8/B8 carry real GND connectivity — marking them NC is exactly the
    kind of 'tidy' beginner touch-up that kills the design."""
    text = _strip_lib_symbols(_text(BOARDS[name][0]))
    marked = _marker_coords(text)
    ix, iy = _j10_position(text)
    pins = _usb_unit_pins(_text(BOARDS[name][0]))
    sbu_coords = {(ix + x, iy - y) for (x, y) in (pins[p] for p in SBU_PINS)}
    assert not (marked & sbu_coords), (
        f"{name}: SBU pins A8/B8 were NC-marked — they are GND-connected"
    )


def test_reserved_labels_scope():
    _need_grad()
    main_s = _strip_lib_symbols(_text(BOARDS["main"][0]))
    grad_s = _strip_lib_symbols(_text(BOARDS["grad"][0]))
    head_s = _strip_lib_symbols(_text(BOARDS["head"][0]))
    for sheet, label in ((main_s, "main"), (grad_s, "grad")):
        assert '(label "mod_sync"' in sheet, f"{label}: mod_sync label lost"
    assert '(label "grad_en"' in grad_s, "grad: grad_en label lost"
    assert '(label "grad_en"' not in main_s, "main: grad_en label leaked in"
    assert '(label "grad_en"' not in head_s, "head: grad_en label leaked in"


# ------------------------------------------------- board geometry


def test_head_socket_on_backside():
    """chip_module is the head of the sandwich: the DET chip socket faces
    the main board, so it must be the one and only B.Cu footprint there."""
    text = _text(BOARDS["head"][1])
    chunks = re.split(r"\(footprint ", text)[1:]
    back, socket_layer = [], None
    for chunk in chunks:
        lyr = re.search(r'\(layer "([^"]+)"', chunk)
        ref = re.search(r'\(property "Reference" "([^"]*)"', chunk)
        fp = re.match(r'"([^"]+)"', chunk)
        if not lyr or not ref:
            continue
        if fp and "ChipSocket_2.54_DET" in fp.group(1):
            socket_layer = lyr.group(1)
        if lyr.group(1) == "B.Cu":
            back.append(ref.group(1))
    assert len(back) == 1, f"head: expected exactly 1 B.Cu footprint, got refs {back}"
    assert socket_layer == "B.Cu", (
        f"head: DET chip socket sits on {socket_layer!r}, expected B.Cu"
    )


# ------------------------------------------------- BOM structure


@cache
def _bom_rows():
    with BOM.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    return rows[0], rows[1:]


def test_bom_arity_and_sections():
    header, rows = _bom_rows()
    assert header[:3] == ["Board", "Designators", "Qty"], f"header: {header}"
    bad_arity = [i + 2 for i, r in enumerate(rows) if len(r) != len(header)]
    assert not bad_arity, f"BOM rows with wrong column count: {bad_arity}"
    sections = {r[0] for r in rows}
    allowed = {"main", "head", "grad"}
    stray = sections - allowed
    assert not stray, f"BOM sections outside {sorted(allowed)}: {sorted(stray)}"
    for missing in allowed - sections:
        assert False, f"BOM has no '{missing}' section"


def test_bom_rows_unique_and_plausible():
    _, rows = _bom_rows()
    keys = []
    for r in rows:
        designators = [d.strip() for d in r[1].split(",") if d.strip()]
        assert designators, f"BOM row without designators: {r}"
        qty = int(r[2])
        assert qty >= 1 and qty == len(designators), (
            f"BOM row Qty {r[2]} != {len(designators)} designators: {r[:3]}"
        )
        keys.append((r[0], tuple(designators)))
    dups = {k for k in keys if keys.count(k) > 1}
    assert not dups, f"duplicate BOM rows: {sorted(dups)[:4]}"
    # designator uniqueness is per section — the same designator name on two
    # boards is legal (J12/Uchip exist on main, head and grad independently)
    per_section = {}
    for sec, ds in keys:
        per_section.setdefault(sec, []).extend(ds)
    for sec, refs in per_section.items():
        dup_refs = {d for d in refs if refs.count(d) > 1}
        assert not dup_refs, f"{sec}: refdes claimed by two rows: {sorted(dup_refs)}"


def test_bom_grad_refs_are_grad_board_refs():
    _need_grad()
    grad_board = {f[0] for f in _board_footprints("grad")}
    _, rows = _bom_rows()
    grad_refs = {
        d.strip()
        for r in rows
        if r[0] == "grad"
        for d in r[1].split(",")
        if not d.strip().startswith("#")
    }
    ghost = sorted(grad_refs - grad_board)
    assert not ghost, f"grad BOM refs not placed on qssc-grad: {ghost}"


def test_bom_main_deficit_is_exactly_c87():
    """C87 is the one known, documented BOM↔board deficit (1206 47uF listed,
    not placed). If anything else goes missing, this fails — that is the
    sabotage grade this test exists for. See PR/issue notes on C87.

    '#' refs are gen_bom.py's PWR_FLAG annotation rows; the board never
    places them as footprints, so they are excluded like on the sheets."""
    main_board = {f[0] for f in _board_footprints("main")}
    _, rows = _bom_rows()
    main_refs = {
        d.strip()
        for r in rows
        if r[0] == "main"
        for d in r[1].split(",")
        if not d.strip().startswith("#")
    }
    deficit = sorted(main_refs - main_board)
    assert deficit == ["C87"], (
        f"main BOM↔board deficit changed (expected exactly ['C87']): {deficit}"
    )


def test_bom_crlf_consistent():
    data = _raw("hardware/datasheets/BOM.csv")
    n_lf, n_crlf = data.count(b"\n"), data.count(b"\r\n")
    assert n_lf == n_crlf, f"BOM.csv mixes line endings: {n_lf} LF, {n_crlf} CRLF"


# ------------------------------------------------- historical preservation


# ---- main sheet, post-gradient-merge contract -------------------------------
# The published main board IS the extended build now (gradient block onboard,
# no third board).  That merge re-baselined hardware/pcb/qssc.kicad_sch: the
# USB-C-repair baseline at BASELINE no longer describes it byte-for-byte, and
# the generator recreates every node with a fresh uuid4, so the uuid-preservation
# contract below cannot hold for main either.  The replacement contract keeps
# the same strictness:
#   * the sheet content is frozen by digest (any edit must consciously
#     re-pin MAIN_SHEET_SHA256 here -- same contract style as the ERC counts);
#   * every symbol present at the repair baseline still exists;
#   * exactly the 8 gradient parts were added, nothing removed;
#   * no shared symbol drifted, except U1's documented swap to the _GRAD
#     symbol variant (it gains the grad_cs/sclk/mosi balls).
MAIN_SHEET_SHA256 = (
    "f993c4007de5e455e5c8c12a085207b1818c8a3332fc9ab8321d9caa761641b6"
)
MAIN_GRAD_REFS = {"Ugrad", "Udrv", "Qgrad", "Rgrad_feed", "Lgrad", "Rsense",
                  "C_grad1", "C_grad2"}


def _main_sheet_contract():
    """Digest + merge-delta guarantees for the re-baselined main sheet."""
    digest = hashlib.sha256(_raw(BOARDS["main"][0])).hexdigest()
    assert digest == MAIN_SHEET_SHA256, (
        "main sheet changed without re-pinning MAIN_SHEET_SHA256 "
        f"(pinned {MAIN_SHEET_SHA256[:12]}..., now {digest[:12]}...)"
    )
    before = _baseline_instances("main")
    after = _sheet_instances("main")
    refs_before = {i["ref"] for i in before}
    refs_after = {i["ref"] for i in after}
    gone = sorted(refs_before - refs_after)
    assert not gone, f"main: baseline symbols disappeared: {gone}"
    added = refs_after - refs_before
    assert added == MAIN_GRAD_REFS, (
        f"main: expected exactly the 8 gradient parts added, got {sorted(added)}"
    )
    by_ref_b = {i["ref"]: i for i in before}
    by_ref_a = {i["ref"]: i for i in after}
    drifted = []
    for r in sorted(refs_before):
        b, a = by_ref_b[r], by_ref_a[r]
        same_geom = all(b[k] == a[k] for k in ("x", "y", "value", "footprint"))
        if (b["lib_id"], b["x"], b["y"], b["value"], b["footprint"]) == (
            a["lib_id"], a["x"], a["y"], a["value"], a["footprint"]
        ):
            continue
        if (r == "U1" and same_geom and b["lib_id"] == "qssc:XC7A35T_CSG324"
                and a["lib_id"] == "qssc:XC7A35T_CSG324_GRAD"):
            continue        # documented: U1 gains the gradient signal balls
        drifted.append(r)
    assert not drifted, f"main: symbols drifted vs baseline: {drifted}"


@pytest.mark.skipif(
    _baseline_blob("hardware/pcb/qssc.kicad_sch") is None,
    reason=f"baseline {BASELINE} not present in this clone",
)
@pytest.mark.parametrize(
    "name", ["main"] + (["grad"] if "grad" in BOARDS else [])
)
def test_repair_preserved_symbols(name):
    """Everything about every symbol except the 8 added no_connect markers
    must be byte-for-byte identical to the baseline.  main was re-baselined
    by the gradient merge -- see MAIN_SHEET_SHA256 / _main_sheet_contract."""
    if name == "main":
        _main_sheet_contract()
        return
    before = _baseline_instances(name)
    after = _sheet_instances(name)
    refs_before = sorted(i["ref"] for i in before)
    refs_after = sorted(i["ref"] for i in after)
    assert refs_before == refs_after, f"{name}: refdes set changed"

    uuid_b = set(
        re.findall(r'\(uuid "([^"]+)"', _baseline_blob(BOARDS[name][0]).decode("utf-8"))
    )
    uuid_a = set(re.findall(r'\(uuid "([^"]+)"', _text(BOARDS[name][0])))
    added = uuid_a - uuid_b
    assert uuid_b <= uuid_a, f"{name}: baseline uuids disappeared"
    # main + grad each gained exactly the 8 NC-marker uuids, nothing else
    assert len(uuid_a) == len(uuid_b) + 8, (
        f"{name}: expected +8 uuids (the NC markers), got {len(uuid_a) - len(uuid_b)}"
    )
    stray = [
        u
        for u in added
        if not re.search(
            r'\(no_connect[\s\S]{0,200}\(uuid "' + u + '"', _text(BOARDS[name][0])
        )
    ]
    assert not stray, f"{name}: non-marker uuids added by the repair: {stray}"

    by_ref_b = {i["ref"]: i for i in before}
    by_ref_a = {i["ref"]: i for i in after}
    drifted = []
    for r in refs_before:
        b, a = by_ref_b[r], by_ref_a[r]
        if (b["lib_id"], b["x"], b["y"], b["value"], b["footprint"]) != (
            a["lib_id"],
            a["x"],
            a["y"],
            a["value"],
            a["footprint"],
        ):
            drifted.append(r)
    assert not drifted, f"{name}: symbols drifted vs baseline: {drifted}"


@pytest.mark.skipif(
    _baseline_blob("hardware/pcb/chip_module.kicad_sch") is None,
    reason=f"baseline {BASELINE} not present in this clone",
)
def test_head_sheet_byte_identical():
    before = _baseline_blob("hardware/pcb/chip_module.kicad_sch")
    assert before == _raw("hardware/pcb/chip_module.kicad_sch"), (
        "chip_module.kicad_sch changed vs baseline — the repair must not "
        "touch the head board"
    )


@pytest.mark.skipif(
    _baseline_blob("hardware/datasheets/BOM.csv") is None,
    reason=f"baseline {BASELINE} not present in this clone",
)
def test_bom_delta_is_exactly_rgrad_feed():
    """Pin the reviewed BOM delta against the baseline.

    Reviewed deltas (issues #162/#166/#167/#168/#169):
      ~ Udrv MPN LM358P -> LM358DR (SOIC-8 ordering needs the D/DR suffix)
      ~ J9 MPN 61300411121 -> 61301011121 (2x05 needs 10 contacts)
      ~ U3/Y1/J4 gain the MPN their own footprint/value already identified
        (ADF4351BCPZ, Abracon ASE-100MHz, CUI PJ-102AH) -- their MPN_MAP keys
        matched no row, so main and grad both shipped blank (issue #169)
      + Rgrad_feed (grad coil 0R feed)
      - the two #FLG*/PWR_FLAG phantom rows (issue #167: not purchasable
        parts, never become a footprint)
      - the six hand-typed grad rows, replaced by the full gradient section
        gen_bom.py now generates (issue #162), grouped exactly as the two
        sandwich boards are grouped

    The pin stays exact in both directions: a row that silently reverts an
    MPN repair fails the `removed` side, and a row that appears or disappears
    unreviewed fails the `added` side.
    """
    before = _baseline_blob("hardware/datasheets/BOM.csv").decode("utf-8")
    after = _text("hardware/datasheets/BOM.csv")
    b_lines, a_lines = before.splitlines(), after.splitlines()
    removed = sorted(set(b_lines) - set(a_lines))
    added = sorted(set(a_lines) - set(b_lines))
    assert removed == [
            'grad,"C_grad1, C_grad2",2,100nF,Capacitor_SMD:C_0603_1608Metric,,,',
            'grad,Udrv,1,LM358,Package_SO:SOIC-8_3.9x4.9mm_P1.27mm,Texas Instruments,LM358P,https://www.ti.com/lit/ds/symlink/lm358.pdf',
            'head,"#FLG10, #FLG11, #FLG12, #FLG9",4,PWR_FLAG,,,,',
            'main,"#FLG1, #FLG2, #FLG3, #FLG4, #FLG5, #FLG6, #FLG7, #FLG8",8,PWR_FLAG,,,,',
            'main,J4,1,12V LED,Connector_BarrelJack:BarrelJack_CUI_PJ-102AH_Horizontal,,,',
            'main,J9,1,JTAG,Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical,Würth,61300411121,',
            'main,U3,1,ADF4351BCPZ,Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm,,,',
            'main,Y1,1,100MHz XO,Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm,,,',
        ], f"BOM rows removed vs baseline: {removed}"
    assert added == [
            'grad,"C31, C33",2,220p,Capacitor_SMD:C_0603_1608Metric,,,',
            'grad,"C34, C35",2,100p,Capacitor_SMD:C_0603_1608Metric,,,',
            'grad,"C36, C37, C41",3,100n,Capacitor_SMD:C_0603_1608Metric,,,',
            'grad,"C49, C50, C85",3,22uF,Capacitor_SMD:C_0805_2012Metric,,,',
            'grad,"C52, C53, C54, C57, C84",5,4.7uF,Capacitor_SMD:C_0805_2012Metric,,,',
            'grad,"C_grad1, C_grad2, C1, C2, C3, C4, C5, C6, C7, C8, C9, C10, C11, C12, C13, C14, C15, C16, C17, C18, C19, C20, C40, C44, C46, C48, C55, C56, C58, C59, C60, C61, C62, C63, C64, C65, C68, C86, Cem0, Cem1, Cem2",41,100nF,Capacitor_SMD:C_0603_1608Metric,,,',
            'grad,"C_in, C21, C22, C23, C24, C25, C26, C27, C28, C29, C30, C42, C43, C45, C47, C51",16,10uF,Capacitor_SMD:C_0805_2012Metric,,,',
            'grad,"D1, D2, D3, D4, D5, D6, D7, D8, D9, D10",10,ESD5V0,Diode_SMD:D_SOD-923,ON Semi,ESD5V0S3B-U,https://www.onsemi.com/pub/Collateral/ESD5V0S3B-U-D.PDF',
            'grad,"R71, R72",2,5.1k,Resistor_SMD:R_0603_1608Metric,,,',
            'grad,"R73, R75, R76, R77, R80, R81, R82",7,4.7k,Resistor_SMD:R_0603_1608Metric,,,',
            'grad,"R78, R79",2,27R,Resistor_SMD:R_0603_1608Metric,,,',
            'grad,"Riref0, Riref1, Riref2",3,4.87k,Resistor_SMD:R_0603_1608Metric,,,',
            'grad,"UE0, UE1, UE2",3,TLC5947,Package_SO:HTSSOP-32-1EP_6.1x11mm_P0.65mm_EP5.2x11mm_Mask4.11x4.36mm_ThermalVias,Texas Instruments,TLC5947,https://www.ti.com/lit/ds/symlink/tlc5947.pdf',
            'grad,C32,1,10n,Capacitor_SMD:C_0603_1608Metric,,,',
            'grad,D_USB,1,SS14 Schottky,Diode_SMD:D_SMA,,,',
            'grad,F1,1,2A PTC,Fuse:Fuse_1206_3216Metric,Littelfuse,nanoSMDC020F,https://www.littelfuse.com/media?resourcetype=datasheets&value=0810070',
            'grad,F2,1,1A PTC,Fuse:Fuse_1206_3216Metric,,,',
            'grad,FB1,1,600Ω@100MHz,Inductor_SMD:L_0603_1608Metric,Murata,BLM18AG601SN1D,https://www.murata.com/products/productdata/8796738650146/ENFA0003.pdf',
            'grad,J1,1,FT232RL,Package_SO:SSOP-28_5.3x10.2mm_P0.65mm,FTDI,FT232RL,https://www.ftdichip.com/Support/Documents/DataSheets/ICs/DS_FT232R.pdf',
            'grad,J10,1,USB-C,Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal,GCT,USB4105-GF-610-50-B,https://www.gct.cc/connector/usb4105/',
            'grad,J12,1,BTB_HOST,qssc:Conn_02x10_BTB,Würth Elektronik,61302011121,https://www.we-online.com/en/components/products/WR-PHD_2_54_MM_SMT_PIN_HEADER_61302011121',
            'grad,J2,1,5V Barrel,Connector_BarrelJack:BarrelJack_CUI_PJ-102AH_Horizontal,CUI,PJ-102AH,https://www.cui.com/product/resource/pj-102a.pdf',
            'grad,J3,1,SMA,Connector_Coaxial:SMA_Amphenol_132289_EdgeMount,Amphenol,132289,https://www.amphenolrf.com/132289.html',
            'grad,J4,1,12V LED,Connector_BarrelJack:BarrelJack_CUI_PJ-102AH_Horizontal,CUI,PJ-102AH,https://www.cui.com/product/resource/pj-102a.pdf',
            'grad,J7,1,RESET,Button_Switch_THT:SW_PUSH_6mm_H5mm,Würth,430182050816,',
            'grad,J8,1,MW_GATE,Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical,Würth,61300211121,',
            'grad,J9,1,JTAG,Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical,Würth,61301011121,',
            'grad,L2,1,100nH,Inductor_SMD:L_0603_1608Metric,,,',
            'grad,LED1,1,Green,LED_SMD:LED_0603_1608Metric,,,',
            'grad,R67,1,1k,Resistor_SMD:R_0603_1608Metric,,,',
            'grad,R68,1,2.2k,Resistor_SMD:R_0603_1608Metric,,,',
            'grad,R70,1,330,Resistor_SMD:R_0603_1608Metric,,,',
            'grad,R83,1,10k,Resistor_SMD:R_0603_1608Metric,,,',
            'grad,REG1,1,AMS1117-3.3,Package_TO_SOT_SMD:SOT-223-3_TabPin2,AMS,AMS1117-3.3,https://www.mouser.com/datasheet/2/260/AMS1117-Datasheet-1273816.pdf',
            'grad,REG2,1,AMS1117-1.8,Package_TO_SOT_SMD:SOT-223-3_TabPin2,AMS,AMS1117-1.8,https://www.mouser.com/datasheet/2/260/AMS1117-Datasheet-1273816.pdf',
            'grad,REG3,1,AP2127K-1.0,Package_TO_SOT_SMD:SOT-23-5,Diodes Inc,AP2127K-1.0,https://www.diodes.com/assets/Datasheets/AP2127K.pdf',
            'grad,Rgrad_feed,1,0R,Resistor_SMD:R_0805_2012Metric,,,',
            'grad,U1,1,XC7A35T-1CSG324C,Package_BGA:Xilinx_CSG324,AMD/Xilinx,XC7A35T-1CSG324C,https://docs.amd.com/r/en-US/ds181-Artix-7-Data-Sheet',
            'grad,U3,1,ADF4351BCPZ,Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm,Analog Devices,ADF4351BCPZ,https://www.analog.com/media/en/technical-documentation/data-sheets/ADF4351.pdf',
            'grad,U5,1,GVA-84+,Package_TO_SOT_SMD:SOT-89-3,Mini-Circuits,GVA-84+,https://www.minicircuits.com/WebStore/dashboard.html?model=GVA-84%2B',
            'grad,U8,1,W25Q128JVSIQ,Package_SO:SOIC-8_3.9x4.9mm_P1.27mm,Winbond,W25Q128JVSIQ,https://www.winbond.com/resource-files/w25q128jv%20revh%2004082019%20plus.pdf',
            'grad,Uchip,1,ChipSocket_2.54_EM,qssc:ChipSocket_2.54_EM,,,',
            'grad,Udrv,1,LM358,Package_SO:SOIC-8_3.9x4.9mm_P1.27mm,Texas Instruments,LM358DR,https://www.ti.com/lit/ds/symlink/lm358.pdf',
            'grad,Y1,1,100MHz XO,Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm,Abracon,ASE-100.000MHZ-LR,https://abracon.com/Oscillators/ASE.pdf',
            'main,J4,1,12V LED,Connector_BarrelJack:BarrelJack_CUI_PJ-102AH_Horizontal,CUI,PJ-102AH,https://www.cui.com/product/resource/pj-102a.pdf',
            'main,J9,1,JTAG,Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical,Würth,61301011121,',
            'main,U3,1,ADF4351BCPZ,Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm,Analog Devices,ADF4351BCPZ,https://www.analog.com/media/en/technical-documentation/data-sheets/ADF4351.pdf',
            'main,Y1,1,100MHz XO,Oscillator:Oscillator_SMD_Abracon_ASE-4Pin_3.2x2.5mm,Abracon,ASE-100.000MHZ-LR,https://abracon.com/Oscillators/ASE.pdf',
        ], f"BOM rows added vs baseline: {added}"

# ------------------------------------------------- simulator constants


def test_code_ordering_and_ode_consistency():
    assert 0 < sc.BG_CODE < sc.DARK_CODE < sc.BRIGHT_CODE, (
        f"codes must order BG < DARK < BRIGHT, got {sc.BG_CODE}, "
        f"{sc.DARK_CODE}, {sc.BRIGHT_CODE}"
    )
    assert sc.DARK_CODE == int(sc.BRIGHT_CODE * (1 - sc.ODMR_CONTRAST)), (
        "DARK_CODE is not BRIGHT_CODE degraded by ODMR_CONTRAST"
    )


def test_codes_fit_adc_and_thresholds():
    assert sc.ADC_MAX == (1 << sc.ADC_BITS) - 1
    for label, code in (
        ("BRIGHT", sc.BRIGHT_CODE),
        ("DARK", sc.DARK_CODE),
        ("BG", sc.BG_CODE),
        ("DETECT", sc.DETECT_THRESHOLD),
    ):
        assert 0 <= code <= sc.ADC_MAX, f"{label}_CODE outside ADC range: {code}"
    assert sc.READOUT_NOISE < sc.BG_CODE, "noise floor exceeds the background"
    assert sc.BG_CODE < sc.DETECT_THRESHOLD, "detection threshold inside background"


def test_rates_plausible():
    assert 1e5 <= sc.ODMR_LINEWIDTH_HZ <= 1e8, sc.ODMR_LINEWIDTH_HZ
    assert 1e1 <= sc.LOCKIN_MOD_HZ <= 1e6, sc.LOCKIN_MOD_HZ
    assert sc.ODMR_LINEWIDTH_HZ > 10 * sc.LOCKIN_MOD_HZ, (
        "lock-in modulation must sit far inside the ODMR linewidth"
    )
    assert 1.0 < sc.ASE_GAIN <= 2.0, sc.ASE_GAIN


def test_fractions_unit_interval():
    for label, v in (
        ("ODMR_CONTRAST", sc.ODMR_CONTRAST),
        ("SITE_FAB_YIELD", sc.SITE_FAB_YIELD),
    ):
        assert 0 < v <= 1, f"{label} outside (0, 1]: {v}"


def test_inventory_counts_positive():
    assert sc.N_SITES > 0 and sc.N_NV_PER_PIXEL > 0
    assert 8 <= sc.ADC_BITS <= 32


# ------------------------------------------------- test-suite meta


@pytest.mark.parametrize("path", sorted(Path(REPO, "tests").glob("test_*.py")))
def test_files_are_real_tests(path):
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(REPO).as_posix()
    assert "def test_" in text, f"{rel}: no test function at all"
    assert not re.search(r"\bassert True\b", text), f"{rel}: contains no-op assert"
    if path.name != Path(__file__).name:
        needle = "scr" + "atch"
        assert needle not in text, f"{rel}: references throwaway worktrees"
