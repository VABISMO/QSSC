"""pcbnew version compatibility for the routing tools.

The pcbnew python bindings differ meaningfully across KiCad releases. Two
breakages hit this repo's tools on KiCad 10.x (verified on 10.0.6, Arch
package, python 3.14):

1. ``for t in board.Tracks()`` raises
   ``AttributeError: 'SwigPyIterator' object has no attribute 'next'`` --
   the generated binding's ``__iter__`` calls ``it.next()``, which newer
   swig runtimes no longer provide (only ``__next__``). Fixed by aliasing
   ``next`` to ``__next__`` on ``SwigPyIterator`` before any iteration.

2. ``PCB_VIA::GetWidth()`` without a layer argument is deprecated: debug
   builds assert ``PCB_VIA::GetWidth called without a layer argument`` and
   the call can take the process down later (SIGSEGV under 10.0.6). Stacked
   microvias may carry a different copper diameter per layer, so the
   binding wants ``GetWidth(layer)``. Older bindings only know the
   zero-arg form, so the layer-argument call is tried first and the
   zero-arg form kept as a ``TypeError`` fallback.

Import this module in every tool that touches pcbnew containers:

    import pcbnew
    from _kicad_compat import patch_swig_iterators, track_width_nm
    patch_swig_iterators()

``patch_swig_iterators()`` is idempotent and a no-op wherever ``next``
already exists, so the tools keep working on the older KiCad they were
written against.
"""

import pcbnew


def patch_swig_iterators():
    """Alias ``SwigPyIterator.next`` to ``__next__`` where the swig runtime
    dropped it (KiCad 10 bindings iterate via ``it.next()``). Safe on older
    runtimes: the alias is only installed when ``next`` is missing."""
    iterator = getattr(pcbnew, "SwigPyIterator", None)
    if iterator is not None and not hasattr(iterator, "next"):
        if hasattr(iterator, "__next__"):
            iterator.next = iterator.__next__


def track_width_nm(t):
    """Copper width (nm) of a PCB_TRACK or PCB_VIA, across pcbnew bindings.

    For tracks this is plain ``GetWidth()``. For vias KiCad >= 10 wants the
    layer argument (see module docstring); ``TopLayer()`` is the
    representative diameter for the through/blind vias these tools probe.
    """
    if isinstance(t, pcbnew.PCB_VIA):
        try:
            return t.GetWidth(t.TopLayer())
        except TypeError:  # pre-10 bindings: no layer argument
            return t.GetWidth()
    return t.GetWidth()


# Via type for a via that does not span the full stack. KiCad <= 9 bindings
# expose one ``VIATYPE_BLIND_BURIED`` enum value; KiCad >= 10 split it into
# separate ``VIATYPE_BLIND`` and ``VIATYPE_BURIED``. The blind vias these
# tools place all start on an outer layer, so BLIND is the correct half of
# the split on the newer bindings.
# Use a two-step lookup so the fallback is never evaluated when the primary
# name exists (getattr evaluates its default arg eagerly -- the bare
# pcbnew.VIATYPE_BLIND below used to crash on KiCad <= 9 even though
# VIATYPE_BLIND_BURIED is present).
if hasattr(pcbnew, "VIATYPE_BLIND_BURIED"):
    VIATYPE_BLIND_BURIED = pcbnew.VIATYPE_BLIND_BURIED
elif hasattr(pcbnew, "VIATYPE_BLIND"):
    VIATYPE_BLIND_BURIED = pcbnew.VIATYPE_BLIND
else:  # no blind/buried enum at all -- fall back to THROUGH so import never fails
    VIATYPE_BLIND_BURIED = pcbnew.VIATYPE_THROUGH


def exit_cleanly(code=0):
    """Terminate the process without running the interpreter's final GC.

    After a heavily-mutated board is saved, teardown is unsafe on KiCad
    10.0.6: the final garbage collection walks the thousands of stale swig
    proxies and segfaults ("Garbage-collecting" under Py_RunMain, observed
    on ~40% of runs). All work is already committed to disk by the time the
    caller invokes this -- flush the streams so nothing buffered is lost,
    then skip interpreter finalization entirely with ``os._exit``. On
    bindings without the crash it is equivalent to a normal ``sys.exit``.
    """
    import os
    import sys as _sys
    for stream in (_sys.stdout, _sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(code)


def fill_all_zones(board):
    """Fill every zone currently on ``board``; returns the filled zones.

    Only safe on a FRESHLY LOADED board. On KiCad 10.x, calling
    ``ZONE_FILLER.Fill`` in the same process that created hundreds of tracks/
    vias via ``board.Add()`` segfaults inside the C++ filler (verified on
    10.0.6), and the swig proxy layer stops handing out usable zone proxies
    after those mutations. Reloading the board in a new process and filling
    there works on every binding, so the routing tools split the fill out
    into a ``--fill`` subprocess instead of doing it inline.
    """
    zones = [z for z in board.Zones()]
    vec = pcbnew.ZONES()
    for z in zones:
        vec.append(z)
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(vec, False, None)
    return zones