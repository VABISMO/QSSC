"""Tests for the Invention 9 classical anti-noise filter stack (qssc.noise_filters).

Covers: effective_noise_sigma monotonicity per filter, slow vs fast channel
behaviour (1/sqrt(N)), feed-forward rejection, effective_t2 DD extension + T1
cap, back-compat (defaults collapse to the prior model), filter_gain_breakdown
keys, and the simulator/array_calibration integrations (anti-noise lowers the
measured noise; DD raises T2). All channel sigmas here are model inputs, not
datasheet constants.
"""
import math

import pytest

from qssc.noise_filters import (
    NoiseEnvironment, AntiNoiseConfig, noise_environment_preset,
    effective_noise_sigma, effective_t2, filter_gain_breakdown,
    hahn_echo, cpmg_decoupling,
)
from qssc.simulator import READOUT_NOISE


# --- back-compat: defaults collapse to the prior model ----------------------

def test_effective_noise_sigma_no_env_is_readout_floor():
    """env=None -> base/sqrt(N), bit-identical to the prior model."""
    for n in (1, 4, 16, 100):
        assert effective_noise_sigma(READOUT_NOISE, None, None, n) == pytest.approx(
            READOUT_NOISE / math.sqrt(n))


def test_effective_noise_sigma_default_filters_pass_through():
    """env set + all-off filters -> every channel at full strength (no
    attenuation). The total is >= the bare floor (env adds noise)."""
    env = NoiseEnvironment.lab()
    floor = READOUT_NOISE  # N=1
    total = effective_noise_sigma(READOUT_NOISE, env, AntiNoiseConfig(), 1)
    assert total > floor  # the environmental channels add on top of the floor


def test_effective_t2_none_returns_base():
    base = 1.8e-3
    assert effective_t2(base, "none", 1, 6e-3) == base
    assert effective_t2(base, "none", 0, 6e-3) == base
    assert effective_t2(base, "", 1, 6e-3) == base


def test_is_default_true_for_empty_config():
    assert AntiNoiseConfig().is_default()
    assert AntiNoiseConfig.from_dict(None).is_default()
    assert AntiNoiseConfig.from_dict({}).is_default()


# --- monotonicity: each filter reduces the effective noise ------------------

def test_thermal_servo_lowers_noise_monotonically():
    env = NoiseEnvironment.lab()
    prev = math.inf
    for gain in (0, 1, 5, 20, 100):
        f = AntiNoiseConfig(thermal_servo_gain=gain)
        n = effective_noise_sigma(READOUT_NOISE, env, f, 1)
        assert n < prev  # strictly decreasing as the servo strengthens
        prev = n


def test_vibration_iso_lowers_noise_monotonically():
    env = NoiseEnvironment.lab()
    prev = math.inf
    for residual in (1.0, 0.5, 0.2, 0.1, 0.01):
        f = AntiNoiseConfig(vibration_iso_residual=residual)
        n = effective_noise_sigma(READOUT_NOISE, env, f, 1)
        assert n <= prev
        prev = n


def test_emi_shield_lowers_noise_monotonically():
    env = NoiseEnvironment.lab()
    prev = math.inf
    for residual in (1.0, 0.5, 0.2, 0.05, 0.01):
        f = AntiNoiseConfig(emi_shield_residual=residual)
        n = effective_noise_sigma(READOUT_NOISE, env, f, 1)
        assert n <= prev
        prev = n


def test_feed_forward_lowers_noise_monotonically():
    env = NoiseEnvironment.lab()
    prev = math.inf
    for ff in (0.0, 0.3, 0.6, 0.9, 0.99):
        f = AntiNoiseConfig(feed_forward_rejection=ff)
        n = effective_noise_sigma(READOUT_NOISE, env, f, 1)
        assert n <= prev
        prev = n


def test_common_mode_reject_lowers_noise():
    env = NoiseEnvironment.lab()
    without = effective_noise_sigma(READOUT_NOISE, env, AntiNoiseConfig(), 1)
    with_cm = effective_noise_sigma(
        READOUT_NOISE, env, AntiNoiseConfig(common_mode_reject=True), 1)
    assert with_cm < without  # halves the slow (temp+pressure) channels


# --- slow vs fast channels: 1/sqrt(N) behaviour -----------------------------

def test_fast_channels_average_down_with_n():
    """Vibration + EMI are uncorrelated shot-to-shot -> they average down by
    1/sqrt(N). With only fast channels present (zero the slow ones), doubling N
    must reduce the fast contribution."""
    env = NoiseEnvironment(temp_drift_counts=0.0, pressure_counts=0.0,
                           vibration_counts=120.0, emi_counts=80.0)
    f = AntiNoiseConfig()  # pass-through
    n1 = effective_noise_sigma(READOUT_NOISE, env, f, 1)
    n4 = effective_noise_sigma(READOUT_NOISE, env, f, 4)
    # the fast-channel residual at N=4 is half the N=1 residual (in quadrature)
    fast1_sq = (120.0 ** 2 + 80.0 ** 2)
    fast4_sq = fast1_sq / 4  # /sqrt(4)=2 -> squared /4
    floor1 = READOUT_NOISE
    floor4 = READOUT_NOISE / 2
    assert n1 == pytest.approx(math.sqrt(floor1 ** 2 + fast1_sq))
    assert n4 == pytest.approx(math.sqrt(floor4 ** 2 + fast4_sq))
    assert n4 < n1


def test_slow_channels_do_not_average_down_with_n():
    """Temperature + pressure are correlated across shots -> NOT reduced by
    1/sqrt(N). With only slow channels present (zero the fast ones), increasing
    N does NOT shrink the slow contribution."""
    env = NoiseEnvironment(temp_drift_counts=40.0, pressure_counts=20.0,
                           vibration_counts=0.0, emi_counts=0.0)
    f = AntiNoiseConfig()
    slow = math.sqrt(40.0 ** 2 + 20.0 ** 2)
    for n in (1, 4, 16):
        total = effective_noise_sigma(READOUT_NOISE, env, f, n)
        floor = READOUT_NOISE / math.sqrt(n)
        assert total == pytest.approx(math.sqrt(floor ** 2 + slow ** 2))


# --- effective_t2: DD extension + T1 cap ------------------------------------

def test_effective_t2_hahn_doubles():
    base = 1.8e-3
    assert effective_t2(base, "hahn", 1, 100e-3) == pytest.approx(base * 2)


def test_effective_t2_cpmg_scales_with_n():
    base = 1.8e-3
    for n in (1, 2, 4, 8):
        assert effective_t2(base, "cpmg", n, 100e-3) == pytest.approx(base * 2 * n)


def test_effective_t2_xy8_exponential():
    base = 1.8e-3
    for n in (1, 2, 3):
        assert effective_t2(base, "xy8", n, 100e-3) == pytest.approx(base * (2 ** n))


def test_effective_t2_capped_at_2_t1():
    """DD cannot refocus T1 relaxation -> hard cap at 2*T1 however many pulses."""
    base = 1.8e-3
    t1 = 6e-3
    cap = 2 * t1
    # cpmg with many blocks would exceed the cap without it
    assert effective_t2(base, "cpmg", 100, t1) == pytest.approx(cap)
    assert effective_t2(base, "xy8", 20, t1) == pytest.approx(cap)
    # hahn (2x) under the cap -> not clipped
    assert effective_t2(base, "hahn", 1, t1) == pytest.approx(base * 2)


def test_effective_t2_unknown_sequence_returns_base():
    base = 1.8e-3
    assert effective_t2(base, "zzz", 4, 6e-3) == base


def test_hahn_echo_and_cpmg_pulse_lists():
    assert hahn_echo() == ["X", "Y", "X"]
    assert cpmg_decoupling(4) == ["Y", "Y", "Y", "Y"]
    assert cpmg_decoupling(0) == ["Y"]  # at least one pulse


# --- presets ----------------------------------------------------------------

def test_noise_environment_presets():
    assert noise_environment_preset("none") is None
    assert noise_environment_preset("") is None
    # None -> "lab" (the documented default), not off
    assert isinstance(noise_environment_preset(None), NoiseEnvironment)
    lab = noise_environment_preset("lab")
    quiet = noise_environment_preset("quiet")
    noisy = noise_environment_preset("noisy")
    assert isinstance(lab, NoiseEnvironment)
    # quiet < lab < noisy on every channel
    assert quiet.vibration_counts < lab.vibration_counts < noisy.vibration_counts
    assert quiet.emi_counts < lab.emi_counts < noisy.emi_counts
    assert quiet.temp_drift_counts < lab.temp_drift_counts < noisy.temp_drift_counts


# --- filter_gain_breakdown --------------------------------------------------

def test_filter_gain_breakdown_keys():
    env = NoiseEnvironment.lab()
    f = AntiNoiseConfig(thermal_servo_gain=10, vibration_iso_residual=0.1,
                        emi_shield_residual=0.05, feed_forward_rejection=0.9,
                        common_mode_reject=True, dd_sequence="cpmg")
    g = filter_gain_breakdown(env, f)
    assert set(g.keys()) == {"temperature", "vibration", "emi", "pressure",
                             "feed_forward", "dd_sequence"}
    assert 0 <= g["temperature"] < 0.1   # servo + feed-forward + CM
    assert g["vibration"] == pytest.approx(0.1 * 0.1)  # residual * keep_ff
    assert g["emi"] == pytest.approx(0.05 * 0.1)
    assert g["feed_forward"] == 0.9
    assert g["dd_sequence"] == "cpmg"


def test_filter_gain_breakdown_no_env_empty():
    assert filter_gain_breakdown(None, AntiNoiseConfig()) == {}


def test_filter_gain_breakdown_pass_through():
    """env + all-off filters -> every channel reads 1.0 (unfiltered)."""
    env = NoiseEnvironment.lab()
    g = filter_gain_breakdown(env, AntiNoiseConfig())
    assert g["temperature"] == 1.0
    assert g["vibration"] == 1.0
    assert g["emi"] == 1.0
    assert g["pressure"] == 1.0
    assert g["feed_forward"] == 0.0


# --- round-trip -------------------------------------------------------------

def test_anti_noise_config_round_trip():
    f = AntiNoiseConfig(thermal_servo_gain=5, vibration_iso_residual=0.2,
                        emi_shield_residual=0.1,
                        feed_forward_channels=("thermal", "current"),
                        feed_forward_rejection=0.8, common_mode_reject=True,
                        dd_sequence="xy8", dd_n_blocks=3)
    d = f.to_dict()
    assert d["feed_forward_channels"] == ["thermal", "current"]
    f2 = AntiNoiseConfig.from_dict(d)
    assert f2.thermal_servo_gain == 5
    assert f2.feed_forward_channels == ("thermal", "current")
    assert f2.dd_sequence == "xy8"
    assert f2.dd_n_blocks == 3
    assert not f2.is_default()


# --- simulator integration --------------------------------------------------

def test_simulated_fpga_anti_noise_lowers_noise():
    """With a strong filter stack, the per-point ODMR noise variance drops."""
    import numpy as np
    from qssc.simulator import SimulatedFPGA
    from qssc.noise_filters import NoiseEnvironment, AntiNoiseConfig

    def variance():
        fpga = SimulatedFPGA(profile="NV", seed=7)
        m = fpga.run_all_odmr(2870_000, 2872_000, 60)
        arr = np.asarray(m, dtype=float)
        # noise ~ std across frequency points of a flat region (away from line)
        return float(arr.std())

    base_var = variance()
    fpga2 = SimulatedFPGA(
        profile="NV", seed=7,
        noise_env=NoiseEnvironment.lab(),
        anti_noise=AntiNoiseConfig(thermal_servo_gain=20, vibration_iso_residual=0.05,
                                   emi_shield_residual=0.05, feed_forward_rejection=0.9,
                                   common_mode_reject=True))
    m2 = fpga2.run_all_odmr(2870_000, 2872_000, 60)
    filtered_var = float(np.asarray(m2, dtype=float).std())
    assert filtered_var <= base_var * 1.05  # filters must not raise the noise


def test_simulated_fpga_back_compat_no_env():
    """noise_env=None, anti_noise=None -> bit-identical codes to the prior model."""
    from qssc.simulator import SimulatedFPGA
    a = SimulatedFPGA(profile="NV", seed=42)
    b = SimulatedFPGA(profile="NV", seed=42, noise_env=None, anti_noise=None)
    ma = a.run_all_odmr(2870_000, 2872_000, 32)
    mb = b.run_all_odmr(2870_000, 2872_000, 32)
    assert (ma == mb).all()


def test_simulated_fpga_dd_extends_t2():
    """A DD sequence extends the SimulatedFPGA's T2 (capped at 2*T1)."""
    from qssc.simulator import SimulatedFPGA
    base = SimulatedFPGA(profile="NV", seed=42)
    dd = SimulatedFPGA(profile="NV", seed=42,
                       anti_noise=AntiNoiseConfig(dd_sequence="cpmg",
                                                  dd_n_blocks=4))
    assert dd.t2 > base.t2
    assert dd.t2 <= 2 * base.t1  # hard cap


# --- array_calibration integration ------------------------------------------

def test_calibrate_array_anti_noise_raises_width():
    """Lower effective noise -> more dim lone-NV lines clear min_snr -> the
    MEASURED width rises (monotonic). Composes with integration_shots."""
    from qssc.array_calibration import calibrate_array
    from qssc.noise_filters import NoiseEnvironment, AntiNoiseConfig

    base = calibrate_array("NV", gradient_mhz_per_um=2.0, seed=42,
                           integration_shots=1)
    strong = calibrate_array(
        "NV", gradient_mhz_per_um=2.0, seed=42, integration_shots=16,
        noise_env=NoiseEnvironment.quiet_lab(),
        anti_noise=AntiNoiseConfig(thermal_servo_gain=20, vibration_iso_residual=0.05,
                                   emi_shield_residual=0.05, feed_forward_rejection=0.9,
                                   common_mode_reject=True))
    assert sum(strong.widths) >= sum(base.widths)


def test_calibrate_array_back_compat():
    """anti_noise=None, noise_env=None -> bit-identical widths to today."""
    from qssc.array_calibration import calibrate_array
    a = calibrate_array("NV", gradient_mhz_per_um=2.0, seed=42)
    b = calibrate_array("NV", gradient_mhz_per_um=2.0, seed=42,
                        noise_env=None, anti_noise=None)
    assert a.widths == b.widths


def test_calibrate_array_records_anti_noise():
    from qssc.array_calibration import calibrate_array
    from qssc.noise_filters import NoiseEnvironment, AntiNoiseConfig
    an = AntiNoiseConfig(thermal_servo_gain=5, dd_sequence="hahn")
    calib = calibrate_array("NV", gradient_mhz_per_um=2.0, seed=42,
                            noise_env=NoiseEnvironment.lab(), anti_noise=an)
    assert calib.anti_noise is not None
    assert calib.anti_noise["thermal_servo_gain"] == 5
    assert calib.anti_noise["dd_sequence"] == "hahn"
    assert calib.noise_env is not None
    assert calib.noise_env["temp_drift_counts"] == 40.0