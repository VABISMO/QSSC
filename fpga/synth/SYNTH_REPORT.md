# QSSC FPGA — yosys synthesis report

- **Target:** Xilinx Artix-7 XC7A35T-1CSG324C (QSSC single-board control FPGA)
- **Flow:** `yosys synth_xilinx -family xc7 -abc9` (synthesis only, **no P&R / no timing**; -abc9 pinned — see synth_yosys.ys)
- **Top module:** `top_level`
- **Status:** COMPLETE

## Resource usage vs XC7A35T-1CSG324C capacity

| Resource | Used | Available | Utilisation |
|---|---:|---:|---:|
| LUT (6-input) | 8377 | 20800 | 40.3% |
| Flip-flop | 7442 | 41600 | 17.9% |
| CARRY4 | 1313 | 5200 | 25.2% |
| Block RAM 36Kb | 0 | 50 | 0.0% |
| DSP48E1 | 9 | 90 | 10.0% |
| I/O buffer | 28 | 250 | 11.2% |
| Global clock BUFG | 1 | 32 | 3.1% |

*Total tech-mapped cells: 18528*

## Cell breakdown (top categories)

| Cell | Count |
|---|---:|
| `FDRE` | 7386 |
| `LUT3` | 2729 |
| `LUT2` | 1544 |
| `LUT5` | 1471 |
| `LUT4` | 1327 |
| `CARRY4` | 1313 |
| `LUT6` | 1306 |
| `INV` | 1261 |
| `MUXF7` | 77 |
| `FDSE` | 54 |
| `OBUF` | 19 |
| `MUXF8` | 15 |
| `DSP48E1` | 9 |
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

### Blackbox / unmapped
```
Replacing existing blackbox module `\ODDR2' at ~/oss-cad-suite/lib/../share/yosys/xilinx/cells_xtra.v:6127.1-6141.10.
```

## What this is / what it is not

- ✅ **Validates:** the RTL is synthesizable (no inferred latches, no blackboxes, no width mismatches), maps cleanly to the 7-series cell library, and the resource estimate fits the XC7A35T with large margin.
- ❌ **Does NOT validate:** timing closure (no post-P&R delays), actual pin routing, clock-tree insertion, or bitstream generation. Those require Xilinx Vivado, which is not available in this environment. The iverilog testbench (`fpga/sim`) covers **functional** correctness; this yosys pass covers **synthesizability + resource fit**.
- The 64 detector channels are digitised fully in parallel by 4x AD7616 16-bit ADCs sharing one 9-wire SPI bus (`adc_reader.v`, no analog MUX, no XADC, no comparator/threshold DAC). The 64 CQD emitters are driven in parallel by 3x TLC5947 constant-current sinks (`tlc5947_ctrl.v`); `qrng_entropy.v` von-Neumann-debiases a dark-channel LSB stream for the `READ_RNG` verb (HARDWARE_DESIGN.md §15E).
