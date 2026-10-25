# Power and clock trees

Where every rail and every clock comes from. The power rails are split
across both boards — the head's analog front-end gets its own filtered
rail — and the single clock source fans out once as the FPGA system
clock and once, divided, as the ADF4351 reference.

```mermaid
flowchart TD
    subgraph MAIN["Main board"]
        J2["J2 barrel 5V / J10 USB-C"] --> F1["F1 PTC 2A"] --> V5["+5V0"]
        J4["J4 barrel 12V"] --> F2["F2 PTC 1A"] --> V12["+12V_EMIT"]
        V5 --> REG1["REG1 LDO 3.3V"] --> V33["+3V3 (FPGA IO, FT232RL)"]
        V33 --> REG2["REG2 1.8V"] --> V18["+1V8 (FPGA core)"]
        V33 --> REG3["REG3 AP2127K-1.0"] --> V10["+1V0 (FPGA core)"]
        V5 --> FB1["FB1 ferrite"] --> V5RF["+5V_RF (ADF4351, GVA-84+)"]
        V12 --> AN["EM_ANODE (common anode, 64 emitters)"]
    end
    subgraph HEAD["Detector head (via J12)"]
        V5H["+5V in"] --> FBA["FB_A ferrite"] --> V5A["+5V_A (TIA, ADC)"]
    end
    subgraph CLK["Clock tree"]
        Y1["Y1 TCXO 100 MHz"] --> U1["U1 FPGA"]
        U1 -- "refclk 25 MHz" --> U3["U3 ADF4351 PFD"]
        U3 --> RF["RF_OUT 2.87 / 3.46 GHz"]
    end
    V5H -. "from +5V0 via J12" .-> V5
```

- Two separate inputs (5 V logic + 12 V emitter rail) by design — see
  ADR-0004 for why the emitter anode was raised.
- RF and analog rails sit behind ferrite beads (`FB1`, `FB_A`) to keep
  switching noise out of the PLL and the TIA domain.
- The ADF4351 loop filter (R67/R68, C31–C33) and the 25 MHz reference
  derivation are shown as nets in `HARDWARE_DESIGN.md` §1.