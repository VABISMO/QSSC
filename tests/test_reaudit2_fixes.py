# tests/test_reaudit2_fixes.py
"""Regression tests for the re-audit-2 MED fixes (#145-#150).

#145 -- the runner real-hardware calibration path clamps RUN_ALL_ODMR n_points
to the RTL CMD_ALL_SWEEP cap of 256 (the API model default is 400, which the
RTL rejects with ERR -> host IOError on the default real-hw sweep).

#146 -- FPGAConnection.read_lockin n_cycles bound matches the RTL CMD_LOCKIN
cap of 10000 (host previously allowed 100000).

#147 -- FPGAConnection.run_all_odmr acquires the per-instance _io_lock across
the whole write + n_points readlines + OK readline (the #131 lock was only on
send_command; run_all_odmr is the longest serial exchange and bypassed it).

#148 -- frontend loadFromHistory staleness: no JS test harness (vitest/jest
absent from frontend/package.json), so #148 is source + live verified only
(the pre-fetch staleness guard in App.jsx runCircuit's poll). Not unit-tested.

#149 -- gyro_measure returns |rate|: a single-axis Ramsey fringe cos(Ot) is
EVEN in O, the sign is unrecoverable from one trace; the FFT fit path already
returns |rate|, so the sim path must agree.

#150 -- SimulatedFPGA._odmr_sweep sums the within-pixel register line
Gaussians (mirrors _run_all_odmr's _pixel_dip_frac) instead of a single
Gaussian at D, so ODMR_SWEEP <qubit> shows the same register spectrum as
RUN_ALL_ODMR for that pixel when the gradient is on.
"""
from __future__ import annotations

import threading

import numpy as np


# ---------------------------------------------------------------------------
# Shared fake serial for the FPGAConnection tests (#146, #147)
# ---------------------------------------------------------------------------

class _FakeSerial:
    """pyserial stand-in: handles ID, RUN_ALL_ODMR (rows + OK), and a default
    4-int reply for READ_LOCKIN. Thread-safe enough for the lock tests."""

    def __init__(self, port, baudrate, timeout=5):
        self._last = "ID"

    def write(self, data):
        self._last = data.decode().strip()

    def readline(self):
        cmd = self._last
        if cmd == "ID":
            return b"QSSC v1\n"
        if cmd.startswith("RUN_ALL_ODMR"):
            # n_points rows of 64 zero-codes, then OK. n is the 4th token.
            # Distinguish the row reads from the OK read by a call counter:
            # the first n_points readlines are rows, the next is OK.
            try:
                n = int(cmd.split()[3])
            except (IndexError, ValueError):
                n = 1
            if not hasattr(self, "_row_i"):
                self._row_i = 0
            self._row_i += 1
            if self._row_i <= n:
                return (" ".join(["0"] * 64) + "\n").encode()
            self._row_i = 0
            return b"OK\n"
        # default (READ_LOCKIN / READ_LOCKINP / SET_GRADIENT / etc.)
        return b"0 0 1 1\n"

    def close(self):
        pass


def _make_fpga_conn(monkeypatch):
    """Build a FPGAConnection with a fake serial + skipped 2s reset sleep."""
    from qssc import backend_fpga
    import serial as _serial_mod
    monkeypatch.setattr(_serial_mod, "Serial", _FakeSerial)
    monkeypatch.setattr(backend_fpga.time, "sleep", lambda *_: None)
    return backend_fpga.FPGAConnection(port="/dev/fakeUSB")


# ---------------------------------------------------------------------------
# #145: runner clamps RUN_ALL_ODMR n to the RTL cap of 256
# ---------------------------------------------------------------------------

def test_run_calibrate_array_clamps_n_points_to_256(monkeypatch):
    """#145: source=real with n_points=400 (the API default) must call
    run_all_odmr with n<=256 (the RTL CMD_ALL_SWEEP cap), not 400."""
    from api import runner

    class _FakeFpga:
        def __init__(self):
            self.last_n = None
            self.emitter_gs = {}
        def send_command(self, cmd):
            return "OK"
        def run_all_odmr(self, f_start, f_stop, n):
            self.last_n = int(n)
            return np.zeros((int(n), 64), dtype=np.int32)
        def close(self):
            pass

    fake = _FakeFpga()
    monkeypatch.setattr(runner, "_make_fpga", lambda *a, **k: fake)
    req = {"source": "real", "profile": "NV", "temperature_k": 300.0,
           "seed": 42, "n_points": 400, "gradient_mhz_per_um": 0.5,
           "pixel_size_um": 200.0}
    # The post-sweep calibration processing may raise on a zero matrix; we only
    # need to confirm run_all_odmr was called with the clamped n.
    try:
        runner.run_calibrate_array(req)
    except Exception:
        pass
    assert fake.last_n is not None, "run_all_odmr was never called"
    assert fake.last_n <= 256, f"n not clamped to RTL cap: {fake.last_n}"
    assert fake.last_n == 256, f"400 should clamp to 256, got {fake.last_n}"


# ---------------------------------------------------------------------------
# #146: read_lockin n_cycles bound matches the RTL cap of 10000
# ---------------------------------------------------------------------------

def test_read_lockin_rejects_n_cycles_above_10000(monkeypatch):
    """#146: FPGAConnection.read_lockin must reject n_cycles > 10000 (the RTL
    CMD_LOCKIN cap), not the prior 100000."""
    conn = _make_fpga_conn(monkeypatch)
    # at the cap -> accepted (the fake returns a 4-int reply)
    out = conn.read_lockin(0, 10000)
    assert set(out.keys()) >= {"differential_code", "integrated_snr"}
    # one above the cap -> ValueError before any serial I/O
    import pytest
    with pytest.raises(ValueError):
        conn.read_lockin(0, 10001)
    conn.close()


# ---------------------------------------------------------------------------
# #147: run_all_odmr acquires the per-instance _io_lock
# ---------------------------------------------------------------------------

def test_run_all_odmr_acquires_io_lock(monkeypatch):
    """#147: if run_all_odmr acquires _io_lock, calling it while the lock is
    held by the main thread BLOCKS until the lock is released. A thread that
    does not acquire the lock would complete immediately."""
    conn = _make_fpga_conn(monkeypatch)
    done = threading.Event()

    def worker():
        try:
            conn.run_all_odmr(2_870_000, 2_880_000, 4)
            done.set()
        except Exception:
            pass

    # Hold the lock: a run_all_odmr call in another thread must NOT finish.
    with conn._io_lock:
        t = threading.Thread(target=worker)
        t.start()
        done.wait(timeout=0.3)
        assert not done.is_set(), "run_all_odmr completed without the lock -> #147 regressed"
    # Release the lock: the worker can now proceed.
    t.join(timeout=2.0)
    assert done.is_set(), "run_all_odmr did not complete after the lock was released"
    conn.close()


# ---------------------------------------------------------------------------
# #149: gyro_measure returns |rate| (sign unrecoverable from one Ramsey trace)
# ---------------------------------------------------------------------------

def test_gyro_measure_returns_abs_rate():
    """#149: a negative rotation rate must measure in the +|rate| basin, NOT the
    -rate basin. The single-axis Ramsey fringe cos(Ot) is even in Omega, so the
    sign is unrecoverable from one trace; the FFT fit path already returns
    |rate| and the sim path must agree.

    Use a fast rate (1000 deg/s) + short free-precession (100 us, well inside
    T2) + heavy averaging so the rate noise (~168 deg/s) is << signal and a
    single sample unambiguously identifies the basin. With the same RNG seed
    both +/- inputs draw the SAME noise, so the two measured rates are equal
    and both positive; without the |rate| fix the negative input would read
    ~-1000."""
    from qssc.sensors import gyro_measure
    common = dict(n_averages=10000, free_precession_us=100.0)
    rng = np.random.default_rng(0)
    neg = gyro_measure(rotation_rate_dps=-1000.0, rng=rng, **common)
    rng = np.random.default_rng(0)
    pos = gyro_measure(rotation_rate_dps=1000.0, rng=rng, **common)
    # the measurement lands in the +|rate| basin (>>0), not the -rate basin
    assert neg.measured_rate_dps > 0.0, \
        f"negative input measured in the -rate basin: {neg.measured_rate_dps} (#149 regressed)"
    assert neg.measured_rate_dps > 500.0, \
        f"|rate| measurement too small for +1000 deg/s input: {neg.measured_rate_dps}"
    # same noise draw -> both inputs report the SAME measured rate (both +|rate|)
    assert abs(neg.measured_rate_dps - pos.measured_rate_dps) < 1.0, \
        f"+/- inputs disagree despite same seed: {neg.measured_rate_dps} vs {pos.measured_rate_dps}"
    # the underlying truth field keeps the signed input (only the MEASUREMENT is |rate|)
    assert neg.rotation_rate_dps == -1000.0
    assert pos.rotation_rate_dps == 1000.0


# ---------------------------------------------------------------------------
# #150: _odmr_sweep sums the within-pixel register lines (gradient on)
# ---------------------------------------------------------------------------

def test_odmr_sweep_shows_register_lines_with_gradient_on():
    """#150: with gradient on, _odmr_sweep must spread the dip across the
    within-pixel register (many lines across ~gradient*pixel MHz), NOT collapse
    to a single ~linewidth dip at D. With gradient off it is one dip. Compare
    the span of the dipped region at a PEAK-RELATIVE threshold (30% of the
    deepest dip) so the comparison is independent of contrast and linewidth:
    gradient-on spans ~the full register spread, gradient-off spans ~one
    linewidth."""
    from qssc.simulator import SimulatedFPGA, BRIGHT_CODE

    def dipped_span_mhz(gradient_mhz_per_um, integration_shots=200):
        fpga = SimulatedFPGA("NV", seed=5,
                             gradient_mhz_per_um=gradient_mhz_per_um,
                             pixel_size_um=200.0,
                             integration_shots=integration_shots)
        # brighten the swept channel (as SET_EMITTER would): off-resonance the
        # code is BRIGHT, on-resonance it DIPS by contrast -- without this the
        # channel reads flat BG_CODE and there is no dip to measure.
        fpga.emitter_gs[0] = 1.0
        fpga._draw_pixel_lines()
        n_lines = len(fpga._pixel_line_freqs[0])
        D = fpga.sim_freq
        spread = fpga.gradient_mhz_per_um * 1e6 * fpga.pixel_size_um
        half = max(spread, 5e6) * 1.2 + 10e6   # cover the spread + margin
        f_start = int((D - half) / 1e3)
        f_stop = int((D + half) / 1e3)
        n = 1200
        toks = fpga._odmr_sweep(0, f_start, f_stop, n).split()
        n_ret = int(toks[0])
        codes = np.array([int(toks[1 + 2 * i + 1]) for i in range(n_ret)])
        freqs_khz = np.array([int(toks[1 + 2 * i]) for i in range(n_ret)])
        step_mhz = (freqs_khz[-1] - freqs_khz[0]) / max(n_ret - 1, 1) / 1e3
        # peak-relative threshold: 30% of the deepest dip -> independent of
        # contrast magnitude and robust to the integration-shots noise floor.
        peak_dip = float(BRIGHT_CODE - codes.min())
        assert peak_dip > 0.0, "no dip detected at all"
        thresh = 0.30 * peak_dip
        dipped = int(np.sum(codes < (BRIGHT_CODE - thresh)))
        return dipped * step_mhz, n_lines

    span_grad, n_lines_grad = dipped_span_mhz(1.0)
    span_off, n_lines_off = dipped_span_mhz(0.0)
    # gradient on -> multiple register lines, dip spans the ~200 MHz spread
    assert n_lines_grad >= 2, f"gradient on should yield >=2 lines, got {n_lines_grad}"
    assert span_grad > 50.0, f"gradient-on dip span too narrow ({span_grad:.1f} MHz) -> single-Gaussian regression (#150)"
    # gradient off -> one ensemble line, dip spans ~one linewidth
    assert n_lines_off == 1
    assert span_off < 15.0, f"gradient-off dip span too wide ({span_off:.1f} MHz)"
    # the on/off contrast is the core assertion: gradient spreads the dip >> off
    assert span_grad > 4.0 * span_off