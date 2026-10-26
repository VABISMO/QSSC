"""Hardware-real mode: the genuine FPGA path is distinct from the simulator.

These tests pin the hardware-real contract:
  * ``hardware=True`` with no board (``QSSC_FPGA_PORT`` unset) raises
    :class:`NoFPGADetected` / returns a ``no-fpga-detected`` body — never a
    silent simulation.
  * ``hardware=False`` keeps today's ``SimulatedFPGA`` path unchanged.
  * The optical-readout path no longer pokes ``sim_states`` on a real
    connection (a real ``FPGAConnection`` has no such attribute).
  * The QRNG / circuit result ``source`` label is truthful.
"""
import pytest

import numpy as np

from qssc import (
    NoFPGADetected, FPGAConnection, SimulatedFPGA, make_connection,
    QSSCBackend, DetectorInterface, MicrowaveController,
)

# pyserial is an OPTIONAL runtime dep — only needed to talk to a real board
# (FPGAConnection imports it lazily inside __init__). CI has no board and no
# pyserial, so the four tests that exercise the real-connection path skip
# there; the six NoFPGADetected / simulator / label tests still run.
try:
    import serial  # noqa: F401
    _HAS_SERIAL = True
except ImportError:
    _HAS_SERIAL = False
requires_serial = pytest.mark.skipif(
    not _HAS_SERIAL, reason="pyserial not installed (no real-board path in CI)")


# --- helpers ---------------------------------------------------------------

class _FakeSerial:
    """Minimal pyserial stand-in: readable/writable, readline() returns ID."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.written = []
        self.timeout = 1.0

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def readline(self):
        if not self._replies:
            return b""
        line = self._replies.pop(0)
        return line if isinstance(line, bytes) else line.encode() + b"\n"

    def close(self):
        pass

    def reset_input_buffer(self):
        pass


def _make_fpga_with_replies(monkeypatch, replies):
    """Build a real FPGAConnection whose pyserial layer is a fake loopback."""
    import serial
    import qssc.backend_fpga as backend_fpga

    fake = _FakeSerial(replies)

    def _fake_serial(*a, **k):
        return fake

    monkeypatch.setattr(serial, "Serial", _fake_serial)
    # Skip the 2 s board-settle sleep so the test is fast. ``time`` and
    # ``FPGAConnection`` live in backend_fpga after the M3 split.
    monkeypatch.setattr(backend_fpga.time, "sleep", lambda *_a, **_k: None)
    # A non-empty port triggers the real-connection branch in make_connection.
    return make_connection(port="/dev/ttyFAKE0", profile="NV",
                           temperature_k=300.0, seed=42)


# --- tests -----------------------------------------------------------------

def test_hardware_no_port_raises(monkeypatch):
    """hardware=True + unset QSSC_FPGA_PORT -> NoFPGADetected, never a sim."""
    monkeypatch.delenv("QSSC_FPGA_PORT", raising=False)
    import api.runner as runner
    with pytest.raises(NoFPGADetected):
        runner._make_fpga("NV", 300.0, 42, hardware=True)


def test_simulator_mode_unchanged(monkeypatch):
    """hardware=False -> SimulatedFPGA (the existing path), no regression."""
    monkeypatch.delenv("QSSC_FPGA_PORT", raising=False)
    import api.runner as runner
    fpga = runner._make_fpga("NV", 300.0, 42, hardware=False)
    assert isinstance(fpga, SimulatedFPGA)
    assert runner._source_label(fpga) == "simulator"


@requires_serial
def test_real_connection_id_probe(monkeypatch):
    """A real connection answers the ID handshake with the QSSC token."""
    # The first readline() is the ID reply ("QSSC v1"); later reads feed the
    # detector / emitter verbs the optical-readout path uses.
    fpga = _make_fpga_with_replies(monkeypatch, ["QSSC v1"])
    assert isinstance(fpga, FPGAConnection)


@requires_serial
def test_real_connection_bad_id_raises(monkeypatch):
    """A port that doesn't answer with the QSSC token is not our board."""
    with pytest.raises(NoFPGADetected):
        _make_fpga_with_replies(monkeypatch, ["NOTQSSC something"])


@requires_serial
def test_optical_readout_real_path(monkeypatch):
    """_drive_optical_readout must not touch sim_states on a real connection.

    A real FPGAConnection has no ``sim_states`` dict; the readout must come
    from the real ADC codes returned by ``READ_DETECTORS`` instead. This feeds
    the readout loop 64 integer codes per call so it thresholds without
    crashing.
    """
    fpga = _make_fpga_with_replies(
        monkeypatch,
        ["QSSC v1"]                       # ID probe
        + ["OK"] * 64                     # SET_EMITTER per channel
        + ["OK", "OK"]                    # DRIVE_PATTERN, CLEAR_EMITTERS
        + [" ".join(str(30000) for _ in range(64))]  # READ_DETECTORS
        + ["OK"] * 64                     # CLEAR on the second pass
        + [" ".join(str(30000) for _ in range(64))],
    )
    assert not hasattr(fpga, "sim_states")  # real connection has no sim state

    backend = QSSCBackend(profile="NV", hardware=fpga, temperature_k=300.0)
    # 1 qubit, 1 classical bit, all-measured so the readout path runs end-to-end.
    from qiskit import QuantumCircuit
    qc = QuantumCircuit(1, 1)
    qc.x(0)
    qc.measure(0, 0)
    job = backend.run(qc, shots=64)
    counts = job.result().get_counts()
    # Must produce a real counts dict, not raise AttributeError on sim_states.
    assert isinstance(counts, dict)
    assert sum(counts.values()) == 64


def test_qrng_source_label_sim(monkeypatch):
    """Simulator QRNG is labelled 'simulated', not 'hardware-adc-noise'."""
    monkeypatch.delenv("QSSC_FPGA_PORT", raising=False)
    import api.main as main
    main._FPGA = None  # invalidate any cached sim connection
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.get("/api/fpga/qrng?num_bytes=4&common_name=t")
    assert r.status_code == 200
    body = r.json()
    assert body.get("source") == "simulated"


def test_run_circuit_no_fpga_detected_body(monkeypatch):
    """A sync hardware run with no board returns the no-fpga-detected body."""
    monkeypatch.delenv("QSSC_FPGA_PORT", raising=False)
    import api.main as main
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.post(
        "/api/circuit/run?sync=true",
        json={"algorithm": "bell", "num_qubits": 2, "shots": 64,
              "profile": "NV", "temperature_k": 300.0, "seed": 42,
              "noise_mode": "real", "hardware": True},
    )
    # a hardware run that could not happen is not a 200: the endpoint answers
    # 503 with the same error body the dashboard reads
    assert r.status_code == 503
    body = r.json()
    assert body.get("error") == "no-fpga-detected"
    assert "message" in body


def test_calibration_no_fpga_detected_body(monkeypatch):
    """Hardware calibration with no board returns the no-fpga-detected body."""
    monkeypatch.delenv("QSSC_FPGA_PORT", raising=False)
    import api.main as main
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.post("/api/calibration/run?profile=NV&hardware=true")
    assert r.status_code == 503
    body = r.json()
    assert body.get("error") == "no-fpga-detected"


def test_calibration_simulator_path_unchanged(monkeypatch):
    """Simulator calibration still returns the 4-stage result shape."""
    monkeypatch.delenv("QSSC_FPGA_PORT", raising=False)
    import api.main as main
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.post("/api/calibration/run?profile=NV&points=40&hardware=false")
    assert r.status_code == 200
    body = r.json()
    assert "error" not in body
    assert body.get("source") == "simulator"
    assert body.get("io") == "simulated"
    assert body.get("stage4_source") == "simulated"
    # 4 stages: detector scan, grid align, D+linewidth fit, per-pin resonance+T2
    assert len(body["stages"]) == 4
    assert body["stages"][3]["pins_measured"] >= 1
    assert body["stages"][3]["source"] == "simulated"
    assert len(body.get("per_pin", [])) >= 1


@requires_serial
def test_real_odmr_sweep_helper(monkeypatch):
    """The real ODMR sweep helper normalises ADC codes to contrast dips."""
    fpga = _make_fpga_with_replies(monkeypatch, ["QSSC v1"])
    mw = MicrowaveController(fpga)
    det = DetectorInterface(fpga)

    # Feed 5 READ_DETECTOR replies: a dip in the middle (resonance).
    dip_codes = [30000, 29500, 12000, 29600, 30100]
    # Prepend the APPLY_PULSE acks (one per frequency) interleaved with reads.
    replies = []
    for c in dip_codes:
        replies.append("OK")          # APPLY_PULSE reply
        replies.append(str(c))        # READ_DETECTOR reply
    # Extend the fake's reply queue (ID already consumed during __init__).
    fpga.ser._replies.extend(replies)

    import api.main as main
    f0, span, n = 2.87, 0.1, 5
    f, contrast = main._real_odmr_sweep(mw, det, f0, span, n, site=0)
    assert len(contrast) == n
    assert contrast.min() == contrast[2]            # middle point is the dip
    assert contrast.max() == pytest.approx(1.0)     # normalised to the peak
    assert 2.87 - 0.1 <= f[0] <= 2.87 and f[-1] >= 2.87


# --- PR3b: pulse-sequence engine + lock-in + gyro host wiring --------------

class _CmdFakeSerial:
    """Command-aware pyserial stand-in: replies by command head, not order.

    Used for the PR3b endpoint + calibration tests where many verbs are exchanged
    in a non-trivial sequence. ``ID`` -> ``QSSC v1``; SET/DRIVE/CLEAR/APPLY ->
    ``OK``; ``READ_DETECTORS`` -> 64 integer codes; ``READ_DETECTOR`` -> one
    code; ``RUN_SEQUENCE`` -> ``"<n> c0..cN-1"`` (constant codes); ``READ_LOCKIN``
    -> four accumulations.
    """

    def __init__(self, *, seq_codes=None, li=(4936, 4936, 4, 4)):
        self.written = []
        self.timeout = 1.0
        self._seq = seq_codes
        self._li = li

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def readline(self):
        cmd = self.written[-1].decode().strip() if self.written else ""
        head = cmd.split()[0] if cmd else ""
        if head == "ID":
            return b"QSSC v1\n"
        if head in ("SET_EMITTER", "DRIVE_EMITTER", "DRIVE_PATTERN",
                    "CLEAR_EMITTERS", "APPLY_PULSE", "CLEAR_DETECTORS",
                    "CALIBRATE", "RESET"):
            return b"OK\n"
        if head == "READ_DETECTORS":
            return (" ".join(["30000"] * 64) + "\n").encode()
        if head == "READ_DETECTOR":
            return b"30000\n"
        if head == "RUN_SEQUENCE":
            n = int(cmd.split()[3]) if len(cmd.split()) > 3 else 8
            codes = self._seq if self._seq is not None else [30000] * n
            codes = list(codes)[:n] + [30000] * max(0, n - len(codes))
            return (" ".join([str(n)] + [str(int(c)) for c in codes]) + "\n").encode()
        if head == "READ_LOCKIN":
            s = self._li
            return (f"{s[0]:010d} {s[1]:010d} {s[2]:010d} {s[3]:010d}\n").encode()
        return b"OK\n"

    def close(self):
        pass

    def reset_input_buffer(self):
        pass


def _patch_cmd_serial(monkeypatch, **fake_kw):
    """Monkeypatch pyserial so make_connection() builds a real FPGAConnection
    backed by a :class:`_CmdFakeSerial`."""
    import serial
    import qssc.backend_fpga as backend_fpga
    fake = _CmdFakeSerial(**fake_kw)
    monkeypatch.setattr(serial, "Serial", lambda *a, **k: fake)
    monkeypatch.setattr(backend_fpga.time, "sleep", lambda *_a, **_k: None)
    return fake


@requires_serial
def test_run_sequence_parses_real_wire_format(monkeypatch):
    """FPGAConnection.run_sequence parses '<n> c0..cN-1' into an int array."""
    fpga = _make_fpga_with_replies(monkeypatch, ["QSSC v1"])
    fpga.ser._replies.extend(["8 30000 28000 26000 24000 22000 20000 18000 16000"])
    codes = fpga.run_sequence(0, 1, 8, 500)          # Hahn, 8 windows
    assert codes.shape == (8,)
    assert codes.dtype == np.int32
    assert int(codes[0]) == 30000 and int(codes[-1]) == 16000


@requires_serial
def test_read_lockin_parses_real_wire_format(monkeypatch):
    """FPGAConnection.read_lockin parses the 4 accumulations + computes SNR."""
    fpga = _make_fpga_with_replies(monkeypatch, ["QSSC v1"])
    # sum_on=4936 (MW on, dark), sum_off=4936 (MW off, bright), n_on=n_off=4.
    fpga.ser._replies.append("0000004936 0000004936 0000000004 0000000004")
    li = fpga.read_lockin(0, 4)
    assert li["n_frames"] == 8
    assert li["differential_code"] == 0.0           # equal sums -> no contrast
    assert "integrated_snr" in li and "single_frame_snr" in li


@requires_serial
def test_read_lockin_differential_sign(monkeypatch):
    """sum_off (bright) > sum_on (dark) -> positive bright-minus-dark diff."""
    fpga = _make_fpga_with_replies(monkeypatch, ["QSSC v1"])
    # MW off (bright) accumulates more than MW on (dark): off=20000, on=12000.
    fpga.ser._replies.append("0000012000 0000020000 0000000004 0000000004")
    li = fpga.read_lockin(0, 4)
    assert li["differential_code"] > 0.0            # bright - dark > 0
    assert li["bright_code"] > li["dark_code"]


@requires_serial
def test_read_gyro_parses_real_wire_format(monkeypatch):
    """FPGAConnection.read_gyro runs RUN_SEQUENCE(Ramsey) + fits a rate."""
    import math
    n, t_step = 16, 6250
    t = np.arange(n) * t_step * 1e-9
    omega = 2 * math.pi * 20e3                      # 20 kHz fringe
    codes = (30000 + 8000 * np.cos(omega * t) * np.exp(-t / 50e-6)).round().astype(int)
    seq_reply = " ".join([str(n)] + [str(int(c)) for c in codes])
    fpga = _make_fpga_with_replies(monkeypatch, ["QSSC v1", seq_reply])
    g = fpga.read_gyro(0, n * t_step, n)
    assert "measured_rate_dps" in g and "rate_grade" in g and "t2_us" in g
    assert g["measured_rate_dps"] > 0.0             # FFT picked up the fringe
    assert isinstance(g["rate_grade"], bool)
    assert isinstance(g["trace"].get("signal"), list) and len(g["trace"]["signal"]) == n


def test_api_gyro_hardware_no_board(monkeypatch):
    """/api/gyro?hardware=true with no board -> no-fpga-detected body."""
    monkeypatch.delenv("QSSC_FPGA_PORT", raising=False)
    import api.main as main
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.get("/api/gyro?hardware=true&n_avg=8")
    assert r.status_code == 503
    assert r.json().get("error") == "no-fpga-detected"


def test_api_signal_hardware_no_board(monkeypatch):
    """/api/signal?hardware=true with no board -> no-fpga-detected body."""
    monkeypatch.delenv("QSSC_FPGA_PORT", raising=False)
    import api.main as main
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.get("/api/signal?hardware=true&sequence=ramsey&points=16")
    assert r.status_code == 503
    assert r.json().get("error") == "no-fpga-detected"


@requires_serial
def test_api_gyro_hardware_with_board(monkeypatch):
    """/api/gyro?hardware=true with a fake board -> measured-rate dict."""
    import math
    monkeypatch.setenv("QSSC_FPGA_PORT", "/dev/ttyFAKE0")
    n, t_step = 16, 6250
    t = np.arange(n) * t_step * 1e-9
    omega = 2 * math.pi * 20e3
    codes = (30000 + 8000 * np.cos(omega * t) * np.exp(-t / 50e-6)).round().astype(int)
    _patch_cmd_serial(monkeypatch, seq_codes=list(codes))
    import api.main as main
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.get(f"/api/gyro?hardware=true&n_avg={n}&free_precession_us=100")
    assert r.status_code == 200
    body = r.json()
    assert "error" not in body
    assert body["mode"] == "hardware"
    assert body["measured_rate_dps"] > 0.0


@requires_serial
def test_calibration_hardware_stage4_says_model_when_the_fit_fails(monkeypatch):
    """Constant stub codes: every T2 fit falls back to the model, so the
    stage-4 label must say model -- not "measured" (audit #7b used to pin the
    lying label here)."""
    monkeypatch.setenv("QSSC_FPGA_PORT", "/dev/ttyFAKE0")
    _patch_cmd_serial(monkeypatch)          # constant codes -> fit falls back to model
    import api.main as main
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.post("/api/calibration/run?profile=NV&hardware=true&grid=2&points=4")
    assert r.status_code == 200
    body = r.json()
    assert "error" not in body
    assert body["source"] == "hardware"
    assert body["io"] == "real"
    assert body["stage4_source"] == "model (RUN_SEQUENCE fit unavailable)"
    assert body["stages"][3]["source"] == "model (RUN_SEQUENCE fit unavailable)"
    assert len(body["per_pin"]) == 4                   # grid=2 -> 4 pins
    assert all("t2_us" in p and "t2_star_us" in p for p in body["per_pin"])
    assert all(p["t2_source"] == "model (fit fallback)" for p in body["per_pin"])


@requires_serial
def test_calibration_hardware_stage4_measured_when_the_fit_works(monkeypatch):
    """Decaying Ramsey/Hahn codes fit per pin -> the label is measured."""
    import math
    monkeypatch.setenv("QSSC_FPGA_PORT", "/dev/ttyFAKE0")
    n, t_step = 16, 6250
    tt = np.arange(n) * t_step * 1e-9
    omega = 2 * math.pi * 120e3
    codes = (30000 + 8000 * np.cos(omega * tt) * np.exp(-tt / 40e-6)).round().astype(int)
    _patch_cmd_serial(monkeypatch, seq_codes=list(codes))
    import api.main as main
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.post("/api/calibration/run?profile=NV&hardware=true&grid=2&points=4")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "error" not in body
    assert body["stage4_source"] == "measured (Ramsey/Hahn via RUN_SEQUENCE)"
    assert body["stages"][3]["source"] == "measured (Ramsey/Hahn via RUN_SEQUENCE)"
    assert all(p["t2_source"] == "measured" for p in body["per_pin"]), body["per_pin"]


@requires_serial
def test_api_signal_hardware_with_board(monkeypatch):
    """/api/signal?hardware=true with a fake board -> measured trace shape."""
    monkeypatch.setenv("QSSC_FPGA_PORT", "/dev/ttyFAKE0")
    _patch_cmd_serial(monkeypatch, seq_codes=[30000, 28000, 26000, 24000,
                                               22000, 20000, 18000, 16000])
    import api.main as main
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.get("/api/signal?hardware=true&sequence=ramsey&points=8")
    assert r.status_code == 200
    body = r.json()
    assert "error" not in body
    assert body["source"] == "hardware"
    assert len(body["time_ns"]) == 8 and len(body["signal"]) == 8
    assert all(0.0 <= float(v) <= 1.05 for v in body["signal"])