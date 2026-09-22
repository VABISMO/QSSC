# qssc/backend_fpga.py
"""Real pyserial link to the Artix-7 board + the hardware-absent fallback.

:class:`FPGAConnection` wraps a single UART port and implements the integer
wire protocol the ``fpga/rtl/command_parser.v`` firmware parses (``ID`` /
``APPLY_PULSE`` / ``READ_DETECTORS`` / ``RUN_SEQUENCE`` / ``READ_LOCKIN`` /
``RUN_ALL_ODMR`` …). It is the real-hardware counterpart to
:class:`~qssc.simulator.SimulatedFPGA`; the controllers in
:mod:`qssc.backend_controllers` and the fits in :mod:`qssc.backend_fits` drive
either through one call site.

:func:`make_connection` returns a :class:`SimulatedFPGA` when no port is given
(the local / mock path) or a :class:`FPGAConnection` on a real port.
:class:`NoFPGADetected` is raised whenever a port is specified but no flashed
QSSC board answers the ``ID`` handshake — so the runner/API surfaces a clear
"no FPGA detected" result instead of silently simulating.
"""
from __future__ import annotations

import math
import time
from typing import Optional

import numpy as np

from .simulator import SimulatedFPGA, N_SITES, READOUT_NOISE
from .backend_fits import _seq_kind_num, _fit_gyro_from_codes


class NoFPGADetected(RuntimeError):
    """Raised when hardware-real mode is requested but no live FPGA answers.

    The board-present test is the ``ID`` firmware verb: a flashed QSSC board
    replies ``QSSC v1``. An empty/unprogrammed port, a wrong device, or a
    ``readline`` timeout all surface as this exception so the runner/API can
    return a clear "no FPGA detected" result instead of silently simulating.
    """

    def __init__(self, port: str, reason: str = ""):
        self.port = port
        self.reason = reason
        msg = f"no FPGA detected on {port}"
        if reason:
            msg += f" — {reason}"
        super().__init__(msg)


# Identity string a flashed QSSC board returns on the ``ID`` verb. The host
# board-present probe (FPGAConnection.__init__) requires a reply that starts
# with this token. Implemented in fpga/rtl/command_parser.v.
FPGA_ID_TOKEN = "QSSC"


class FPGAConnection:
    """Thin wrapper over a pyserial link to the Artix-7 board.

    pyserial is imported lazily so the package works without it installed.
    """

    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 115200):
        import serial  # local import
        try:
            self.ser = serial.Serial(port, baudrate, timeout=5)
        except Exception as exc:  # port absent / permission denied / busy
            raise NoFPGADetected(port, f"cannot open serial port: {exc}") from exc
        time.sleep(2)
        # Board-present handshake: a flashed QSSC board answers ``ID`` with
        # ``QSSC v1``. An unprogrammed FPGA, a wrong device, or a silent port
        # must surface as NoFPGADetected here so the caller never mistakes a
        # dead link for a working board and silently falls back to simulation.
        try:
            reply = self.send_command("ID")
        except Exception as exc:
            self.ser.close()
            raise NoFPGADetected(port, f"ID probe failed: {exc}") from exc
        if not reply.startswith(FPGA_ID_TOKEN):
            self.ser.close()
            raise NoFPGADetected(
                port, f"ID reply {reply!r} did not start with {FPGA_ID_TOKEN!r}")

    def send_command(self, command: str) -> str:
        self.ser.write((command + "\n").encode())
        return self.ser.readline().decode().strip()

    def close(self):
        self.ser.close()

    # ---- pulse-sequence engine + lock-in host methods ---------------
    # The RTL (fpga/rtl/command_parser.v) returns RAW 16-bit ADC codes
    # / raw on-off accumulations; the host does every physics fit (rate, T2,
    # SNR). These three methods parse the real integer wire format and return
    # the SAME structured shapes as the SimulatedFPGA wrappers below, so the
    # /api endpoints are mode-agnostic (one call site, two backends).

    def run_sequence(self, site: int, kind, n_windows: int,
                     t_step_ns: int = 0) -> np.ndarray:
        """``RUN_SEQUENCE <site> <kind> <n> <t_step>`` -> n raw ADC readout codes.

        kind: 0=Ramsey, 1=Hahn, 2=CPMG (int or case-insensitive name). The
        firmware replies ``"<n> c0 c1 ... cN-1"`` (dec-padded, NO ``OK``). The
        host fits T2 / rate on the returned codes. Returns an ``int32`` array
        of length ``n_windows``.
        """
        kind_num = _seq_kind_num(kind)
        if not (0 <= int(site) < N_SITES):
            raise ValueError("site out of range 0..63")
        if not (1 <= int(n_windows) <= 1024):
            raise ValueError("n_windows out of range 1..1024")
        reply = self.send_command(
            f"RUN_SEQUENCE {int(site)} {kind_num} {int(n_windows)} {int(t_step_ns)}")
        toks = reply.split()
        if len(toks) < 1 or int(toks[0]) != int(n_windows):
            raise IOError(f"RUN_SEQUENCE reply mismatch: {reply!r}")
        return np.array([int(t) for t in toks[1:1 + int(n_windows)]], dtype=np.int32)

    def read_lockin(self, site: int, n_cycles: int) -> dict:
        """``READ_LOCKIN <site> <n>`` -> on/off accumulations -> lock-in result.

        The firmware replies ``"<sum_on> <sum_off> <n_on> <n_off>"`` (NO
        ``OK``): ``sum_on`` is the MW-on (ODMR dip, dark) accumulation,
        ``sum_off`` the MW-off (bright) accumulation. The RTL does no
        division; the host computes the bright-minus-dark differential and
        SNR. Returns the same dict shape as
        :func:`qssc.sensors.lockin_integrated_readout`.
        """
        if not (0 <= int(site) < N_SITES) or not (1 <= int(n_cycles) <= 100000):
            raise ValueError("site/n_cycles out of range")
        reply = self.send_command(f"READ_LOCKIN {int(site)} {int(n_cycles)}")
        return self._parse_lockin_reply(reply)

    def read_lockinp(self, site: int, n_cycles: int, half_ns: int) -> dict:
        """``READ_LOCKINP <site> <n> <half_ns>`` -> prime-period lock-in result.

        Same accumulator + reply shape as :meth:`read_lockin`, but the on/off
        half-period is the host-supplied ``half_ns`` (= ``p * tau / 2`` for the
        prime ``p`` that :mod:`qssc.prime_readout` assigned to this NV channel),
        instead of the fixed 200 us default. The prime-coprime orthogonality
        (zero cross-talk over the LCM) lives in the Python readout model; the
        RTL just demodulates at the requested half-period. Returns the same
        dict shape as :meth:`read_lockin`.
        """
        if not (0 <= int(site) < N_SITES) or not (1 <= int(n_cycles) <= 100000):
            raise ValueError("site/n_cycles out of range")
        if int(half_ns) < 2000:
            raise ValueError("half_ns must be >= 2000 (>= 20 clk cycles)")
        reply = self.send_command(
            f"READ_LOCKINP {int(site)} {int(n_cycles)} {int(half_ns)}")
        return self._parse_lockin_reply(reply)

    def _parse_lockin_reply(self, reply: str) -> dict:
        """Parse the four-int lock-in reply (shared by READ_LOCKIN/READ_LOCKINP)."""
        toks = reply.split()
        if len(toks) != 4:
            raise IOError(f"READ_LOCKIN(P) reply mismatch: {reply!r}")
        sum_on, sum_off, n_on, n_off = (int(t) for t in toks)
        n_on = max(1, n_on)
        n_off = max(1, n_off)
        mean_on = sum_on / n_on            # MW on  -> dark (ODMR dip)
        mean_off = sum_off / n_off         # MW off -> bright
        diff = mean_off - mean_on          # bright - dark (matches the sim path)
        # The 4 accumulation ints carry no per-frame variance, so use the known
        # single-frame readout noise floor as sigma (the same assumption
        # lockin_integrated_readout makes); the integrated sigma shrinks as
        # sqrt(N). This is the lock-in SNR gain, not a measured variance.
        single_sigma = float(READOUT_NOISE)
        n_eff = max(n_on, n_off)
        sigma = single_sigma / math.sqrt(n_eff) if n_eff else single_sigma
        return {
            "n_frames": n_on + n_off,
            "differential_code": round(diff, 2),
            "sigma_code": round(sigma, 2),
            "single_frame_snr": round(abs(diff) / single_sigma, 2) if single_sigma else 0.0,
            "integrated_snr": round(abs(diff) / sigma, 2) if sigma else 0.0,
            "snr_gain": round(math.sqrt(n_eff), 2),
            "bright_code": round(mean_off, 2),
            "dark_code": round(mean_on, 2),
        }

    def run_all_odmr(self, f_start_khz: int, f_stop_khz: int,
                     n_points: int) -> np.ndarray:
        """``RUN_ALL_ODMR <f_start> <f_stop> <n>`` -> ``(n, 64)`` ADC code matrix.

        The firmware sweeps the ADF4351 across [f_start, f_stop] in ``n`` steps
        ON-CHIP (one command, not n serial round-trips); at each step it gates
        the MW for the ODMR window and reads all 64 AD7616 channels
        simultaneously, emitting one line of 64 space-separated 16-bit codes per
        frequency point, then a final ``OK``. Row ``i`` is the 64-pixel
        photocurrent at freq point ``i``. The host runs
        :func:`qssc.odmr_decompose.decompose_odmr` per column (pixel) to count
        each pixel's resolved ODMR lines = its calibrated register width.
        Returns an ``int32`` array of shape ``(n_points, 64)``.
        """
        if n_points <= 0 or n_points > 4096:
            raise ValueError("n_points out of range 1..4096")
        cmd = f"RUN_ALL_ODMR {int(f_start_khz)} {int(f_stop_khz)} {int(n_points)}\n"
        self.ser.write(cmd.encode())
        rows = []
        for _ in range(n_points):
            line = self.ser.readline().decode().strip()
            toks = line.split()
            if len(toks) != N_SITES:
                raise IOError(f"RUN_ALL_ODMR row mismatch: {line!r}")
            rows.append([int(t) for t in toks])
        ok = self.ser.readline().decode().strip()
        if ok != "OK":
            raise IOError(f"RUN_ALL_ODMR missing OK: {ok!r}")
        return np.array(rows, dtype=np.int32)

    def read_gyro(self, site: int, t_ns: int, n_windows: int = 64) -> dict:
        """Ramsey pi/2-T-pi/2 -> rotation rate (dps) from real ADC codes.

        There is no ``READ_GYRO`` RTL verb: the host runs ``RUN_SEQUENCE``
        (Ramsey) and fits the fringe rate on the returned codes
        (:func:`qssc.backend_fits._fit_gyro_from_codes`). Returns the same dict
        shape as :func:`qssc.sensors.gyro_measure` (a GyroResult asdict).
        """
        if not (0 <= int(site) < N_SITES) or t_ns <= 0 or n_windows < 4:
            raise ValueError("site/t_ns/n_windows out of range")
        t_step = max(1, int(t_ns) // int(n_windows))
        codes = self.run_sequence(site, 0, n_windows, t_step)   # 0 = Ramsey
        return _fit_gyro_from_codes(codes, t_step, t_ns)


def make_connection(port: Optional[str] = None, *, profile="NV",
                    temperature_k: float = 300.0, seed: Optional[int] = None):
    """Return a real or simulated FPGA connection.

    ``port=None`` (or ``--mock``) yields a :class:`SimulatedFPGA`; otherwise a
    real :class:`FPGAConnection` is opened on ``port``.
    """
    if port is None:
        return SimulatedFPGA(profile=profile, temperature_k=temperature_k, seed=seed)
    return FPGAConnection(port)