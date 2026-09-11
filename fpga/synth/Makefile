# fpga/synth/Makefile -- synthesis + bitstream build targets for the QSSC FPGA.
#
# Targets:
#   synth      - yosys synth_xilinx -> qssc_top.edif + SYNTH_REPORT.md (this box)
#   bitstream  - synth, then Vivado P&R -> qssc_top.bit (needs Vivado installed)
#   clean      - remove generated netlists / reports / bitstream
#
# `bitstream` cannot run here (no Vivado); run it on a machine with Vivado on
# PATH. `synth` runs anywhere yosys (oss-cad-suite) is available.

HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ROOT := $(abspath $(HERE/../..))

.PHONY: synth bitstream clean

EDIF   := $(HERE)/qssc_top.edif
BIT    := $(HERE)/qssc_top.bit

synth: $(EDIF)
	@echo "=== synth complete: $< ==="

$(EDIF):
	@cd $(ROOT) && bash fpga/synth/run_synth.sh

# yosys synthesis first (produces qssc_top.edif), then the Vivado P&R flow.
bitstream: $(BIT)
	@echo "=== bitstream complete: $< ==="

$(BIT): $(EDIF) build_bitstream.tcl
	@command -v vivado >/dev/null 2>&1 || { \
		echo "ERROR: vivado not found on PATH -- run 'make bitstream' on a" >&2; \
		echo "       Vivado machine (this flow needs Vivado P&R)." >&2; \
		exit 2; }
	cd $(HERE) && vivado -mode batch -source build_bitstream.tcl

clean:
	rm -f $(EDIF) $(BIT) $(HERE)/qssc_top.json $(HERE)/synth.log \
	      $(HERE)/timing_summary.rpt $(HERE)/utilization.rpt \
	      $(HERE)/SYNTH_REPORT.md $(ROOT)/abc.history