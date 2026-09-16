# FPGA RTL module graph

The nine modules of `fpga/rtl/` and their wiring — the structural view
behind the port list in `HARDWARE_DESIGN.md` §1A. All modules share the
100 MHz TCXO clock (`Y1`); the design deliberately avoids any Xilinx
hard-ADC dependency (ADR-0002), so the graph ports to any 7-series part
via `constraints.xdc` alone (ADR-0005).

```mermaid
flowchart TB
    Y1["Y1 TCXO 100 MHz"] --> TOP
    subgraph TOP["top_level.v"]
        URX["uart_rx"]
        CP["command_parser"]
        UTX["uart_tx"]
        SM["spi_master"]
        AC["adf4351_ctrl"]
        TC["tlc5947_ctrl"]
        AR["adc_reader"]
        EA["emitter_array"]
    end
    URX --> CP
    CP --> AC & TC & AR & EA
    AC --> SM
    CP --> UTX

    SM -- "SPI LE/CLK/DATA" --> PLL["ADF4351 (main board)"]
    AC -- "refclk 25 MHz" --> PLL
    TC -- "em_sclk/sin/xlat/blank" --> TLC["3× TLC5947 daisy"]
    AR -- "adc_convst/sclk/miso<br/>busy/resetn/cs0..3" --> ADC["4× AD7616 via J12"]
    EA -- "64 enables" --> EM["emitter pattern"]

    AR --> UTX
    UTX -- "115200-8-N-1" --> FT["FT232RL → host"]
```

- `spi_master` is the shared SPI engine; `adf4351_ctrl` frames the
  frequency-register writes on top of it.
- `emitter_array` holds the 64-channel pattern state that
  `tlc5947_ctrl` shifts out as 12-bit PWM words.
- `adc_reader` owns the full AD7616 sequencer protocol (see
  `readout-chain.md`).