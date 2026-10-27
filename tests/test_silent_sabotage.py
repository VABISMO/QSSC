# tests/test_silent_sabotage.py
"""Anti-sabotage gates: patterns a committed change can use to quietly
degrade the project while every other gate stays green.

The trigger for this file: the question *"what code would silently sabotage
the project?"* -- not malware hunting, but the mundane failure modes of a
repo that mostly audits itself: a broad ``except Exception: pass``
swallowing a broken PCB save, a subprocess without a timeout that wedges CI
forever, an ``open()`` whose default re-encodes a UTF-8 board file under a
non-UTF-8 locale, ``eval``/``pickle`` on data that has become attacker
influence, a wall-clock read inside the deterministic engine, or an
unseeded RNG call that makes an "identical" re-run of the calibration
quietly differ. Each of these is invisible in review because it is
*plausible code*, each is invisible to DRC/ERC because it is not
electrical, and each produces no test failure until it is too late.

The gates are structural static scans (``ast``-based, so multi-line calls
and single-line ``except X: pass`` forms are found). Scoping mirrors the
failure modes:

* *prod* (``qssc/`` + ``api/``) -- zero tolerance for the top hazards,
  with ONE exact allowlist: the five ``except Exception: pass`` handlers
  in ``api/`` that are verified deliberate failover points (each already
  carries ``# noqa: BLE001``): jobs.py Mongo->Redis->in-process status
  ladder and broker-down reconciliation, the malformed-redis-record skip
  and the watchdog race, runner.py's double-fault ``close()``.
* *tools* (``tools/``) -- exact allowlists for the verified-correct
  instances (parse-repair fallbacks around hand-maintained PCB files, and
  KiCad subprocess calls whose real hazard is being killed mid-save).

``tests/``, ``hardware/``, ``fpga/`` and the throwaway work-dirs are outside
the scans.

All allowlists are EXACT (set equality in BOTH directions): every addition
or removal of a tolerated pattern, or any re-pin target that no longer
exists, fails until the list is consciously edited. That is the deliberate
opposite of a growing ignore-list: repaired code re-pins, new instances
need review.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------
# region split + parse cache
# --------------------------------------------------------------------------
PROD_DIRS = ("qssc", "api")
# Test files may not carry the literal throwaway-dir name (test_dumb_mistakes
# gates that, and splits its own needle the same way), so the dir name is
# built here instead of written out.
SCR_DIR_NAME = "scr" + "atches"
SCOPED_EXCLUDE = {"tests", "hardware", "fpga", SCR_DIR_NAME, ".git",
                  # Non-source trees a contributor has locally but CI does not:
                  # virtualenvs, dependency trees and caches. The walks below
                  # do not consult .gitignore, so without these a repo-local
                  # `.venv` filled every global gate with third-party code and
                  # made the whole suite fail on any developer machine.
                  ".venv", "venv", "node_modules", "__pycache__",
                  ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
                  # gitignored work dir: only the two pinned snapshots are
                  # tracked (see test_no_junk_tracked); its ad-hoc helper
                  # scripts are per-machine and must not gate CI.
                  ".tmpwork"}


def _prod_files() -> list:
    return sorted(p for d in PROD_DIRS for p in (REPO / d).rglob("*.py"))


def _tools_files() -> list:
    return sorted((REPO / "tools").rglob("*.py"))


def _test_files() -> list:
    return sorted((REPO / "tests").rglob("*.py"))


def _all_py_files() -> list:
    """Everything the global gates see: prod + tools + root-level scripts
    (tests/hardware/fpga and the throwaway work-dirs intentionally
    excluded)."""
    out = set(_prod_files()) | set(_tools_files())
    out.update(p for p in REPO.rglob("*.py")
               if not any(part in SCOPED_EXCLUDE for part in p.parts))
    return sorted(out)


@lru_cache(maxsize=2048)
def _tree(rel: str):
    src = (REPO / rel).read_text(encoding="utf-8")
    return ast.parse(src, filename=rel)


def _rel(p: Path) -> str:
    return p.relative_to(REPO).as_posix()


def test_scan_scope_sanity():
    """The gates are only meaningful if the scans actually see the repo: if
    a rename eats a scope, the counts below shift and this fails."""
    assert len(_prod_files()) >= 35, len(_prod_files())
    assert len(_tools_files()) >= 55, len(_tools_files())
    assert len(_test_files()) >= 40, len(_test_files())
    assert len(_all_py_files()) >= 100, len(_all_py_files())


# --------------------------------------------------------------------------
# general helpers over the cached ASTs
# --------------------------------------------------------------------------
def _funcs(tree) -> list:
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _call_name(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _calls(tree, names: set):
    """Yield Call nodes whose function name/attr is in *names*
    (open(...) and Path.open(...) both surface as the name 'open')."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) in names:
            yield node


def _is_str_const(node, values=None) -> bool:
    return (isinstance(node, ast.Constant) and isinstance(node.value, str)
            and (values is None or node.value in values))


def _has_kw(call: ast.Call, kw: str) -> bool:
    return any(k.arg == kw for k in call.keywords)


# --------------------------------------------------------------------------
# 1. mutable default arguments: one call can poison every later call
# --------------------------------------------------------------------------
def test_no_mutable_default_argument():
    bad: dict = {}
    for p in _all_py_files():
        rel = _rel(p)
        for fn in _funcs(_tree(rel)):
            for d in [*fn.args.defaults, *fn.args.kw_defaults]:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)) or isinstance(d, ast.Call) and _call_name(d) in {
                        "list", "dict", "set"}:
                    bad.setdefault(rel, []).append(fn.name)
    assert not bad, f"mutable default argument(s): {bad}"


# --------------------------------------------------------------------------
# 2. bare except: also swallows KeyboardInterrupt/SystemExit
# --------------------------------------------------------------------------
def test_no_bare_except():
    bad: dict = {}
    for p in _all_py_files():
        rel = _rel(p)
        for node in ast.walk(_tree(rel)):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                bad.setdefault(rel, []).append(node.lineno)
    assert not bad, f"bare 'except:' (also catches KeyboardInterrupt): {bad}"


# --------------------------------------------------------------------------
# 3. broad + silent: ``except (Base)Exception: pass`` -- the class of
#    swallow that makes a failed save/calculation look like a success.
#    Pinned by the line of the ``pass`` (== the handler line for the
#    single-line forms, == the body line for multi-line handlers).
# --------------------------------------------------------------------------
def _broad_silent(rel: str) -> list:
    out = []
    for node in ast.walk(_tree(rel)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        t = node.type
        if isinstance(t, ast.Tuple):
            parts = {getattr(e, "id", getattr(e, "attr", ""))
                     for e in t.elts}
        else:
            parts = {getattr(t, "id", getattr(t, "attr", ""))} if t else set()
        if ({"Exception", "BaseException"} & parts
                and len(node.body) == 1
                and isinstance(node.body[0], ast.Pass)):
            out.append(node.body[0].lineno)
    return out


# verified-correct prod instances: each handler is a documented failover
# point and already carries a ruff BLE001-suppressing noqa comment.
# EXACT equality both ways.
PROD_BROAD_SILENT_ALLOW = {
    ("api/jobs.py", 153),   # Mongo -> Redis -> in-process status ladder
    ("api/jobs.py", 272),   # skip malformed Redis status records
    ("api/jobs.py", 407),   # watchdog race: worker logs its own failure
    ("api/jobs.py", 500),   # RQ broker down -> status reconciliation
    ("api/runner.py", 204),  # _HW_FPGA.close() double fault on invalidate
}


def test_prod_no_broad_silent_pass():
    got = {(rel, lno) for p in _prod_files()
           for lno in _broad_silent(_rel(p)) for rel in [_rel(p)]}
    assert got == PROD_BROAD_SILENT_ALLOW, (
        f"prod broad-silent allowlist drifted -- new except-Exception:"
        f"pass handlers (review then re-pin): "
        f"{sorted(got - PROD_BROAD_SILENT_ALLOW)}; "
        f"repaired/re-pinned: {sorted(PROD_BROAD_SILENT_ALLOW - got)}")


# verified-correct tolerate-list (tools/ only): parse-repair fallbacks around
# hand-maintained PCB files. Lines are pinned to the PASS line (AST
# body[0].lineno = handler line + 1 for multi-line handlers). EXACT equality.
TOOLS_BROAD_SILENT_ALLOW = {
    ("tools/_kicad_compat.py", 94),
    ("tools/inject_base_routing.py", 62),
    ("tools/inject_base_routing.py", 66),
    ("tools/inject_base_routing.py", 163),
    ("tools/benchmark_circuits.py", 376),
    ("tools/copy_zones.py", 37),
    ("tools/copy_zones.py", 41),
    ("tools/copy_zones.py", 45),
    ("tools/widen_tracks.py", 37),
    ("tools/router/planes.py", 80),
}


def test_tools_broad_silent_pass_allowlist_exact():
    got = {(rel, lno) for p in _tools_files()
           for lno in _broad_silent(_rel(p)) for rel in [_rel(p)]}
    assert got == TOOLS_BROAD_SILENT_ALLOW, (
        f"broad-silent allowlist drifted -- new instances (review then "
        f"re-pin): {sorted(got - TOOLS_BROAD_SILENT_ALLOW)}; "
        f"repaired/re-pinned: {sorted(TOOLS_BROAD_SILENT_ALLOW - got)}")


# --------------------------------------------------------------------------
# 4. dynamic code execution on non-literal input
# --------------------------------------------------------------------------
def test_no_dynamic_code_exec():
    """eval/exec only with an explicit ``# noqa: S102`` on the call line
    (bandit convention); pickle/pickle.loads and yaml-until-SafeLoader are
    not tolerated -- deserialising data whose producer might be
    influenced."""
    bad: dict = {}
    for p in _all_py_files():
        rel = _rel(p)
        src_lines = (REPO / rel).read_text(encoding="utf-8").splitlines()
        for node in ast.walk(_tree(rel)):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else None
            attr = f.attr if isinstance(f, ast.Attribute) else None
            base = f.value if isinstance(f, ast.Attribute) else None
            base_id = getattr(base, "id", None)
            flagged = False
            if name in {"eval", "exec", "compile"}:
                line = src_lines[node.lineno - 1] if node.lineno <= len(
                    src_lines) else ""
                flagged = "# noqa: S102" not in line
            elif base_id in {"pickle", "cPickle", "shelve", "dill"} and \
                    attr in {"loads", "load", "Unpickler", "UnpicklerFile",
                             "unpickle"}:
                flagged = True
            elif base_id == "yaml" and attr in {"load", "unsafe_load",
                                                "full_load"}:
                # explicit SafeLoader = trusted-by-construction
                flagged = not any(k.arg in {"Loader", "loader"}
                                  for k in node.keywords)
            if flagged:
                bad.setdefault(rel, []).append((attr or name, node.lineno))
    assert not bad, f"dynamic code execution: {bad}"


# --------------------------------------------------------------------------
# 5. shell: no os.system / shell=True -- subprocess with a list, always
# --------------------------------------------------------------------------
def test_no_shell():
    bad: dict = {}
    for p in _all_py_files():
        rel = _rel(p)
        for i, line in enumerate((REPO / rel).read_text(
                encoding="utf-8").splitlines(), 1):
            if "os.system(" in line or re.search(r"\bshell\s*=\s*True", line):
                bad.setdefault(rel, []).append(i)
    assert not bad, f"shell outs: {bad}"


# --------------------------------------------------------------------------
# 6. text-mode open() without explicit encoding (the default-encoding trap)
# --------------------------------------------------------------------------
def test_text_open_uses_explicit_encoding():
    bad: dict = {}
    for p in _all_py_files():
        rel = _rel(p)
        nos = []
        for call in _calls(_tree(rel), {"open"}):
            if _has_kw(call, "encoding"):
                continue
            if any(_is_str_const(a, {"rb", "wb", "ab", "br", "bw", "ba"})
                   for a in call.args[:2]):
                continue
            nos.append(call.lineno)
        if nos:
            bad[rel] = nos
    assert not bad, (
        f"text-mode open() without explicit encoding: {bad}\n"
        f"(the default encoding of a locale-mismatched machine silently "
        f"corrupts UTF-8 PCB/SCH/BOM reads)")


# --------------------------------------------------------------------------
# 7. subprocess.run without a timeout: one wedged child stops CI forever
# --------------------------------------------------------------------------
def _subprocess_nodes(rel: str) -> list:
    """subprocess.run / check_output / check_call / call / Popen / getoutput
    (getoutput implies shell -- flagged always)."""
    out = []
    for node in ast.walk(_tree(rel)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            module = node.func.value
            if node.func.attr in {"run", "check_output", "check_call", "call",
                                  "Popen", "getoutput", "getstatusoutput"} \
                    and isinstance(module, ast.Name) and \
                    module.id == "subprocess":
                out.append(node)
    return out


def test_subprocess_timeout():
    prod_bad: dict = {}
    tools_got = set()
    tools_extra = set()          # non-run subprocess flavors (never allowed)
    for p in _prod_files() + _tools_files():
        rel = _rel(p)
        for call in _subprocess_nodes(rel):
            attr = call.func.attr
            is_run_flavor = attr == "run"
            accepts_timeout = attr in {"run", "check_output", "check_call",
                                       "call"}
            if _has_kw(call, "timeout") and accepts_timeout:
                continue
            lno = call.lineno
            if p in _prod_files():
                prod_bad.setdefault(rel, []).append(lno)
            elif is_run_flavor:
                tools_got.add((rel, lno))
            else:
                tools_extra.add((rel, call.func.attr, lno))
    assert not prod_bad, (
        f"subprocess call without timeout= in prod code (one wedged child "
        f"freezes CI forever): {prod_bad}")
    assert not tools_extra, (
        f"tools/ uses non-run subprocess flavors (check_output/Popen/...) "
        f"without timeout -- route them through subprocess.run(+timeout) or "
        f"consciously re-pin: {sorted(tools_extra)}")
    want = {
        ("tools/freeroute.py", 358),
        ("tools/route_qssc_pcb.py", 176),
    }
    assert tools_got == want, (
        f"tools/ subprocess.run-without-timeout drifted -- new (review: a "
        f"KiCad child must either get one or prove it cannot hang) "
        f"{sorted(tools_got - want)}; "
        f"repaired/re-pinned {sorted(want - tools_got)}")


# --------------------------------------------------------------------------
# 8. no wall-clock inside the deterministic engine (qssc/ only)
# --------------------------------------------------------------------------
_WALLCLOCK = re.compile(
    r"\btime\.time\s*\(|\btime\.monotonic\b|\btime\.perf_counter"
    r"|\bdatetime\.datetime\.now\b|\bdatetime\.now\b|fromtimestamp\s*\("
    r"|\bdate\.today\s*\(|\btime\.ctime\s*\(")


def test_engine_no_wallclock():
    """Clockless engine: a wall-clock read inside ``qssc/`` makes re-runs of
    the calibration non-comparable, so ``qssc/`` is scanned with the single
    deliberate exception (crypto.py's key-space anchor). ``api/`` is
    deliberately OUT of this scan -- there the clock is bookkeeping, not
    determinism: ``_guards.py`` rate-limit buckets (``time.monotonic``),
    the job ``created_at`` timestamps (``_now_ms``) and the runner's
    ``perf_counter`` t0/elapsed_ms latency pairs are all verified-correct
    and none of them feeds the engine.
    """
    hits: dict = {}
    for p in _prod_files():
        rel = _rel(p)
        if not rel.startswith("qssc/"):
            continue
        for i, line in enumerate((REPO / rel).read_text(
                encoding="utf-8").splitlines(), 1):
            if _WALLCLOCK.search(line):
                hits.setdefault(rel, []).append(i)
    # crypto.py reads the clock as a *key-space* anchor (deliberate,
    # documented); everything else in the engine must be clockless.
    allow = {("qssc/crypto.py", 77)}
    got = {(rel, lno) for rel, h in hits.items() for lno in h}
    assert got == allow, (
        f"wallclock reads in the engine drifted -- new (a wall-clock read "
        f"makes re-runs non-comparable) {sorted(got - allow)}; "
        f"re-pinned {sorted(allow - got)}")


# --------------------------------------------------------------------------
# 9. engine RNG calls must be seeded (or allowlisted as verified fallbacks)
# --------------------------------------------------------------------------
# np.random.<fn>() with NO arguments silently falls back to OS entropy: two
# "identical" engine runs then disagree. Issue #170 option 1 removed the
# ``rng = rng or np.random.default_rng()`` convenience defaults -- the
# generator parameter is REQUIRED at every engine entry point (or guarded
# with an explicit ValueError), so every call site threads a seeded rng
# from --seed / the API ctx. The allowlist shrank 9 -> 1: the deliberate
# Miller-Rabin fresh-entropy witness draw (a witness that is predictable
# from a seed weakens the primality proof) is the only remaining zero-arg
# np.random site in the engine.
_RNG_UNSEEDED_ALLOW = {
    ("qssc/circuits.py", 53),       # Miller-Rabin witness (deliberate)
}


def _np_random_calls(rel: str) -> list:
    """ast.Call nodes for <np|numpy|npr>.random.<fn>(...) -- includes the
    np.random.random() form (its attr is 'random' as well)."""
    out = []
    for node in ast.walk(_tree(rel)):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)):
            continue
        inner = node.func.value
        if (isinstance(inner, ast.Attribute) and inner.attr == "random"
                and isinstance(inner.value, ast.Name)
                and inner.value.id in {"np", "numpy", "npr"}):
            out.append(node)
    return out


def test_engine_rng_calls_seeded_or_allowlisted():
    sites: dict = {}          # all np.random.* call sites (for the freeze)
    unseeded: dict = {}       # zero-argument calls = OS entropy fallback
    for p in _prod_files():
        rel = _rel(p)
        for call in _np_random_calls(rel):
            sites.setdefault(rel, []).append(call.lineno)
            if not call.args and not call.keywords:
                unseeded.setdefault(rel, []).append(call.lineno)
    new_unseeded = {(rel, lno) for rel, nos in unseeded.items() for lno in nos
                    if (rel, lno) not in _RNG_UNSEEDED_ALLOW}
    assert not new_unseeded, (
        f"unseeded np.random.*() call(s) in the engine (OS entropy fallback "
        f"makes re-runs non-reproducible) -- thread the existing seed/rng "
        f"through like the other call sites, or consciously re-pin: "
        f"{new_unseeded}")
    for rel, lno in sorted(_RNG_UNSEEDED_ALLOW):
        assert lno in sites.get(rel, []), (
            f"allowlisted RNG site {rel}:{lno} no longer calls np.random.* "
            f"-- the fallback was repaired: REMOVE the allowlist entry")
    assert sum(len(v) for v in sites.values()) >= 15, (
        f"np.random.* sites nearly vanished from the engine "
        f"({sum(len(v) for v in sites.values())}) -- scan scope rotted?")


# --------------------------------------------------------------------------
# 10. JSON artifacts are strict: no NaN/Infinity, no duplicate keys
# --------------------------------------------------------------------------
def test_json_artifacts_strict():
    tracked = subprocess.run(
        ["git", "ls-files", "*.json"], cwd=REPO,
        capture_output=True, text=True, check=False, timeout=120)
    files = [REPO / f for f in tracked.stdout.splitlines() if f.strip()]
    assert files, "git ls-files *.json returned nothing (scope rotted?)"
    dup_bad, nonfinite_bad = [], []

    for p in files:
        rel = _rel(p)

        def _hook(pairs, _rel=rel):
            keys = [k for k, _ in pairs]
            if len(keys) != len(set(keys)):
                dup_bad.append(_rel)
            return dict(pairs)

        def _const(name, _rel=rel):
            nonfinite_bad.append(f"{_rel}: {name}")
            return name  # parse succeeds; the violation list does the work

        try:
            json.loads(p.read_text(encoding="utf-8"),
                       object_pairs_hook=_hook, parse_constant=_const)
        except json.JSONDecodeError as exc:
            pytest.fail(f"{rel}: not valid UTF-8 JSON: {exc}")
    assert not nonfinite_bad, (
        f"JSON artifacts containing NaN/Infinity (stdlib json accepts them, "
        f"strict parsers and jq do not): {nonfinite_bad}")
    assert not dup_bad, (
        f"JSON artifacts with duplicate keys (loaders disagree on which "
        f"value wins): {dup_bad}")


# --------------------------------------------------------------------------
# 11. RTL: posedge blocks -- exact count pin + non-blocking style only
# --------------------------------------------------------------------------
RTL_DIR = REPO / "fpga" / "rtl"

# (file, exact number of ``always @(posedge ...)`` blocks) at gate-creation
# state: 22 clocked blocks across fpga/rtl/ -- a new block MUST be conscious.
RTL_POSEDGE_BLOCKS = {
    "adc_reader.v": 4,
    "adf4351_ctrl.v": 1,
    "command_parser.v": 2,
    "dac_ctrl.v": 1,
    "emitter_array.v": 1,
    "qrng_entropy.v": 1,
    "spi_master.v": 1,
    "tlc5947_ctrl.v": 3,
    "top_level.v": 5,
    "uart_rx.v": 2,
    "uart_tx.v": 1,
}


def _strip_for_headers(line: str) -> str:
    """Remove Verilog ``for (...)`` headers (balanced parens).

    A synthesis for-loop's header legitimately contains blocking-style
    assignments (``for (i = 0; i < N; i = i + 1)``) -- those are NOT body
    statements, and the ``;``-split of the header leaks segments like
    ``i = i + 1)`` that must not be judged as body blocking assignments.
    """
    while True:
        m = re.search(r"\bfor\s*\(", line)
        if not m:
            return line
        depth = 0
        for k in range(m.end() - 1, len(line)):
            if line[k] == "(":
                depth += 1
            elif line[k] == ")":
                depth -= 1
                if depth == 0:
                    line = line[: m.start()] + line[k + 1:]
                    break
        else:
            return line          # unbalanced header -- leave the line alone


def _rtl_posedge_problems(src: str) -> list:
    """Blocking assignment (``=``) inside a clocked block is the classic
    synthesised-race bug; every ``always @(posedge ...)`` block's body may
    only use non-blocking assignment (``<=``). Line comments stripped,
    ``for (...)`` headers removed first (their loop increments are header
    assignments, not body statements), begin/end matched with word
    boundaries."""
    problems = []
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if not (re.search(r"\balways\s*@\s*\(", line) and "posedge" in line):
            continue
        depth = 0
        started = False
        stmts: list = []
        for j in range(i, min(len(lines), i + 400)):
            l2 = _strip_for_headers(re.sub(r"//.*", "", lines[j]))
            depth += len(re.findall(r"\bbegin\b", l2)) - \
                len(re.findall(r"\bend\b", l2))
            if "begin" in l2:
                started = True
            if started:
                stmts.extend(l2.split(";"))
                if depth <= 0:
                    break
            elif j > i:      # single-statement block without begin
                stmts.extend(l2.split(";"))
                break
        for stmt in stmts:
            stmt = stmt.strip()
            if not stmt or stmt.startswith(("begin", "end", "if", "else",
                                            "case", "always", "assign",
                                            "for", "release")):
                continue
            # a lone '=' (not part of <=, >=, ==, !=, <<=/>>=) in a
            # clocked-block statement is a blocking assignment
            for k, ch in enumerate(stmt):
                if ch != "=":
                    continue
                if k and stmt[k - 1] in "<>!=":
                    continue           # <=, >=, ==, !=, shift-assign
                if k + 1 < len(stmt) and stmt[k + 1] in "=<>":
                    continue           # ==, =< (typos), <=
                problems.append((i + 1, stmt[:60]))
                break
    return problems


@pytest.mark.parametrize("fname,expected_blocks",
                         sorted(RTL_POSEDGE_BLOCKS.items()))
def test_rtl_posedge_gates(fname: str, expected_blocks: int):
    src = (RTL_DIR / fname).read_text(encoding="utf-8")
    n = len(re.findall(r"\balways\s*@\s*\(.*posedge", src))
    assert n == expected_blocks, (
        f"{fname}: {n} posedge blocks (pinned {expected_blocks}) -- a new "
        f"clocked block must adopt the non-blocking style and be "
        f"consciously re-pinned here")
    problems = _rtl_posedge_problems(src)
    assert not problems, (
        f"{fname}: blocking assignment (=) inside always @(posedge ...) "
        f"block(s): {problems[:10]}")


def test_rtl_posedge_total():
    assert sum(RTL_POSEDGE_BLOCKS.values()) == 22