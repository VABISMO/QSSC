# System overview

End-to-end view spanning all four layers the existing diagrams split
across three files: host software, FPGA, boards, and the optical stack.
The host never touches hardware directly — everything flows through one
USB-UART link into the FPGA, which is the only SPI/I2C-style master on
every peripheral bus.

```mermaid
flowchart LR
    subgraph HOST["Host PC"]
        CLI["qssc CLI + Qiskit\nsim/analysis"]
        API["REST API /api/protocol\n+ dashboard"]
    end
    subgraph FPGA["Artix-7 XC7A35T CSG324 (main board)"]
        UART["uart_rx / uart_tx\n115200-8-N-1"]
        PAR["command_parser"]
        RTL["adf4351_ctrl · tlc5947_ctrl\nadc_reader · emitter_array"]
    end
    subgraph MB["Main board (6-layer HDI, below die)"]
        FT["FT232RL USB-UART"]
        ADF["ADF4351 PLL + GVA-84+\n→ 2.87 / 3.46 GHz"]
        TLC["3× TLC5947\n64 emitter ch"]
        SOC["Die socket\n(2.54 mm through-hole)"]
    end
    subgraph HB["Detector head (6-layer, above die)"]
        TIA["16× TLV9004 TIA\nRf 1M · Cf 1p"]
        ADC["4× AD7616\n16-ch 16-bit"]
    end
    subgraph DIE["QSSC die (sandwiched)"]
        EM["64 CQD emitters\n(bottom face)"]
        DET["64 graphene/MoS₂\ndetectors (top face)"]
    end
    OPT["Pump → host\n→ PL 637–800 nm → 650 nm LP"]

    CLI -- "USB / J1" --> FT --> UART
    API --- CLI
    UART --> PAR --> RTL
    RTL -- "SPI + refclk 25 MHz" --> ADF -- "RF_OUT 0 dBm" --> OPT
    RTL -- "em_sclk/sin/xlat/blank" --> TLC -- "emitter_cat[63:0]" --> EM
    EM --> OPT --> DET
    DET -- "DET_IN[63:0]" --> TIA --> ADC
    ADC -- "9-wire SPI via J12" --> RTL
```

- Physical arrangement of boards and die: `HARDWARE_DESIGN.md` §1B (stack
  cross-section) and ADR-0003.
- Host-side control and simulation: `qssc/` package, `HARDWARE_DESIGN.md` §10.