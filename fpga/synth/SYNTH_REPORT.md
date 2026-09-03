# QSSC FPGA — yosys synthesis report

- **Target:** Xilinx Artix-7 XC7A35T-1CSG324C (QSSC single-board control FPGA)
- **Flow:** `yosys synth_xilinx -family xc7` (synthesis only, **no P&R / no timing**)
- **Top module:** `top_level`
- **Status:** COMPLETE

## Resource usage vs XC7A35T-1CSG324C capacity

| Resource | Used | Available | Utilisation |
|---|---:|---:|---:|
| LUT (6-input) | 2762 | 20800 | 13.3% |
| Flip-flop | 2167 | 41600 | 5.2% |
| CARRY4 | 822 | 5200 | 15.8% |
| Block RAM 36Kb | 0 | 50 | 0.0% |
| DSP48E1 | 7 | 90 | 7.8% |
| I/O buffer | 141 | 250 | 56.4% |
| Global clock BUFG | 1 | 32 | 3.1% |

*Total tech-mapped cells: 6239*

## Cell breakdown (top categories)

| Cell | Count |
|---|---:|
| `FDRE` | 2134 |
| `LUT3` | 995 |
| `CARRY4` | 822 |
| `LUT2` | 582 |
| `LUT5` | 514 |
| `LUT6` | 497 |
| `INV` | 287 |
| `LUT4` | 174 |
| `OBUF` | 68 |
| `IBUF` | 67 |
| `MUXF7` | 34 |
| `FDSE` | 31 |
| `MUXF8` | 13 |
| `DSP48E1` | 7 |
| `IOBUF` | 6 |
| `RAM64M` | 5 |
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
- The 64 detector channels are read fully in parallel: each `det_in[i]` LVCMOS input feeds a 16-bit fabric counter (no analog MUX, no XADC). A MCP4725 DAC sets the board comparator threshold over I2C (`SET_THRESHOLD`); the 64 emitters are driven individually (`emitter_en[63:0]`, no 8x8 matrix).
