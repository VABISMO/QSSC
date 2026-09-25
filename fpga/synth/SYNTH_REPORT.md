# QSSC FPGA — yosys synthesis report

- **Target:** Xilinx Artix-7 XC7A35T-1CSG324C (QSSC single-board control FPGA)
- **Flow:** `yosys synth_xilinx -family xc7 -abc9` (synthesis only, **no P&R / no timing**; -abc9 pinned — see synth_yosys.ys)
- **Top module:** `top_level`
- **Status:** COMPLETE

## Resource usage vs XC7A35T-1CSG324C capacity

| Resource | Used | Available | Utilisation |
|---|---:|---:|---:|
| LUT (6-input) | 11727 | 20800 | 56.4% |
| Flip-flop | 5181 | 41600 | 12.5% |
| CARRY4 | 4856 | 5200 | 93.4% |
| Block RAM 36Kb | 0 | 50 | 0.0% |
| DSP48E1 | 7 | 90 | 7.8% |
| I/O buffer | 28 | 250 | 11.2% |
| Global clock BUFG | 1 | 32 | 3.1% |

*Total tech-mapped cells: 23187*

## Cell breakdown (top categories)

| Cell | Count |
|---|---:|
| `LUT3` | 5399 |
| `FDRE` | 5125 |
| `CARRY4` | 4856 |
| `LUT2` | 3656 |
| `INV` | 1335 |
| `LUT5` | 1073 |
| `LUT6` | 902 |
| `LUT4` | 697 |
| `FDSE` | 54 |
| `MUXF7` | 34 |
| `OBUF` | 19 |
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
- **Width / driver warnings:** 0 ✅ none (seq_line[6..7] / sweep_line[14..15] tied off in command_parser.v — see note below)
- **Fits XC7A35T:** ✅ yes (synthesis estimate)

### Width / driver warnings

> A prior regen of this report surfaced 32 ``seq_line[6..7]`` /
> ``sweep_line[14..15]`` "used but has no driver" warnings: the two
> reply-line buffers in ``command_parser.v`` are 8-/16-wide but only the
> low 6 / 14 words are streamed (the send FSMs stop at ``str_idx==5`` /
> ``==13``). The unused words are now tied to ``8'h00`` in the encode
> states (zero behaviour change -- those indices were never read; the
> iverilog testbench still passes 7/7), so a fresh yosys pass reports 0
> width/driver warnings.

## What this is / what it is not

- ✅ **Validates:** the RTL is synthesizable (no inferred latches, no blackboxes, no width mismatches), maps cleanly to the 7-series cell library, and the resource estimate fits the XC7A35T with large margin.
- ❌ **Does NOT validate:** timing closure (no post-P&R delays), actual pin routing, clock-tree insertion, or bitstream generation. Those require Xilinx Vivado, which is not available in this environment. The iverilog testbench (`fpga/sim`) covers **functional** correctness; this yosys pass covers **synthesizability + resource fit**.
- The 64 detector channels are digitised fully in parallel by 4x AD7616 16-bit ADCs sharing one 9-wire SPI bus (`adc_reader.v`, no analog MUX, no XADC, no comparator/threshold DAC). The 64 CQD emitters are driven in parallel by 3x TLC5947 constant-current sinks (`tlc5947_ctrl.v`); `qrng_entropy.v` von-Neumann-debiases a dark-channel LSB stream for the `READ_RNG` verb (HARDWARE_DESIGN.md §15E).
