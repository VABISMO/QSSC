# tests/conftest.py
"""Shared pytest configuration for the QSSC test suite.

Sets ``QSSC_TESTING=1`` for the whole session so :func:`api._guards._is_local`
treats the Starlette ``TestClient`` connection peer (``"testclient"``) as local.
Several suites (``test_api``, ``test_parallel_computing``, ``test_qrng``,
``test_hardware_real``, ``test_pixel_mode``) exercise ``?sync=true`` circuit
runs and the localhost-only FPGA endpoints through ``TestClient``; without this
flag the testclient peer is NOT gated as local on a deploy, so sync would be
blocked with 403. Production never imports this file, so the flag does not leak
into a real deploy.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# Set before any test runs (and before the app/client fixtures import the
# guards). ``setdefault`` leaves an explicit outer value untouched.
os.environ.setdefault("QSSC_TESTING", "1")


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    """Clear the per-IP rate-limit buckets before each test.

    ``RATE_LIMIT_PER_MIN`` defaults to 20 and the rate-limit bucket for the
    ``testclient`` peer accumulates across the whole session (a sliding 60s
    window). Without a per-test reset, suites that fire many rate-limited
    requests (POST /api/circuit/run, /api/circuit/custom, ...) trip 429 for
    later tests -- e.g. ``test_mongo_persistence`` after the combined suite
    (audit #143). This isolates each test's rate-limit state. Production never
    imports this file, so the reset does not leak into a real deploy."""
    try:
        from api import main as _m
        _m._rate_buckets.clear()
    except Exception:  # noqa: BLE001
        pass
    yield
    try:
        from api import main as _m
        _m._rate_buckets.clear()
    except Exception:  # noqa: BLE001
        pass


def pytest_collection_modifyitems(config, items):
    """Skip tests parametrised for the local-only gradient board when absent.

    ``hardware/pcb/qssc-grad.*`` is work in progress and is NOT published in
    this repository (only qssc + chip_module are). Clones without the file
    skip these tests; machines that have it run them as usual. Param-less
    grad-reading tests carry an explicit ``_need_grad()`` guard in their own
    module.
    """
    grad = (Path(__file__).resolve().parent.parent
            / "hardware" / "pcb" / "qssc-grad.kicad_pcb")
    if grad.exists():
        return
    skip = pytest.mark.skip(
        reason="qssc-grad board is local-only (not published in this repo)")

    def mentions_grad(v):
        if isinstance(v, str):
            return v == "grad" or "qssc-grad" in v
        if isinstance(v, (list, tuple, set, frozenset)):
            return any(mentions_grad(x) for x in v)
        return False

    for item in items:
        cs = getattr(item, "callspec", None)
        if cs and any(mentions_grad(v) for v in cs.params.values()):
            item.add_marker(skip)
