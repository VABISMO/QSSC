"""Hardware-real mode: the genuine FPGA path is distinct from the simulator.

These tests pin the contract the user asked for:
  * ``hardware=True`` with no board (``QSSC_FPGA_PORT`` unset) raises
    :class:`NoFPGADetected` / returns a ``no-fpga-detected`` body — never a
    silent simulation.
  * ``hardware=False`` keeps today's ``SimulatedFPGA`` path unchanged.
  * The optical-readout path no longer pokes ``sim_states`` on a real
    connection (a real ``FPGAConnection`` has no such attribute).
  * The QRNG / circuit result ``source`` label is truthful.
"""
import pytest

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
    import qssc.backend as backend

    fake = _FakeSerial(replies)

    def _fake_serial(*a, **k):
        return fake

    monkeypatch.setattr(serial, "Serial", _fake_serial)
    # Skip the 2 s board-settle sleep so the test is fast.
    monkeypatch.setattr(backend.time, "sleep", lambda *_a, **_k: None)
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
    assert r.status_code == 200
    body = r.json()
    result = body["result"]
    assert result.get("error") == "no-fpga-detected"
    assert "message" in result


def test_calibration_no_fpga_detected_body(monkeypatch):
    """Hardware calibration with no board returns the no-fpga-detected body."""
    monkeypatch.delenv("QSSC_FPGA_PORT", raising=False)
    import api.main as main
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.post("/api/calibration/run?profile=NV&hardware=true")
    assert r.status_code == 200
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
    # 4 stages: detector scan, grid align, D+linewidth fit, per-pin resonance+T2
    assert len(body["stages"]) == 4
    assert body["stages"][3]["pins_measured"] >= 1
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