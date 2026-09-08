// qrng_entropy.v — on-FPGA quantum random-number generator from AD7616
// LSB noise.
//
// The 16-bit AD7616 sampling a DARK (unpumped) detector channel delivers a
// noise floor whose least-significant bits are dominated by thermal/Johnson
// + ADC quantization noise (a dark channel has no photocurrent, hence no
// shot noise) and are effectively independent between samples.  This module
// streams those LSBs through a von-Neumann extractor
//   pair (b0,b1):  01 -> 0,  10 -> 1,  00 / 11 -> discard
// and packs the debiased bits into W-bit words exposed to the command
// parser via the READ_RNG verb.
//
// This is a *physical* entropy source -- the bits come from real analog
// noise on the ADC front end -- unlike the Qiskit `qrand` algorithm, which
// is a simulated Hadamard coin flip (qc.h ; qc.measure).  The 1 kHz lock-in
// mod_sync is intentionally NOT used here: entropy is harvested from a
// quiescent channel with the microwave modulation off, so the LSB noise is
// broadband thermal/Johnson + ADC quantization noise, not the lock-in signal.
//
// Top-level wiring: the adc_reader background scan taps a dark channel
// (ENTROPY_CH, default channel 0) and feeds a fresh sample here every scan;
// the command_parser READ_RNG verb harvests N bytes and streams them as hex.
// The Python SimulatedFPGA mirrors this pipeline so the same host code drives
// both.
module qrng_entropy #
(
    parameter W = 32                       // output word width (bits)
) (
    input  wire             clk,
    input  wire             rst,
    // one AD7616 sample stream -- a dark detector channel = entropy source.
    input  wire [15:0]      adc_data,
    input  wire             adc_valid,    // one-cycle strobe per fresh sample
    // control / status from the command parser
    input  wire             harvest_en,   // 1 = keep harvesting LSBs
    output wire [W-1:0]     rng_word,     // latest debiased W-bit word
    output wire             rng_valid,    // pulse: a fresh word is ready
    output wire [15:0]      bits_harvested// running count of kept bits
);
    // von-Neumann extractor state: consume LSB pairs of successive samples.
    reg        have_first;
    reg        first_bit;
    reg [W-1:0] shift;
    reg [$clog2(W+1)-1:0] bit_cnt;
    reg [W-1:0] word_q;
    reg        valid_q;
    reg [15:0] harvested_cnt;

    wire lsb = adc_data[0];

    always @(posedge clk) begin
        if (rst) begin
            have_first    <= 1'b0;
            first_bit     <= 1'b0;
            shift         <= {W{1'b0}};
            bit_cnt       <= {$clog2(W+1){1'b0}};
            word_q        <= {W{1'b0}};
            valid_q       <= 1'b0;
            harvested_cnt <= 16'd0;
        end else begin
            valid_q <= 1'b0;                  // default: no new word this cycle
            if (harvest_en && adc_valid) begin
                if (!have_first) begin
                    first_bit  <= lsb;
                    have_first <= 1'b1;
                end else begin
                    // a complete pair (first_bit, lsb) is in hand
                    if (first_bit != lsb) begin
                        // 01 -> 0, 10 -> 1 : keep the second sample's LSB
                        shift    <= {shift[W-2:0], lsb};
                        bit_cnt  <= bit_cnt + 1'b1;
                        harvested_cnt <= harvested_cnt + 16'd1;
                        if (bit_cnt == W-1) begin
                            word_q  <= {shift[W-2:0], lsb};
                            valid_q <= 1'b1;
                            bit_cnt <= {$clog2(W+1){1'b0}};
                        end
                    end
                    have_first <= 1'b0;        // either way, the pair is consumed
                end
            end
        end
    end

    assign rng_word       = word_q;
    assign rng_valid      = valid_q;
    assign bits_harvested = harvested_cnt;
endmodule