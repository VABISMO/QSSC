# QSSC FPGA — yosys synthesis report

- **Target:** Xilinx Artix-7 XC7A35T-1CSG324C (QSSC single-board control FPGA)
- **Flow:** `yosys synth_xilinx -family xc7 -abc9` (synthesis only, **no P&R / no timing**; -abc9 pinned — see synth_yosys.ys)
- **Top module:** `top_level`
- **Status:** COMPLETE

## Resource usage vs XC7A35T-1CSG324C capacity

| Resource | Used | Available | Utilisation |
|---|---:|---:|---:|
| LUT (6-input) | 4091 | 20800 | 19.7% |
| Flip-flop | 3998 | 41600 | 9.6% |
| CARRY4 | 668 | 5200 | 12.8% |
| Block RAM 36Kb | 0 | 50 | 0.0% |
| DSP48E1 | 7 | 90 | 7.8% |
| I/O buffer | 24 | 250 | 9.6% |
| Global clock BUFG | 1 | 32 | 3.1% |

*Total tech-mapped cells: 9093*

## Cell breakdown (top categories)

| Cell | Count |
|---|---:|
| `FDRE` | 3960 |
| `LUT3` | 1920 |
| `LUT2` | 787 |
| `CARRY4` | 668 |
| `LUT5` | 573 |
| `LUT6` | 528 |
| `LUT4` | 283 |
| `INV` | 252 |
| `FDSE` | 36 |
| `MUXF7` | 34 |
| `OBUF` | 15 |
| `MUXF8` | 13 |
| `DSP48E1` | 7 |
| `IBUF` | 5 |
| `RAM64M` | 5 |
| `IOBUF` | 4 |
| `FDPE` | 2 |
| `BUFG` | 1 |

## Logic-quality checks

- **Inferred latches:** 0 ✅ none
- **Blackbox / unmapped cells:** 0 ✅ none
- **Width / driver warnings:** 0 ✅ none
- **Fits XC7A35T:** ✅ yes (synthesis estimate)

## What this is / what it is not

- ✅ **Validates:** the RTL is synthesizable (no inferred latches, no blackboxes, no width mismatches), maps cleanly to the 7-series cell library, and the resource estimate fits the XC7A35T with large margin.
- ❌ **Does NOT validate:** timing closure (no post-P&R delays), actual pin routing, clock-tree insertion, or bitstream generation. Those require Xilinx Vivado, which is not available in this environment. The iverilog testbench (`fpga/sim`) covers **functional** correctness; this yosys pass covers **synthesizability + resource fit**.
- The 64 detector channels are digitised fully in parallel by 4x AD7616 16-bit ADCs sharing one 9-wire SPI bus (`adc_reader.v`, no analog MUX, no XADC, no comparator/threshold DAC). The 64 CQD emitters are driven in parallel by 3x TLC5947 constant-current sinks (`tlc5947_ctrl.v`); `qrng_entropy.v` von-Neumann-debiases a dark-channel LSB stream for the `READ_RNG` verb (HARDWARE_DESIGN.md §15E).
