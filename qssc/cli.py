# qssc/cli.py
"""Command-line interface for the Quantum Solid State Chip (QSSC).

A clean, dependency-light Click CLI that exposes the simulator + Qiskit
backend: calibration, period finding, Shor factoring, arithmetic, ODMR and an
info command.  Run ``python -m qssc --help``.
"""
from __future__ import annotations

import json
import math
from fractions import Fraction

import click
import numpy as np
from rich.console import Console
from rich.table import Table

from . import __version__
from .defects import PROFILES, get_profile
from .simulator import simulate_odmr
from .backend import (
    QSSCBackend, EmitterController, DetectorInterface, MicrowaveController,
    CalibrationManager, make_connection,
)
from .circuits import (
    create_period_finding_circuit, create_math_circuit,
    is_prime, select_coprime,
)
from .protocol import protocol_summary

console = Console()


def _build_backend(profile: str, port, temperature: float, shots: int, seed: int):
    hw = make_connection(port, profile=profile, temperature_k=temperature, seed=seed)
    emitter = EmitterController(hw)
    detector = DetectorInterface(hw)
    mw = MicrowaveController(hw)
    # Register mode: the backend simulates ONE pixel's ~10-NV register; the
    # 64-pixel hardware die is its separate chip view (see QSSCBackend).
    backend = QSSCBackend(profile=profile, hardware=hw,
                          shots=shots, seed=seed, temperature_k=temperature)
    return backend, hw, (emitter, detector, mw)


def _ensure_mapping(hw, mock, emitter, detector):
    if mock:
        return CalibrationManager.default_detector_lut()
    cal = CalibrationManager(emitter, detector)
    cal.perform_calibration()
    return cal.mapping


@click.group()
@click.version_option(__version__, prog_name="qssc")
def cli():
    """Quantum Solid State Chip (room temperature) — simulator & Qiskit backend."""


@cli.command()
@click.option("--profile", default="NV", type=click.Choice(list(PROFILES)))
@click.option("--temperature", default=300.0, help="Chip temperature (K).")
def info(profile, temperature):
    """Show backend / chip description."""
    prof = get_profile(profile)
    desc = QSSCBackend(profile=prof, temperature_k=temperature).describe()
    t = Table(title=f"QSSC — {prof.name} @ {temperature} K", show_lines=True)
    t.add_column("Parameter", style="cyan")
    t.add_column("Value", style="green")
    for k in ("backend", "defect", "num_qubits", "pixels", "n_nv_per_pixel",
              "t1_ms", "t2_us", "readout_error", "d_ghz", "shots"):
        t.add_row(k, str(desc[k]))
    console.print(t)
    console.print(f"[dim]Protocol summary:[/]")
    # pass the selected profile so `qssc info --profile NV` shows the NV
    # protocol, not the hBN base (the protocol module's bare default).
    console.print_json(json.dumps(protocol_summary(profile)))


@cli.command()
@click.option("--profile", default="NV", type=click.Choice(list(PROFILES)))
@click.option("--port", default=None, help="FPGA serial port (omit for simulator).")
@click.option("--mock/--no-mock", default=True, help="Use the in-process simulator.")
@click.option("--temperature", default=300.0)
@click.option("--shots", default=1024)
@click.option("--seed", default=42)
def calibrate(profile, port, mock, temperature, shots, seed):
    """Calibrate the emitter->detector lookup table (on-chip readout)."""
    hw = make_connection(None if mock else port, profile=profile,
                         temperature_k=temperature, seed=seed)
    emitter = EmitterController(hw)
    detector = DetectorInterface(hw)
    cal = CalibrationManager(emitter, detector)
    if mock:
        cal.mapping = CalibrationManager.default_detector_lut()
    else:
        cal.perform_calibration()
    t = Table(title="Calibration LUT (emitter pixel -> detector electrode)",
              show_lines=False)
    t.add_column("Emitter (r,c)", style="cyan")
    t.add_column("Detector electrode", style="green")
    for (r, c), det in sorted(cal.mapping.items()):
        t.add_row(f"({r},{c})", str(det))
    console.print(t)


@cli.command()
@click.argument("n", type=int)
@click.option("--a", default=None, type=int, help="Base (auto if omitted).")
@click.option("--profile", default="NV", type=click.Choice(list(PROFILES)))
@click.option("--mock/--no-mock", default=True)
@click.option("--port", default=None)
@click.option("--temperature", default=300.0)
@click.option("--shots", default=2048)
@click.option("--seed", default=42)
def period(n, a, profile, mock, port, temperature, shots, seed):
    """Find the period of a mod n via quantum phase estimation."""
    if n < 2:
        raise click.BadParameter("n must be >= 2")
    rng = np.random.default_rng(seed)
    a = a or select_coprime(n, rng)
    backend, hw, _ = _build_backend(profile, None if mock else port,
                                    temperature, shots, seed)
    qc = create_period_finding_circuit(n, a)
    res = backend.run(qc, shots=shots).result()
    counts = res.get_counts()
    bs = max(counts, key=counts.get)
    phase = int(bs, 2) / 2 ** qc.num_clbits
    frac = Fraction(phase).limit_denominator(n - 1)
    r = frac.denominator
    ok = pow(a, r, n) == 1
    t = Table(title=f"Period finding — n={n}, a={a}", show_lines=True)
    for k, v in (("n", n), ("a", a), ("top phase", f"{phase:.4f}"),
                 ("fraction", str(frac)), ("period r", r), ("a^r mod n = 1", ok)):
        t.add_row(k, str(v))
    console.print(t)


@cli.command()
@click.argument("n", type=int)
@click.option("--profile", default="NV", type=click.Choice(list(PROFILES)))
@click.option("--mock/--no-mock", default=True)
@click.option("--port", default=None)
@click.option("--temperature", default=300.0)
@click.option("--shots", default=4096)
@click.option("--seed", default=42)
@click.option("--retries", default=8, help="Retry with new bases if no factor found.")
def shor(n, profile, mock, port, temperature, shots, seed, retries):
    """Factor composite n with Shor's algorithm."""
    if n < 2 or is_prime(n):
        raise click.BadParameter("n must be a composite integer > 1")
    backend, hw, _ = _build_backend(profile, None if mock else port,
                                    temperature, shots, seed)
    rng = np.random.default_rng(seed)
    found = None
    for _ in range(retries):
        a = select_coprime(n, rng)
        qc = create_period_finding_circuit(n, a)
        res = backend.run(qc, shots=shots).result()
        counts = res.get_counts()
        from .circuits import postprocess_period
        r, factors = postprocess_period(counts, qc.num_clbits, a, n)
        if factors[0] and factors[1]:
            found = (a, r, factors)
            break
    t = Table(title=f"Shor factoring — N={n}", show_lines=True)
    if found:
        a, r, (p, q) = found
        t.add_row("base a", str(a)); t.add_row("period r", str(r))
        t.add_row("factor 1", str(p)); t.add_row("factor 2", str(q))
        t.add_row("product", str(p * q))
    else:
        t.add_row("result", "no factor found (retry with --retries/--shots)")
    console.print(t)


@cli.command()
@click.argument("op", type=click.Choice(["add", "sub", "mul", "sqrt", "log"]))
@click.argument("num1", type=float)
@click.argument("num2", type=float, required=False)
@click.option("--profile", default="NV", type=click.Choice(list(PROFILES)))
@click.option("--mock/--no-mock", default=True)
@click.option("--temperature", default=300.0)
@click.option("--shots", default=2048)
@click.option("--seed", default=42)
def math(op, num1, num2, profile, mock, temperature, shots, seed):
    """Run an arithmetic circuit (add/sub/mul/sqrt/log)."""
    backend, _, _ = _build_backend(profile, None, temperature, shots, seed)
    qc = create_math_circuit(op, num1, num2)
    res = backend.run(qc, shots=shots).result()
    counts = res.get_counts()
    bs = max(counts, key=counts.get)
    out = int(bs, 2)
    if op in ("sqrt", "log"):
        out = (out / 2 ** qc.num_clbits) * num1
    t = Table(title=f"QSSC math — {op}", show_lines=True)
    t.add_row("op", op); t.add_row("num1", str(num1))
    t.add_row("num2", str(num2 if num2 is not None else "-"))
    t.add_row("result", str(out))
    console.print(t)


@cli.command()
@click.option("--profile", default="NV", type=click.Choice(list(PROFILES)))
@click.option("--b-field", default=1e-3, help="Magnetic field (T).")
@click.option("--points", default=400)
@click.option("--save", default=None, help="Save ODMR plot to this PNG path.")
def odmr(profile, b_field, points, save):
    """Simulate and plot the ODMR spectrum."""
    prof = get_profile(profile)
    f0 = prof.d_ghz
    freqs = np.linspace(f0 - 0.08, f0 + 0.08, points)
    f, c = simulate_odmr(prof, freqs, b_field_t=b_field)
    peak = f[np.argmin(c)]  # contrast dips -> minimum
    console.print(f"[green]ODMR resonance ≈ {peak:.4f} GHz "
                  f"(D = {prof.d_ghz} GHz)[/]")
    if save:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            plt.figure(figsize=(8, 5))
            plt.plot(f, c, color="#3b82f6")
            plt.axvline(prof.d_ghz, color="#ef4444", ls="--", label="D")
            plt.xlabel("Frequency (GHz)"); plt.ylabel("Fluorescence contrast")
            plt.title(f"ODMR — {prof.name}")
            plt.grid(alpha=0.3); plt.legend()
            plt.tight_layout(); plt.savefig(save, dpi=120)
            console.print(f"[dim]saved plot -> {save}[/]")
        except Exception as exc:
            console.print(f"[yellow]plot skipped: {exc}[/]")


if __name__ == "__main__":
    cli()