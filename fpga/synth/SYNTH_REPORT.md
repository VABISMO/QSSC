# QSSC FPGA — yosys synthesis report

- **Target:** Xilinx Artix-7 XC7A35T-1CPG236C (QSSC control FPGA)
- **Flow:** `yosys synth_xilinx -family xc7` (synthesis only, **no P&R / no timing**)
- **Top module:** `top_level`
- **Status:** COMPLETE

## Resource usage vs XC7A35T-1CPG236C capacity

| Resource | Used | Available | Utilisation |
|---|---:|---:|---:|
| LUT (6-input) | 2063 | 20800 | 9.9% |
| Flip-flop | 1016 | 41600 | 2.4% |
| CARRY4 | 554 | 5200 | 10.7% |
| Block RAM 36Kb | 0 | 50 | 0.0% |
| DSP48E1 | 8 | 90 | 8.9% |
| I/O buffer | 33 | 250 | 13.2% |
| Global clock BUFG | 1 | 32 | 3.1% |

*Total tech-mapped cells: 3960*

## Cell breakdown (top categories)

| Cell | Count |
|---|---:|
| `FDRE` | 974 |
| `LUT3` | 922 |
| `CARRY4` | 554 |
| `LUT2` | 474 |
| `LUT6` | 249 |
| `LUT5` | 247 |
| `INV` | 233 |
| `LUT4` | 171 |
| `FDSE` | 40 |
| `MUXF7` | 34 |
| `OBUF` | 26 |
| `MUXF8` | 13 |
| `DSP48E1` | 8 |
| `RAM64M` | 5 |
| `IOBUF` | 4 |
| `IBUF` | 3 |
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
- The XADC is modelled behaviourally (`xadc_behavioral` stub) — on silicon it is the fixed-package Artix-7 hard block, instantiated via the Xilinx XADC wizard in Vivado.
