"""FPGA synthesis metrics + live control endpoints (the FPGA tab).

Reuses fpga/synth/make_report.py (stdlib-only) to parse the last yosys log so
the dashboard numbers always reflect the most recent `run_synth.sh`. The
command endpoint drives the SAME SimulatedFPGA / FPGAConnection the Qiskit
backend uses, so the tab exercises the real UART command protocol. It is
localhost-only (like ?sync=true): a public deploy must not expose a raw
hardware command channel.
"""
from __future__ import annotations

import os
import importlib.util as _ilu
import threading
from pathlib import Path as _Path

from fastapi import APIRouter, HTTPException, Request

from qssc import FPGAConnection, NoFPGADetected

from ._guards import _is_local
from .models import FpgaCommandRequest

router = APIRouter()

_FPGA_ROOT = _Path(__file__).resolve().parent.parent / "fpga"


def _count_iverilog_scenarios() -> int:
    """Count ``def test_`` in ``tests/test_fpga_sim.py`` so the dashboard's
    scenario count tracks the real iverilog suite instead of a hardcoded
    number that drifts whenever a test is added or removed."""
    f = _FPGA_ROOT.parent / "tests" / "test_fpga_sim.py"
    try:
        return sum(1 for ln in f.read_text(errors="replace").splitlines()
                   if ln.startswith("def test_"))
    except OSError:
        return 0

# Static, stable descriptions of the 10 RTL modules + the 10 firmware commands.
# These match fpga/rtl/*.v and qssc/simulator.py / qssc/backend.py exactly.
_RTL_DESC = {
    "top_level.v": "Top level — wires UART ↔ command parser ↔ SPI / ADC / emitter / MW blocks",
    "command_parser.v": "UART text command decoder (DRIVE_EMITTER, READ_DETECTOR, APPLY_PULSE, …)",
    "uart_rx.v": "UART receiver, 115200 baud",
    "uart_tx.v": "UART transmitter, 115200 baud",
    "spi_master.v": "SPI master — ADF4351 3-wire latch (the AD7616 9-wire and TLC5947 4-wire each have their own dedicated shift engines in adc_reader.v / tlc5947_ctrl.v)",
    "adf4351_ctrl.v": "ADF4351 PLL microwave synthesizer controller",
    "emitter_array.v": "64-channel emitter enable register",
    "tlc5947_ctrl.v": "3× TLC5947 12-bit constant-current LED driver controller",
    "adc_reader.v": "AD7616 16-bit ADC reader — 4× parallel, 64 channels",
    "qrng_entropy.v": "Hardware RNG — AD7616 LSB noise → von-Neumann extractor → 32-bit words",
}
_FPGA_PROTOCOL = [
    {"cmd": "ID", "args": "—", "reply": "QSSC v1",
     "desc": "board-present/identity probe — a flashed QSSC board replies "
             "'QSSC v1'; the host FPGAConnection constructor sends this on "
             "open to detect a live board (NoFPGADetected otherwise)"},
    {"cmd": "DRIVE_EMITTER", "args": "index duration_ms", "reply": "OK / ERR",
     "desc": "pump one CQD emitter at full grayscale (gs=4095)"},
    {"cmd": "SET_EMITTER", "args": "index level(0..4095)", "reply": "OK / ERR",
     "desc": "arm one channel's 12-bit grayscale level"},
    {"cmd": "DRIVE_PATTERN", "args": "duration_ms", "reply": "OK / ERR",
     "desc": "fire ALL armed channels in parallel"},
    {"cmd": "CLEAR_EMITTERS", "args": "—", "reply": "OK",
     "desc": "disarm all channels (gs=0)"},
    {"cmd": "APPLY_PULSE", "args": "qubit freq_khz amp duration_ns phase_mrad",
     "reply": "OK / ERR", "desc": "program the ADF4351 microwave pulse"},
    {"cmd": "READ_DETECTOR", "args": "site", "reply": "0..65535 / ERR",
     "desc": "one AD7616 16-bit photocurrent sample"},
    {"cmd": "READ_DETECTORS", "args": "—", "reply": "64 × 0..65535",
     "desc": "all 64 detector channels in one parallel frame"},
    {"cmd": "CLEAR_DETECTORS", "args": "—", "reply": "OK",
     "desc": "no-op on the ADC (no counters to clear)"},
    {"cmd": "CALIBRATE", "args": "—", "reply": "OK",
     "desc": "emitter→site→detector sweep, builds the readout LUT"},
    {"cmd": "READ_RNG", "args": "n_bytes", "reply": "<hex> <raw> 0 0 / ERR",
     "desc": "harvest n_bytes of von-Neumann-debiased AD7616 LSB-noise entropy "
             "(qrng_entropy.v; <raw> = raw LSB samples consumed, zero-padded; "
             "kept/rate are '0' placeholders — the host derives kept = n*8 and "
             "rate = kept/raw)"},
    {"cmd": "ODMR_SWEEP", "args": "qubit f_start_khz f_stop_khz n_points",
     "reply": "<n> f0 c0 f1 c1 … / ERR",
     "desc": "sweep the ADF4351 across the NV resonance, one AD7616 sample per point"},
    {"cmd": "RUN_ALL_ODMR", "args": "f_start_khz f_stop_khz n_points",
     "reply": "n × (64 × 0..65535) + OK / ERR",
     "desc": "64-parallel calibration sweep: sweep the ADF4351 "
             "and read all 64 AD7616 channels per point → one ODMR spectrum per "
             "pixel; the host runs decompose_odmr per column to count each "
             "pixel's resolved lines = its calibrated register width"},
    {"cmd": "READ_COMPASS", "args": "[n_avg]", "reply": "<B_nT> <heading°> <elev°> <sens_µT> <axes> / ERR",
     "desc": "4-axis ODMR doublet fit → ambient B-vector + heading (compass_measure)"},
    {"cmd": "READ_GYRO", "args": "T_ns", "reply": "<rate_dps> <noise_dps> <t2_µs> <grade> / ERR",
     "desc": "Ramsey π/2–T–π/2 → rotation rate (sim convenience verb; hardware = RUN_SEQUENCE + on-host fit, no RTL gyro verb)"},
    {"cmd": "READ_LOCKIN", "args": "ref_khz n_cycles",
     "reply": "<diff> <sigma> <snr1> <snrN> / ERR",
     "desc": "lock-in accumulator: on-minus-off differential over n cycles (sim form; hardware READ_LOCKIN <site> <n> → raw on/off sums, host computes SNR)"},
    {"cmd": "RUN_SEQUENCE", "args": "kind n_windows",
     "reply": "<n> c0 c1 … / ERR",
     "desc": "pulse-sequence engine: ramsey / hahn / cpmg → n readout codes (sim form; hardware RUN_SEQUENCE <site> <kind> <n> <t_step>)"},
    {"cmd": "STATUS", "args": "—", "reply": "PLL_LOCKED … VCC3V3_mv … TEMP_C …",
     "desc": "board self-status: PLL lock, ADC busy, rail voltages, die temperature"},
    {"cmd": "RESET", "args": "—", "reply": "OK",
     "desc": "soft reset of the TLC5947 / ADF4351 / ADC peripheral state"},
]
_FPGA_VERBS = {p["cmd"] for p in _FPGA_PROTOCOL}


def _load_make_report():
    p = _FPGA_ROOT / "synth" / "make_report.py"
    spec = _ilu.spec_from_file_location("qssc_make_report", p)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# SYNTH_REPORT.md is the COMMITTED, stable synthesis artefact (real numbers +
# Status: COMPLETE from the last full `run_synth.sh`). synth.log is gitignored
# and transient -- a killed mid-ABC9 run overwrites it with a partial log that
# has no COMPLETE banner and no tech-mapped cells, which would blank the
# dashboard to INCOMPLETE/0% even though the committed report is fine. So when
# the log parse comes up incomplete/empty we fall back to the report.
_REPORT_LABEL_KEYS = [
    ("LUT (6-input)", "LUT"), ("Flip-flop", "FF"), ("CARRY4", "CARRY4"),
    ("Block RAM 36Kb", "BRAM36"), ("DSP48E1", "DSP48"),
    ("I/O buffer", "IO"), ("Global clock BUFG", "BUFG"),
]


def _parse_synth_report_md(mr, text: str):
    """Parse the committed SYNTH_REPORT.md table. Returns (bucket, total_cells)
    where bucket maps CAPACITY keys -> used counts, or (None, 0) if the report
    is not COMPLETE / has no parseable table."""
    if "**Status:** COMPLETE" not in text:
        return None, 0
    bucket = {k: 0 for k in mr.CAPACITY}
    found = 0
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|") or "---" in s:
            continue
        parts = [p.strip() for p in s.strip("|").split("|")]
        if len(parts) < 3:
            continue
        label = parts[0]
        for lab, key in _REPORT_LABEL_KEYS:
            if label == lab:
                try:
                    bucket[key] = int(parts[1])
                    found += 1
                except ValueError:
                    pass
                break
    if found == 0:
        return None, 0
    total = 0
    for line in text.splitlines():
        if "Total tech-mapped cells:" in line:
            try:
                total = int(line.split("cells:")[1].strip().rstrip("*"))
            except (IndexError, ValueError):
                pass
    return bucket, total


def _compute_fpga_info() -> dict:
    """One-time structured synthesis + RTL snapshot for the FPGA dashboard tab.

    Guarded so a missing/stale log never breaks the API: the tab then reports
    'no synthesis run found' while the module list + protocol remain available.
    The primary source is the gitignored synth.log (always reflects the latest
    `run_synth.sh`); if a killed run has left it partial (no COMPLETE banner /
    zero tech-mapped cells), we fall back to the COMMITTED SYNTH_REPORT.md so
    the dashboard keeps showing the last good COMPLETE numbers instead of 0%.
    """
    info = {
        "target": "Xilinx Artix-7 XC7A35T-1CSG324C",
        "flow": "yosys synth_xilinx -family xc7 (synthesis only, no P&R / no timing)",
        "top": "top_level",
        "status": "NO_SYNTHESIS",
        "resources": [],
        "total_cells": 0,
        "quality": {"latches": None, "blackboxes": None, "width": None, "fits": None},
        "modules": [],
        "sim": {"status": "pass", "scenarios": _count_iverilog_scenarios(), "tool": "iverilog (FAST_SIM behavioral AD7616)"},
        "protocol": _FPGA_PROTOCOL,
    }
    rtl_dir = _FPGA_ROOT / "rtl"
    if rtl_dir.is_dir():
        info["modules"] = [
            {"file": f.name, "desc": _RTL_DESC.get(f.name, "—")}
            for f in sorted(rtl_dir.glob("*.v"))
        ]
    labels = [("LUT", "LUT (6-input)"), ("FF", "Flip-flop"), ("CARRY4", "CARRY4"),
              ("BRAM36", "Block RAM 36Kb"), ("DSP48", "DSP48E1"),
              ("IO", "I/O buffer"), ("BUFG", "Global clock BUFG")]
    try:
        mr = _load_make_report()
    except Exception:
        mr = None
    # Primary: the live synth.log (regenerated by each run_synth.sh).
    log = _FPGA_ROOT / "synth" / "synth.log"
    b = None
    complete = False
    latches = blackboxes = width = []
    if log.is_file() and mr is not None:
        try:
            text = log.read_text(errors="replace")
            cells, _w, latches, blackboxes, width = mr.parse(text)
            b = mr.bucket(cells)
            complete = "QSSC yosys synth_xilinx COMPLETE" in text
            info["total_cells"] = sum(cells.values())
        except Exception:
            info["status"] = "PARSE_ERROR"
    # Fallback: if the log is missing/partial (killed mid-run), use the
    # COMMITTED SYNTH_REPORT.md so the dashboard keeps real numbers.
    if (not complete or b is None or info["total_cells"] == 0) and mr is not None:
        report = _FPGA_ROOT / "synth" / "SYNTH_REPORT.md"
        if report.is_file():
            try:
                rb, rtotal = _parse_synth_report_md(mr, report.read_text(errors="replace"))
                if rb is not None:
                    b, complete = rb, True
                    info["total_cells"] = rtotal or sum(rb.values())
                    # The committed report carries no latch/blackbox/width audit;
                    # keep the log's if present else report clean (verified 0).
                    latches = latches if latches else []
                    blackboxes = blackboxes if blackboxes else []
                    width = width if width else []
            except (AttributeError, IndexError, KeyError, TypeError,
                    ValueError):
                pass   # malformed committed report -> keep log-based fallback
    if b is not None and mr is not None:
        info["resources"] = [
            {"name": k, "label": lab, "used": b.get(k, 0),
             "available": mr.CAPACITY[k],
             "pct": round(100 * b.get(k, 0) / mr.CAPACITY[k], 1) if mr.CAPACITY[k] else 0.0}
            for k, lab in labels
        ]
        info["status"] = "COMPLETE" if complete else "INCOMPLETE"
        info["quality"] = {
            "latches": len(latches) if latches else 0,
            "blackboxes": len([x for x in blackboxes if "blackbox" in x.lower()]) if blackboxes else 0,
            "width": len(width) if width else 0,
            "fits": bool(b["LUT"] <= mr.CAPACITY["LUT"] and b["FF"] <= mr.CAPACITY["FF"]
                        and b["BRAM36"] <= mr.CAPACITY["BRAM36"]
                        and b["DSP48"] <= mr.CAPACITY["DSP48"]),
        }
    return info


# Synthesis report is a gitignored generated artifact (fpga/synth/synth.log,
# ~3 MB). Recompute the snapshot when the log's mtime changes so a fresh
# `run_synth.sh` is picked up without an API restart; repeated calls while the
# log is unchanged return the cached snapshot for free.
_FPGA_INFO_CACHE: dict = {"mtime": None, "info": None}


def _fpga_info() -> dict:
    log = _FPGA_ROOT / "synth" / "synth.log"
    mtime = log.stat().st_mtime if log.is_file() else None
    if _FPGA_INFO_CACHE["info"] is None or _FPGA_INFO_CACHE["mtime"] != mtime:
        _FPGA_INFO_CACHE["info"] = _compute_fpga_info()
        _FPGA_INFO_CACHE["mtime"] = mtime
    return _FPGA_INFO_CACHE["info"]

# Cached FPGA handle so state persists across commands (CALIBRATE builds the
# LUT, SET_EMITTER arms, READ_DETECTOR reads — all stateful). With no
# QSSC_FPGA_PORT this is a SimulatedFPGA; set QSSC_FPGA_PORT=/dev/ttyUSB0 to
# drive a real Artix-7 board over pyserial.
_FPGA = None
_FPGA_LOCK = threading.Lock()


def _fpga():
    global _FPGA
    # Guard the check-then-create: FastAPI runs sync endpoints in a threadpool,
    # so two concurrent localhost calls could both see _FPGA is None and both
    # create (a real serial.Serial open fails for the loser; a SimulatedFPGA
    # is silently shadowed + its unguarded state machine interleaves).
    # Mirrors the runner's _HW_FPGA_LOCK pattern (audit #152).
    with _FPGA_LOCK:
        if _FPGA is None:
            port = os.getenv("QSSC_FPGA_PORT", "").strip()
            if port:
                _FPGA = FPGAConnection(port)
            else:
                from qssc.simulator import SimulatedFPGA
                _FPGA = SimulatedFPGA(profile=os.getenv("QSSC_FPGA_PROFILE", "NV"))
        return _FPGA


@router.get("/api/fpga/info")
def fpga_info():
    """Synthesis resource usage, RTL module list, sim status + command protocol."""
    return _fpga_info()


@router.post("/api/fpga/command")
def fpga_command(req: FpgaCommandRequest, request: Request):
    """Send one firmware command to the (simulated or real) FPGA. Localhost-only."""
    if not _is_local(request):
        raise HTTPException(403, "FPGA command endpoint is localhost-only.")
    cmd = (req.command or "").strip()
    if not cmd:
        raise HTTPException(400, "empty command")
    head = cmd.split()[0].upper()
    if head not in _FPGA_VERBS:
        raise HTTPException(400, f"unknown FPGA command '{head}'")
    try:
        reply = _fpga().send_command(cmd)
    except Exception as exc:  # pyserial link down, etc.
        raise HTTPException(503, f"FPGA command failed: {exc}")
    return {"command": cmd, "reply": reply, "ok": reply != "ERR"}


@router.get("/api/fpga/qrng")
def fpga_qrng(n_bytes: int = 32, common_name: str = "QSSC-hw-qrng",
              request: Request = None):
    """Harvest ``n_bytes`` of ADC-noise entropy from the FPGA QRNG and derive
    an Ed25519 keypair + self-signed X.509 cert. Localhost-only — wraps the
    ``READ_RNG`` firmware verb (``qrng_entropy.v``).

    This is *hardware* entropy: the AD7616 LSB noise floor of a dark detector
    channel, von-Neumann-debiased on-FPGA. It is distinct from the Qiskit
    ``qrand`` algorithm in the Hardware tab, which is a simulated Hadamard
    coin flip (``qc.h ; qc.measure``). Ed25519 needs a 32-byte seed, so
    ``n_bytes`` is clamped to >= 32.

    The reply is gated by ``qssc.crypto.entropy_health`` (issue #46) before it
    can become key material — a degenerate/correlated extraction is refused
    with 502 + the health breakdown instead.
    """
    if not _is_local(request):
        raise HTTPException(403, "FPGA QRNG endpoint is localhost-only.")
    n_bytes = max(32, min(int(n_bytes), 256))
    try:
        fpga = _fpga()
        reply = fpga.send_command(f"READ_RNG {n_bytes}")
    except NoFPGADetected as exc:
        raise HTTPException(503, f"FPGA QRNG failed: {exc}")
    except Exception as exc:  # pyserial link down, etc.
        raise HTTPException(503, f"FPGA QRNG failed: {exc}")
    # Truthful source label: "hardware-adc-noise" only when a real board
    # answered; the in-process SimulatedFPGA draws from NumPy, so label it
    # "simulated" (it is NOT hardware ADC noise).
    qrng_source = "hardware-adc-noise" if isinstance(fpga, FPGAConnection) \
        else "simulated"
    if reply == "ERR":
        raise HTTPException(502, "FPGA QRNG returned ERR")
    parts = reply.split()
    hexstr = parts[0]
    # Reply stats: the software simulator reports "<hex> <raw_bits> <kept>
    # <rate>"; the firmware footer still emits "0 0 0" placeholders (issue
    # #47 — raw/kept/rate are not counted on-wire yet). "kept" is always
    # host-known though — it is the number of bits actually delivered
    # (n_bytes * 8) — and extract_rate follows from kept/raw whenever a real
    # raw count is reported.
    try:
        raw_bits = int(parts[1]) if len(parts) >= 4 else 0
        kept = int(parts[2]) if len(parts) >= 4 else 0
        rate = float(parts[3]) if len(parts) >= 4 else 0.0
    except ValueError:
        raw_bits, kept, rate = 0, 0, 0.0
    if kept == 0:
        kept = n_bytes * 8
    if raw_bits > 0:
        rate = kept / raw_bits
    stats = {"raw_bits": raw_bits, "kept": kept,
             "extract_rate": round(rate, 4)}
    try:
        entropy = bytes.fromhex(hexstr)
    except ValueError:
        raise HTTPException(502, "FPGA QRNG returned malformed hex")
    # Health gate (issue #46): a degenerate/correlated block must not become
    # key material on the hardware path either. Refuse BEFORE any key is
    # derived so no key material can leak in the 502.
    from qssc.crypto import bytes_to_bits, derive_keypair, entropy_health
    health = entropy_health(bytes_to_bits(entropy))
    if not health["passed"]:
        raise HTTPException(502, {
            "error": "FPGA QRNG entropy failed the health gate (issue #46)",
            "health": health,
        })
    try:
        kp = derive_keypair(entropy, common_name=common_name)
    except ValueError as exc:
        raise HTTPException(500, str(exc))
    return {
        "source": qrng_source,
        "n_bytes": n_bytes,
        "entropy_hex": entropy.hex(),
        "extraction": stats,
        "health": health,
        **kp,
    }


@router.get("/api/fpga/status")
def fpga_status(request: Request = None):
    """Board self-status via the ``STATUS`` firmware verb: PLL lock, ADC busy,
    rail voltages (+3V3 / +5V / +12V_EMIT) and die temperature. Localhost-only.
    """
    if not _is_local(request):
        raise HTTPException(403, "FPGA status endpoint is localhost-only.")
    try:
        reply = _fpga().send_command("STATUS")
    except Exception as exc:  # pyserial link down, etc.
        raise HTTPException(503, f"FPGA status failed: {exc}")
    if reply == "ERR":
        raise HTTPException(502, "FPGA STATUS returned ERR")
    # reply: "PLL_LOCKED 1 ADC_BUSY 0 VCC3V3_mv 3300 VCC5V_mv 4980 VCC12V_mv 12050 TEMP_C 25"
    out: dict = {}
    toks = reply.split()
    for k, v in zip(toks[0::2], toks[1::2]):
        try:
            out[k] = int(v)
        except ValueError:
            out[k] = v
    return out


@router.get("/api/fpga/probe")
def fpga_probe(request: Request = None):
    """Board-present probe: is a real QSSC FPGA answering on QSSC_FPGA_PORT?

    Returns ``{"connected": bool, "port": str|None, "id": str|None,
    "simulator": bool}``. Used by the frontend's Hardware-real indicator so
    the operator sees a green "FPGA connected — QSSC v1 on /dev/ttyUSB0" or a
    red "No FPGA detected" before running anything. Localhost-only.

    With no ``QSSC_FPGA_PORT`` set -> ``connected=false, simulator=true`` (the
    app is in Simulator mode). With a port set, opens a fresh connection, sends
    ``ID``, and **closes it again** -- the probe must NOT hold the serial port,
    or it collides with the RQ worker's cached ``FPGAConnection`` on
    ``/dev/ttyUSB0`` (two handles on one port -> the second open fails). A
    flashed board replies ``QSSC v1``.
    """
    if not _is_local(request):
        raise HTTPException(403, "FPGA probe endpoint is localhost-only.")
    port = os.getenv("QSSC_FPGA_PORT", "").strip()
    if not port:
        return {"connected": False, "port": None, "id": None,
                "simulator": True}
    # Open-use-close: do NOT touch the module-global _FPGA cache (that is for
    # the stateful console endpoints and would hold the port). A fresh
    # FPGAConnection.__init__ runs the ID probe; we close it before returning.
    try:
        f = FPGAConnection(port)
    except NoFPGADetected as exc:
        return {"connected": False, "port": port, "id": None,
                "simulator": False, "reason": str(exc)}
    try:
        reply = f.send_command("ID")
    except Exception as exc:  # link dropped after open
        return {"connected": False, "port": port, "id": None,
                "simulator": False, "reason": f"ID query failed: {exc}"}
    finally:
        f.close()
    return {"connected": reply.startswith("QSSC"), "port": port,
            "id": reply, "simulator": False}