# tests/test_core.py
"""Unit tests for the QSSC core: defects, simulator, circuits and backend."""
import numpy as np
import pytest

from qssc import (
    HBN_VB, get_profile, coherence_at_temperature,
    auto_align, simulate_odmr, simulate_cqd_pulse, make_detector_response,
    xy8_decoupling, estimate_coherence_xy8,
    QSSCBackend, SimulatedFPGA, is_prime, mod_mult_gate,
    create_period_finding_circuit, create_shor_circuit, postprocess_period,
    create_qft_nos_circuit, postprocess_period_swapless,
    create_math_circuit,
)
from qssc.protocol import protocol_as_dict, protocol_summary


# -- defects ----------------------------------------------------------------

def test_hbn_profile_defaults():
    p = get_profile("hBN-VB")
    assert p.zero_field_splitting == pytest.approx(3.46e9)
    assert p.t2_room > 0 and p.t1_room > 0
    assert get_profile("NV").zero_field_splitting == pytest.approx(2.87e9)


def test_coherence_scaling():
    t1_hot, t2_hot = coherence_at_temperature(HBN_VB, 300.0)
    t1_cold, t2_cold = coherence_at_temperature(HBN_VB, 77.0)
    # cooling should improve both T1 and T2
    assert t1_cold > t1_hot
    assert t2_cold > t2_hot
    # T2 bounded by 2*T1
    assert t2_hot <= 2 * t1_hot


# -- simulator ---------------------------------------------------------------

def test_cqd_pulse():
    # CQD ASE/LED pump pulse -> a noisy pump-power record in the ADC range,
    # scaled by the ASE gain factor. No galvo scan on this chip (fixed array).
    pulse = simulate_cqd_pulse(intensity=1.0, rng=np.random.default_rng(0))
    assert set(pulse.keys()) >= {"intensity", "duration_s", "adc", "ase_gain"}
    assert 0 <= pulse["adc"] <= 65535
    assert pulse["ase_gain"] >= 1.0


def test_detector_response():
    # On-chip graphene/MoS2 detector array: 64 electrodes of int32 16-bit AD7616
    # codes, bright (high code) under a pumped emitter with no resonant MW.
    rng = np.random.default_rng(0)
    codes = make_detector_response({0, 28}, profile="NV", n_sites=64, rng=rng)
    assert codes.shape == (64,) and codes.dtype == np.int32
    assert 0 <= codes.min() and codes.max() <= 65535
    # pumped electrodes are bright; un-pumped are at the background floor
    assert codes[0] > codes[1]  # channel 0 bright vs channel 1 background


def test_detector_dip_is_ensemble_contrast():
    # The on-resonance ODMR dip is the small *ensemble* contrast (~5%), not a
    # 75% single-spin swing. A pumped electrode on-resonance (MW at D) reads
    # DARK_CODE = BRIGHT_CODE*(1 - ODMR_CONTRAST), a few percent below bright.
    rng = np.random.default_rng(0)
    bright = make_detector_response({0}, profile="NV", n_sites=64, rng=rng)[0]
    rng = np.random.default_rng(0)
    dark = make_detector_response({0}, profile="NV", n_sites=64,
                                  mw_freq_khz=2_870_000, rng=rng)[0]
    dip_frac = (bright - dark) / bright
    # real NV-ensemble ODMR: 3-8%. Reject the old 75% (regression guard).
    assert 0.02 < dip_frac < 0.12, f"ODMR dip {dip_frac:.3f} not ensemble-scale"


def test_odmr_resonance_linewidth():
    # The pulse resonance acceptance is the ~5 MHz ODMR linewidth, not the old
    # 287 MHz noise*D window. On-resonance and just-within-linewidth flip the
    # spin; far off-resonance is rejected. (freq is in kHz: +4 MHz = +4000 kHz.)
    fpga = SimulatedFPGA("NV", seed=0)
    fpga.send_command("DRIVE_EMITTER 0 50")
    # 2.87 GHz exactly (on) and +4 MHz (within the 5 MHz line) -> OK
    assert fpga.send_command("APPLY_PULSE 0 2870000 2 200 0") == "OK"
    assert fpga.send_command("APPLY_PULSE 0 2874000 2 200 0") == "OK"
    # +10 MHz and +20 MHz (outside the ~5 MHz linewidth) -> ERR
    assert fpga.send_command("APPLY_PULSE 0 2880000 2 200 0") == "ERR"
    assert fpga.send_command("APPLY_PULSE 0 2890000 2 200 0") == "ERR"


def test_auto_align_grid():
    sites = auto_align("hBN-VB", grid=8, rng=np.random.default_rng(0))
    assert len(sites) == 64
    assert all(s.qubit_id == i for i, s in enumerate(sites))


def test_odmr_hbn_resonance():
    f, c = simulate_odmr("hBN-VB")
    assert len(f) == len(c)
    # the deepest dip should sit within the Zeeman-split pair around D
    dip = f[np.argmin(c)]
    assert abs(dip - HBN_VB.d_ghz) < 0.06


def test_odmr_field_splitting():
    f0, c0 = simulate_odmr("hBN-VB", b_field_t=0.0)
    # zero field: dips collapse towards D (single pair at +/-A)
    dip0 = f0[np.argmin(c0)]
    assert abs(dip0 - HBN_VB.d_ghz) < 0.06


def test_xy8():
    seq = xy8_decoupling(1)
    assert len(seq) == 8
    assert estimate_coherence_xy8(1e-6, n_blocks=2) > 1e-6


def test_simulated_fpga_protocol():
    # On-chip parallel readout command set: DRIVE_EMITTER / SET_EMITTER /
    # DRIVE_PATTERN / CLEAR_EMITTERS / READ_DETECTOR / APPLY_PULSE /
    # READ_DETECTORS / CLEAR_DETECTORS / CALIBRATE. No CAPTURE_FRAME.
    fpga = SimulatedFPGA("NV", seed=1)
    assert fpga.send_command("DRIVE_EMITTER 0 50") == "OK"
    # with no resonant MW yet, a pumped site reads bright (high 16-bit code)
    code = int(fpga.send_command("READ_DETECTOR 0"))
    assert 0 <= code <= 65535 and code > 1000
    # parallel multi-emitter path: arm two channels, fire in parallel, clear
    assert fpga.send_command("SET_EMITTER 10 4095") == "OK"
    assert fpga.send_command("SET_EMITTER 20 4095") == "OK"
    assert fpga.send_command("DRIVE_PATTERN 50") == "OK"
    stream = fpga.send_command("READ_DETECTORS")
    toks = stream.split()
    assert len(toks) == 64
    assert all(t.lstrip("-").isdigit() for t in toks)
    # channels 10 and 20 are armed -> bright; channel 11 is not -> background
    assert int(toks[10]) > int(toks[11])
    assert fpga.send_command("CLEAR_EMITTERS") == "OK"
    assert fpga.send_command("CLEAR_DETECTORS") == "OK"
    assert fpga.send_command("CALIBRATE") == "OK"
    # unknown / malformed -> ERR
    assert fpga.send_command("FIRE_LASER 0 50") == "ERR"
    assert fpga.send_command("CAPTURE_FRAME") == "ERR"


def test_readout_odmr_dip():
    # CQD pump -> graphene detector photocurrent -> ODMR dip at resonance.
    # A pumped site with no resonant MW reads bright (ms=0, high code); a
    # resonant X-gate (phase ~= pi) flips the spin to dark (ms=+-1, low code),
    # and an off-resonance drive is rejected by the linewidth check (ERR).
    fpga = SimulatedFPGA("NV", seed=3)
    assert fpga.send_command("DRIVE_EMITTER 45 50") == "OK"
    # Lock-in averaging: read the electrode several times and take the mean so
    # the small ensemble ODMR dip (~5%, ~2560 16-bit counts) is resolved above
    # the per-sample lock-in noise -- exactly what the 1 kHz lock-in does on
    # the real chip. A single noisy sample would not robustly show the dip.
    bright = np.mean([int(fpga.send_command("READ_DETECTOR 45")) for _ in range(20)])
    # resonant X-gate at D_NV = 2.87 GHz (phase 3142 mrad ~= pi) -> spin flip
    assert fpga.send_command("APPLY_PULSE 45 2870000 2 200 3142") == "OK"
    dark = np.mean([int(fpga.send_command("READ_DETECTOR 45")) for _ in range(20)])
    # the ODMR dip is the ~5% ensemble contrast (~2560 counts); averaging leaves
    # it well above the residual lock-in noise, so assert > 1000 counts of dip.
    assert bright > dark + 1000
    # off-resonance drive (4 GHz vs 2.87 GHz, far outside the ~5 MHz linewidth)
    # -> ERR, no flip applied
    assert fpga.send_command("APPLY_PULSE 45 4000000 2 200 3142") == "ERR"


def test_calibration_detector_lut():
    # The external CalibrationManager sweep drives each emitter channel in
    # turn, reads all 64 detector electrodes in parallel, and registers the
    # modulated one. With single-channel addressing this must recover the
    # identity LUT (emitter channel i -> detector electrode i), not a
    # degenerate always-picks-zero mapping.
    from qssc.backend import (EmitterController, DetectorInterface,
                              CalibrationManager)
    fpga = SimulatedFPGA("NV", seed=4)
    em = EmitterController(fpga)
    det = DetectorInterface(fpga)
    cal = CalibrationManager(em, det)
    mapping = cal.perform_calibration()
    assert len(mapping) == 64
    for channel, electrode in mapping.items():
        assert electrode == channel  # 1:1 emitter-channel -> detector-electrode
    # the default identity LUT must agree with the swept one
    assert mapping == CalibrationManager.default_detector_lut()


# -- circuits ----------------------------------------------------------------

def test_is_prime():
    assert is_prime(2) and is_prime(17) and is_prime(1000003)
    assert not is_prime(1) and not is_prime(15) and not is_prime(1000004)


def test_mod_mult_gate():
    g = mod_mult_gate(7, 15)
    assert "7" in g.label and "15" in g.label
    with pytest.raises(ValueError):
        mod_mult_gate(5, 15)  # gcd != 1


def test_period_circuit_and_postprocess():
    qc = create_period_finding_circuit(15, 7)
    assert qc.num_clbits > 0
    # synthetic ideal counts for r=4, a=7, N=15 -> factors 3,5 (4 control bits)
    counts = {"0000": 256, "0100": 256, "1000": 256, "1100": 256}
    r, factors = postprocess_period(counts, 4, 7, 15)
    assert r == 4
    assert factors == (3, 5)


def test_shor_circuit_builds():
    qc, a = create_shor_circuit(15, a=7)
    assert a == 7 and qc.num_qubits > 0


def test_qft_nos_fewer_qubits_than_shor():
    # QFT-NOS uses a reduced counting register t=l+1 vs Shor's 2l+1
    nos_qc, a = create_qft_nos_circuit(15, a=7)
    shor_qc, _ = create_shor_circuit(15, a=7)
    assert a == 7
    assert nos_qc.num_qubits < shor_qc.num_qubits
    # swapless readout: the hardware measures bit-reversed QPE strings, and
    # postprocess_period_swapless reverses them back. For r=4 the standard QPE
    # phases k/4 (k=1,2,3) land at decimals 8,16,24 -> 5-bit "01000","10000",
    # "11000"; the swapless hardware emits the reversed strings, which we feed
    # here to confirm the function recovers r=4 and factors 3,5.
    counts = {"00010": 256, "00001": 256, "00011": 256}
    r, factors = postprocess_period_swapless(counts, nos_qc.num_clbits, 7, 15)
    assert r == 4
    assert factors == (3, 5)


def test_qft_nos_factors_15_cold():
    # Like Shor: decoheres at 300 K, recovers 3x5 when cooled.
    b_cold = QSSCBackend("hBN-VB", sites=_quick_sites(20), seed=1,
                         temperature_k=1e-3, noise_mode="real")
    qc, a = create_qft_nos_circuit(15, a=7)
    res = b_cold.run(qc, shots=4096).result()
    _, factors = postprocess_period_swapless(res.get_counts(), qc.num_clbits, a, 15)
    assert factors == (3, 5)


# -- crypto (quantum RNG -> Ed25519 + X.509) ---------------------------------

def test_von_neumann_and_keypair():
    from qssc.crypto import von_neumann_extract, bits_to_bytes, derive_keypair
    raw = [0, 1, 1, 0, 0, 1, 1, 0, 1, 0, 0, 1] * 4
    bits, stats = von_neumann_extract(raw, need=16)
    assert stats["sufficient"] is True
    assert len(bits) >= 16
    raw_bytes = bits_to_bytes(bits[:16])
    assert len(raw_bytes) == 2
    entropy = bytes(range(64))  # deterministic 64-byte block
    key = derive_keypair(entropy, common_name="QSSC-test-node")
    assert key["algorithm"] == "Ed25519"
    assert "BEGIN CERTIFICATE" in key["certificate_pem"]
    assert "BEGIN PRIVATE KEY" in key["private_key_pem"]
    assert key["common_name"] == "QSSC-test-node"
    # the serial is a <=159-bit integer inside the X.509 cert, but the returned
    # dict must carry it as a *string* so the async-job result is BSON/Mongo
    # serialisable (BSON int is capped at 8 bytes / 2^63). Regression for the
    # bug where the finished qrand job never persisted -> blank result block.
    assert isinstance(key["serial"], str)
    assert int(key["serial"], 16) > 0
    # the full arbitrary-precision serial is still embedded in the cert itself
    assert "BEGIN CERTIFICATE" in key["certificate_pem"]


def test_qrand_result_bson_serialisable():
    """The async qrand job persists the runner result to MongoDB; the
    ``serial`` field used to be a ~159-bit int that overflows BSON
    (``OverflowError: MongoDB can only handle up to 8-byte ints``), so the
    finished record never persisted and the frontend showed a blank result.
    Encode the runner result with BSON to lock the fix in."""
    from api.runner import run_circuit
    r = run_circuit({"algorithm": "qrand", "profile": "hBN-VB",
                     "temperature_k": 300.0, "seed": 42, "noise_mode": "real",
                     "shots": 8192, "num_qubits": 8, "num_bytes": 16,
                     "common_name": "QSSC-bson-regression"})
    assert "error" not in r
    assert isinstance(r["serial"], str)
    try:
        import bson
        bson.BSON.encode(r)  # must not raise OverflowError
    except ImportError:
        pass


def test_qrand_private_key_not_persisted(monkeypatch):
    """The qrand private key + symmetric key must never be persisted to the
    job store (Mongo / fallback). The worker stashes them in a one-time
    in-memory store and strips them from the persisted result; the first
    finished-status poll delivers them once, and subsequent polls do not.
    Regression for the private-key-in-MongoDB leak."""
    from api import jobs
    # force the in-process fallback (no Redis/Mongo) for a deterministic test
    monkeypatch.setattr(jobs, "_get_redis", lambda: None)
    monkeypatch.setattr(jobs, "_get_mongo", lambda: None)
    jobs._fallback_store.clear()
    jobs._secret_store.clear()
    payload = {"algorithm": "qrand", "profile": "hBN-VB",
               "temperature_k": 300.0, "seed": 42, "noise_mode": "real",
               "shots": 8192, "num_qubits": 8, "num_bytes": 16,
               "common_name": "QSSC-secret-scrub"}
    job_id = jobs.enqueue("circuit", payload)
    import time as _t
    first_finished = None
    for _ in range(400):
        rec = jobs.get_job(job_id)
        if rec and rec.get("status") == "finished":
            first_finished = rec  # the first finished read delivers secrets
            break
        _t.sleep(0.1)
    assert first_finished is not None, "qrand job did not finish"
    # (1) one-time delivery: the first finished poll carries the private key
    assert "BEGIN PRIVATE KEY" in first_finished["result"]["private_key_pem"]
    assert first_finished["result"]["symmetric_key_hex"]
    # (2) the persisted record (in-process fallback store) never stored secrets
    persisted = jobs._fallback_store[job_id]["result"]
    assert "BEGIN CERTIFICATE" in persisted["certificate_pem"]
    assert "private_key_pem" not in persisted
    assert "symmetric_key_hex" not in persisted
    # (3) re-polling does NOT re-deliver the one-time secrets
    rec2 = jobs.get_job(job_id)
    assert "private_key_pem" not in rec2["result"]
    assert "symmetric_key_hex" not in rec2["result"]
    jobs.delete_job(job_id)


# -- QKD (BB84) --------------------------------------------------------------

def test_bb84_secure_without_eve_detected_with_eve():
    from qssc.qkd import simulate_bb84, BB84_QBER_THRESHOLD
    b = QSSCBackend("hBN-VB", sites=_quick_sites(16), seed=2,
                    temperature_k=1e-3, noise_mode="real")
    clean = simulate_bb84(b, num_bits=256, eavesdrop=False, shots=512, seed=7)
    assert clean["qber"] < BB84_QBER_THRESHOLD
    assert clean["secure"] is True
    assert clean["eve_detected"] is False
    # intercept-resend Eve raises QBER towards ~25% -> detected, insecure
    eve = simulate_bb84(b, num_bits=256, eavesdrop=True, shots=512, seed=7)
    assert eve["qber"] > BB84_QBER_THRESHOLD
    assert eve["secure"] is False
    assert eve["eve_detected"] is True


def test_math_circuits_build():
    for op, args in [("add", (2, 3)), ("mul", (3, 4)), ("sqrt", (16, None))]:
        qc = create_math_circuit(op, args[0], args[1])
        assert qc.num_clbits > 0


def _math_top(op, a, b, shots=4096, seed=0):
    """Run a math circuit via the runner (the real, transpiled path) and return
    the top result integer."""
    from api.runner import run_circuit
    r = run_circuit({"algorithm": "math", "op": op, "a": a, "b": b,
                     "profile": "NV", "shots": shots, "seed": seed})
    assert "error" not in r, f"runner error: {r.get('error')}"
    return r["result"]


def test_math_sub_add_signed():
    # regression: sub() used to add (a no-op `n2=-n2; a2=abs(n2)`), and add() with
    # a negative second operand also added. Both must now produce a-b / a+b.
    assert _math_top("sub", 10, 3) == 7, "sub(10,3) must be 7, not 13"
    assert _math_top("add", 10, -3) == 7, "add(10,-3) must be 7, not 13"
    assert _math_top("add", 3, 4) == 7
    assert _math_top("sub", 20, 5) == 15


def test_math_sqrt_log_no_second_operand():
    # regression: runner crashed with float(None) when sqrt/log had no 2nd operand
    from api.runner import run_circuit
    r = run_circuit({"algorithm": "math", "op": "sqrt", "a": 16,
                     "profile": "NV", "shots": 256, "seed": 0})
    assert "error" not in r, f"sqrt without b must not crash: {r.get('error')}"


def test_user_circuit_python_and_qasm():
    from qssc import build_user_circuit, UserCircuitError
    py = ("from qiskit import QuantumCircuit\n"
          "qc = QuantumCircuit(2, 2)\n"
          "qc.h(0); qc.cx(0, 1); qc.measure(range(2), range(2))\n")
    qc = build_user_circuit(py)
    assert qc.num_qubits == 2 and qc.num_clbits == 2
    qasm = ("OPENQASM 2.0;\nqreg q[2];\ncreg c[2];\n"
            "h q[0];\ncx q[0],q[1];\nmeasure q->c;\n")
    qc2 = build_user_circuit(qasm)
    assert qc2.num_qubits == 2
    # auto-measure when no classical bits
    qc3 = build_user_circuit("from qiskit import QuantumCircuit\n"
                             "qc = QuantumCircuit(2)\nqc.h(0); qc.cx(0,1)")
    assert qc3.num_clbits >= 2
    # blocked import + qubit cap
    with pytest.raises(UserCircuitError):
        build_user_circuit("import os\nqc = QuantumCircuit(2)")
    with pytest.raises(UserCircuitError):
        build_user_circuit("from qiskit import QuantumCircuit\n"
                           "qc = QuantumCircuit(99)", max_qubits=64)


def test_user_circuit_numpy_file_io_blocked():
    from qssc import build_user_circuit, UserCircuitError
    # numpy numeric use still works via the convenience np
    ok = ("import numpy as np\n"
          "from qiskit import QuantumCircuit\n"
          "qc = QuantumCircuit(2, 2)\nqc.h(0); qc.cx(0,1); qc.measure(0,0)\n")
    build_user_circuit(ok)
    # numpy file-IO stripped from the convenience np ...
    with pytest.raises(UserCircuitError):
        build_user_circuit(
            "from qiskit import QuantumCircuit\n"
            "qc = QuantumCircuit(2); qc.h(0)\n"
            "np.load('/etc/passwd')\n")
    # ... and from a bare `import numpy`
    with pytest.raises(UserCircuitError):
        build_user_circuit(
            "import numpy\n"
            "from qiskit import QuantumCircuit\n"
            "qc = QuantumCircuit(2); qc.h(0)\n"
            "numpy.fromfile('/etc/passwd')\n")


# -- backend -----------------------------------------------------------------

def _quick_sites(n=16):
    from qssc.simulator import QubitSite
    p = HBN_VB
    t1, t2 = coherence_at_temperature(p, 300.0)
    return [QubitSite(qubit_id=i, row=i // 8, col=i % 8, x_um=0, y_um=0,
                      intensity=150.0, t1=t1, t2=t2, readout_err=0.12)
            for i in range(n)]


def test_backend_describe():
    b = QSSCBackend("hBN-VB", sites=_quick_sites(), seed=0)
    d = b.describe()
    assert d["defect"].startswith("hBN")
    assert d["num_qubits"] == 16
    assert d["d_ghz"] == pytest.approx(3.46, abs=0.01)


def test_optical_readout_chain():
    """The Backend-physics optical chain: emitter -> ASE -> waveguide ->
    filter -> graphene detector -> lock-in, anchored to the simulator's
    verified BRIGHT_CODE / ODMR contrast / lock-in SNR."""
    b = QSSCBackend("NV", sites=_quick_sites(), seed=0)
    o = b.optical_readout()
    # emitter (CQD ASE array, green)
    assert o["emitter"]["pixels"] == 16
    assert o["emitter"]["ase_gain"] == pytest.approx(1.15)
    assert o["emitter"]["pump_optical_power_mw"] > 0
    # waveguide + filter stage present
    assert o["waveguide"]["propagation_efficiency"] <= 1.0
    assert o["filter"]["transmission"] == pytest.approx(0.92)
    # canonical on-chip design is waveguide-direct: no microlens stage at all
    assert "microlens" not in o
    # detector + TIA + 4x AD7616 parallel 16-bit ADC (no MUX, no XADC)
    assert o["detector"]["adc_bits"] == 16
    assert "AD7616" in o["detector"]["adc"]
    assert "mux" not in o["detector"]
    assert o["detector"]["responsivity_a_per_w"] > 0
    # readout figures anchor to the simulator constants
    r = o["readout"]
    assert r["bright_code"] == 51200
    assert r["dark_code"] == 48640
    assert r["dip_counts"] == 2560
    assert r["readout_noise_counts"] == 640
    assert r["single_read_snr_sigma"] == pytest.approx(4.0)
    assert r["odmr_contrast"] == pytest.approx(0.05)
    assert r["photocurrent_bright_ua"] == pytest.approx(
        (51200 / 65535) * 1.0 / 1e6 * 1e6, rel=1e-3)
    # end-to-end link budget reconciles with BRIGHT_CODE
    lb = o["link_budget"]
    assert 0 < lb["end_to_end_efficiency"] < 0.01
    assert lb["at_detector_uw"] > 0
    assert "BRIGHT_CODE" in lb["reconciles_with"]
    # describe() surfaces the block
    assert "optical_readout" in b.describe()


def test_optical_readout_is_temperature_dependent():
    """The optical chain RECALCULATES per run: cooling cuts detector noise
    (sqrt T) and raises ODMR contrast, so the lock-in SNR must climb at 77 K
    vs 300 K -- not stay at a fixed snapshot. Also the SiC NIR stack differs
    (916 nm pump, 850 nm long-pass, few-layer MoS2 detector)."""
    warm = QSSCBackend("NV", sites=_quick_sites(), seed=0, temperature_k=300.0)
    cold = QSSCBackend("NV", sites=_quick_sites(), seed=0, temperature_k=77.0)
    rw, rc = warm.optical_readout()["readout"], cold.optical_readout()["readout"]
    # noise falls, contrast rises, SNR climbs sharply when cold
    assert rc["readout_noise_counts"] < rw["readout_noise_counts"]
    assert rc["odmr_contrast"] > rw["odmr_contrast"]
    assert rc["single_read_snr_sigma"] > rw["single_read_snr_sigma"]
    assert rc["single_read_snr_sigma"] > 4.0          # 77 K beats the 300 K 4sigma
    # bright level is anchored (detector saturation), only dark/dip move
    assert rc["bright_code"] == rw["bright_code"] == 51200
    # SiC uses the NIR stack
    sic = QSSCBackend("SiC-VSi", sites=_quick_sites(), seed=0).optical_readout()
    assert sic["emitter"]["wavelength_nm"] >= 700
    assert "850" in sic["filter"]["type"]
    assert "MoS2" in sic["detector"]["type"]


def test_runner_surfaces_per_run_optical():
    """The runner result dict carries the per-run optical readout data
    (displayed bitstring, measured states, photocurrent) computed in
    backend._drive_optical_readout, so the frontend Execution component
    physics card reflects the circuit that actually ran."""
    from api.runner import run_circuit
    r = run_circuit({"algorithm": "bell", "profile": "NV",
                     "temperature_k": 295.0, "shots": 256, "seed": 1})
    assert "error" not in r
    assert "optical" in r, "runner must surface res.optical for per-run physics"
    opt = r["optical"]
    # the bell circuit measures 2 qubits over the 64-site grid
    assert len(opt.get("read_states", [])) == 64
    assert opt.get("displayed_bitstring")
    assert opt.get("photocurrent_mean", 0) >= 0


def test_readout_reflects_measured_bit():
    """The per-run optical readout must reflect the measured bit, not a fixed
    bright level: a measured-1 qubit is pumped with its spin in the dark ms=+-1
    state (the ODMR dip the circuit's MW drove) and thresholds to state 1; a
    measured-0 stays bright and thresholds to state 0. Catches the stale
    DARK_CODE=800 / no-state-injection regression where read_states was inverted."""
    from qssc.simulator import SimulatedFPGA
    from qssc.backend import QubitReadoutProcessor, EmitterController, DetectorInterface, CalibrationManager
    fpga = SimulatedFPGA("NV", seed=7)
    em = EmitterController(fpga)
    det = DetectorInterface(fpga)
    proc = QubitReadoutProcessor()
    lut = CalibrationManager.default_detector_lut()
    # threshold must sit between bright (51200) and the ~5% dark dip (48640)
    assert proc.threshold == 0.5 * (51200 + 48640)
    # measured bit 0 -> spin bright -> high code; measured bit 1 -> dark dip -> low code
    em.drive(0, 50); fpga.sim_states[0] = 0
    code_bright = det.read(0)
    em.drive(0, 50); fpga.sim_states[0] = 1
    code_dark = det.read(0)
    assert code_bright > code_dark, "bright spin must read higher than dark dip"
    import numpy as np
    codes = np.zeros(64, dtype=np.int32)
    codes[0] = code_bright
    codes[1] = code_dark
    states = proc.process(codes, lut, num_qubits=64)
    assert states[0] == 0 and states[1] == 1, "readout must recover the measured bit"


def test_backend_runs_bell():
    from qiskit import QuantumCircuit
    b = QSSCBackend("hBN-VB", sites=_quick_sites(), seed=0)
    qc = QuantumCircuit(2, 2)
    qc.h(0); qc.cx(0, 1); qc.measure([0, 1], [0, 1])
    res = b.run(qc, shots=512).result()
    counts = res.get_counts()
    assert sum(counts.values()) == 512
    # Bell state -> only 00 and 11 should dominate
    top = max(counts, key=counts.get)
    assert top in ("00", "11")


def test_backend_shor_15():
    # Honest physics: Shor's compiled modular-exponentiation decomposes to
    # hundreds of 2q gates whose ~hundreds-of-us duration far exceeds the
    # hBN V_B- room-temperature T2 (~2 us). At 300 K the circuit decoheres to a
    # near-uniform distribution and no factors are recovered; the assessment
    # must report it as NOT within the coherence window.
    b = QSSCBackend("hBN-VB", sites=_quick_sites(20), seed=1,
                    temperature_k=300.0, noise_mode="real")
    qc, a = create_shor_circuit(15, a=7)
    res = b.run(qc, shots=2048).result()
    r, factors = postprocess_period(res.get_counts(), qc.num_clbits, a, 15)
    assert res.assessment["within_coherence"] is False
    assert factors[0] is None  # decohered at room temperature -> no factors

    # Cooled to millikelvin the coherence window opens up and Shor recovers
    # 3 x 5 (a=7 -> period 4). This is the whole point of the cooling knob.
    b_cold = QSSCBackend("hBN-VB", sites=_quick_sites(20), seed=1,
                         temperature_k=1e-3, noise_mode="real")
    res_cold = b_cold.run(qc, shots=2048).result()
    _, factors_cold = postprocess_period(res_cold.get_counts(), qc.num_clbits,
                                         a, 15)
    assert res_cold.assessment["within_coherence"] is True
    assert factors_cold == (3, 5)


# -- protocol ----------------------------------------------------------------

def test_protocol_data():
    sections = protocol_as_dict()
    assert len(sections) >= 5
    assert all(s["steps"] for s in sections)
    s = protocol_summary()
    assert "room temperature" in s["operating_temp"].lower()
    assert s["d_GHz"] == pytest.approx(3.46)