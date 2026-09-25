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

# Set before any test runs (and before the app/client fixtures import the
# guards). ``setdefault`` leaves an explicit outer value untouched.
os.environ.setdefault("QSSC_TESTING", "1")