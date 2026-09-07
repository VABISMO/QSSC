# tests/_pcb_env.py
"""Make KiCad's ``pcbnew`` binding importable for the PCB test track -- and fail
LOUDLY when it is not available.

The PCB tests are regression gates: a skipped gate is a hole in the net.
There is no ``importorskip`` anywhere in this suite.  ``ensure_pcbnew()``
resolves the binding in the three environments the repo actually runs in:

* system Python that ships the bindings on ``sys.path`` already (Arch, KiCad
  appimage on PATH),
* a venv over the system interpreter -- the binding is ABI-tied to the exact
  CPython version it was built for, so this only works when the venv's
  version matches (e.g. python3.14 venv on a python3.14 KiCad install);
* any install layout, found by globbing the usual prefix candidates.

When nothing importable turns up the caller gets an ``ImportError`` naming the
fix -- the test FAILS, it does not vanish.
"""
from __future__ import annotations

import glob
import os
import sys

# Candidate directories that contain pcbnew.py across the layouts we know:
#   Arch pacman        /usr/lib/python3.X/site-packages
#   Debian/Ubuntu apt  /usr/lib/python3/dist-packages  (kicad package)
#   KiCad appimage/ppa /usr/lib/kicad*/... or /opt/kicad*/...
_GLOBS = [
    "/usr/lib/python3*/dist-packages",
    "/usr/lib/python3*/site-packages",
    "/usr/lib/kicad*/lib/python3*/dist-packages",
    "/usr/lib/kicad*/share/kicad/python",
    "/opt/kicad*/lib/python3*/dist-packages",
    "/usr/local/lib/python3*/dist-packages",
]

_IMPORTED: list[str] | None = None
_PCBNEW_DIR: str | None = None


def pcbnew_dir() -> str:
    """Directory the ``pcbnew`` binding was imported from (after
    ``ensure_pcbnew()``). Ingredient for subprocess ``PYTHONPATH`` -- see
    ``child_env``."""
    global _PCBNEW_DIR
    if _PCBNEW_DIR is None:
        import pcbnew
        _PCBNEW_DIR = os.path.dirname(os.path.abspath(pcbnew.__file__))
    return _PCBNEW_DIR


def child_env(**overrides: str) -> dict[str, str]:
    """A subprocess environment that can ``import pcbnew``.

    ``ensure_pcbnew()`` extends THIS interpreter's ``sys.path`` when the
    binding lives outside its default search path (a venv over a system KiCad
    install) -- but a ``subprocess`` child starts a fresh interpreter and does
    NOT inherit that edit. Tests that run ``tools/*.py`` scripts pass
    ``child_env(...)`` instead of a bare ``{**os.environ, ...}``; keyword
    arguments are applied on top (pass ``PYTHONPATH`` to add script dirs -- it
    is kept, prepended by the binding directory)."""
    env = {**os.environ, **overrides}
    parts = [env.get("PYTHONPATH", ""), pcbnew_dir()]
    env["PYTHONPATH"] = os.pathsep.join(p for p in parts if p)
    return env


def ensure_pcbnew():
    """Import ``pcbnew`` (extending ``sys.path`` if needed) and apply the repo's
    swig-iterator/teardown compat patches from ``tools/_kicad_compat.py``.
    Returns the module. Raises ``ImportError`` with actionable text if KiCad's
    python binding is not installed for this interpreter's ABI version."""
    global _IMPORTED
    if _IMPORTED is not None:
        return _IMPORTED[0]

    try:
        import pcbnew  # noqa: F401
    except ModuleNotFoundError:
        for pattern in _GLOBS:
            for cand in sorted(glob.glob(pattern), reverse=True):
                if os.path.exists(os.path.join(cand, "pcbnew.py")):
                    if cand not in sys.path:
                        sys.path.append(cand)
                    break
        try:
            import pcbnew  # noqa: F401
        except ModuleNotFoundError as e:
            raise ImportError(
                "pcbnew (KiCad python binding) is not importable by this "
                f"interpreter ({sys.version.split()[0]}). The PCB tests do not "
                "skip -- install KiCad (kicad-cli + bindings built for this "
                "python version), run them with the system python, or set "
                "PYTHONPATH to the directory containing pcbnew.py. "
                "See tests/_pcb_env.py docstring."
            ) from e

    # tools/_kicad_compat.py: aliases SwigPyIterator.next -> __next__ (KiCad
    # bindings iterate with py2-era .next under python>=3.10) and marks the
    # teardown path safe for in-process boards.
    tools = os.path.join(os.path.dirname(__file__), os.pardir, "tools")
    if tools not in sys.path:
        sys.path.insert(0, os.path.abspath(tools))
    from _kicad_compat import patch_swig_iterators

    patch_swig_iterators()
    import pcbnew

    _IMPORTED = [pcbnew]
    return pcbnew