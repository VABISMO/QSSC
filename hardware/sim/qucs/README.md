# Main board analog validation — qucsator vs ngspice

Pre-registered cross-engine validation of the analog sections of the main
QSSC board (`hardware/pcb/qssc.kicad_sch`), modelled from the netlist
export (126 components, 172 nets). Two independent engines simulate the
same idealised decks; agreement between them validates the harness, the
agreement with pre-registered bands validates the documentation claims.

## Scope

| Section | Circuit | Analyses |
|---------|---------|----------|
| LF loop filter | ADF4351 CP -> C31 220p -> R67 1k -> C32 10n -> R68 2.2k -> C33 220p | AC 100 Hz..100 MHz (Zt), TR 0..2 ms (pulse + step) |
| RF output chain | U3.12 -> C34 100p -> GVA-84+ (16 dB) -> L2 100n shunt / C35 100p -> J3 50 ohm, D3 ESD5V0 shunt | SP 2.4..3.6 GHz (4 variants: no ESD, 0.3 pF, 1 pF, full amp chain) |

## Running

```sh
export QUCSATOR=/path/to/qucsator_rf     # Qucs-S AppImage qucsator_rf 1.0.7
export NGSPICE=/path/to/ngspice          # ngspice-47
python3 sim_qucs_main.py
```

Writes decks and engine outputs into `_build/` (gitignored) and the verdict
JSON next to the script. Total runtime is under a second. The driver is
self-contained: all component values, assumptions, pre-registered checks
and deck builders are in `sim_qucs_main.py`.

## Results (`qucs_main_results.json`)

6 pass, 1 documented deviation, 2 voided checks, 0 fail, 0 finding.

- **AC-1 pass** — loop filter |Zt| agrees within 0.6 % across engines at
  all probe frequencies.
- **AC-2 deviation** — derived open-loop unity-gain crossing is
  **17.17 kHz (qucsator) / 17.15 kHz (ngspice)**, outside the
  pre-registered [30, 80] kHz band around the section 6 "~50 kHz" loop
  bandwidth claim. The two engines agree to 0.1 %, so this is a
  documentation-consistency finding, not a simulation failure: with the
  stated assumptions (Kphi = 2.19 mA/2pi, Kvco = 40 MHz/V, N = 114.8) the
  ladder of C31/R67/C32/R68/C33 yields 17.2 kHz. Reaching 50 kHz would
  need Kvco ~ 116 MHz/V, outside the ADF4351 range. Section 6's "~50 kHz"
  should be revised or its assumptions restated.
- **TR-1 pass** — CP pulse response peaks at 3.97 mV (qucsator) / 4.40 mV
  (ngspice) at ~6 us (charge redistribution through the ladder; C33
  shunts the output during the 20 ns pulse itself). Delta 0.43 mV.
- **TR-2/TR-3 void** — the pre-registered 7.008 V DC step expectation has
  no valid DC path in the deck (C33 blocks the ladder; only the 10 Mohm
  bleed path remains, DC asymptote 21.9 kV, bleed time constant far
  beyond the 2 ms window). Voided as bad checks rather than redesigned
  mid-run; see the `amendment` entries in `sim_qucs_main.py`.
- **SP-A pass** — passive output chain: IL -0.0003 dB at 2.87 GHz,
  S11 -41.6 dB, cross-engine agreement 0.0002 dB.
- **SP-B-03 pass** — 0.3 pF ESD junction cap costs only 0.07 dB.
- **SP-B-10 pass** — even a generic 1 pF ESD cap stays at 0.78 dB
  (threshold for the "verify ESD variant before fab" finding is 3 dB);
  the ESD diode does not materially load the RF path at these values.
- **SP-C pass** — full chain with the GVA-84+ at 16 dB:
  **|S21| = 15.930 dB (qucsator) = 15.930 dB (ngspice)**, S11 -45.12 dB
  both. Chain loss above the bare amp is 0.07 dB.

Cross-engine agreement in all SP cases is at the 0.0001..0.0003 dB level
(9-decimal agreement on raw S-parameters), which validates the harness
extraction math against two independent code paths.

## Engine-specific findings (kept as ledger entries (l)/(m))

These cost a debugging iteration each and are recorded here so the next
deck author does not rediscover them:

- qucsator `Ipulse` `T2` is the **absolute pulse stop time**, not the
  width (width = T2 - T1). Verified via plateau charge: T1=10n/T2=20n
  delivered 10 ns of current.
- The qucsator `Amp` device parameter `G` is a **linear voltage gain**,
  not dB (G=16 produced 24.01 dB). 16 dB -> G = 6.3096.
- ngspice `wrdata` repeats the x-variable per plotted quantity: AC rows
  are `(f, re, im)` triplets per variable, transient rows are
  `(t, v)` pairs per variable.
- A Thevenin (VCVS + 50 ohm) amplifier model delivers `b2 = V_oc/2` into
  a matched load, while the qucsator `Amp` device delivers `|S21| = G`
  directly; the ngspice deck uses 2x the wave gain to stay
  wave-equivalent.
- qucsator accepts neither the `meg` suffix (`10e6` works) nor a bare
  `s`-suffixed value in `Ipulse`.

## Assumptions

Ledger entries (a)..(m) in `sim_qucs_main.py`: netlist component values;
ideal caps; ideal CP; Kphi = Icp/(2pi); Kvco = 40 MHz/V; N = 114.8; D3
modelled as pure junction cap (0.3 pF and 1 pF variants); +5V_RF as AC
ground; unilateral ideal GVA-84+ (16 dB, 50 ohm, S12=0); ngspice as the
independent engine; 10 Mohm bleed at the CP node in both engines for the
DC operating point; Ipulse T2 stop-time semantics; Amp G linear
semantics.

Not modelled: ADF4351 CP output impedance and switching transients, CP
negative-output behaviour, VCO tuning-port input impedance (this is the
real board's DC path for the step case, see TR-2 void), amplifier NF and
OIP3, PCB parasitics.