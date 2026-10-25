`timescale 1ns / 1ps
// =============================================================================
// div_seq.v -- signed 32/32 division, sequential restoring divider
// -----------------------------------------------------------------------------
// WHY: command_parser computes
//     sweep_step = (f_stop - f_start) / (n_points - 1)
// as a COMBINATIONAL division between two variables.  Vivado builds a full
// divider from it: 295 CARRY4, 328 logic levels, 89.65 ns of path delay
// against a 10 ns budget -- WNS = -79.6 ns with 3983 failing endpoints.
// That cannot close at 100 MHz on ANY device.
//
// The result is needed only ONCE per command and is consumed much later (the
// FSM programs the ADF4351 over SPI first, hundreds of cycles), so dividing
// over 34 cycles is free in practice.
//
// Latency: 1 start-to-done is 2 cycles of overhead + 32 divide steps = 34.
// Cost: ~35 LUT + ~100 FF (vs ~1000 LUT for the combinational divider).
//
// Semantics match the original `a / b` Verilog expression for signed operands
// (truncated toward zero), which tb_div_seq verifies against the operator.
// =============================================================================
module div_seq (
    input  wire               clk,
    input  wire               rst,      // synchronous, active high
    input  wire               start,    // 1-cycle pulse; loads num/den
    input  wire signed [31:0] num,
    input  wire signed [31:0] den,      // caller guarantees den != 0
    output reg  signed [31:0] quot,
    output reg                done      // high for 1 cycle when quot valid
);

    // ---- magnitudes --------------------------------------------------------
    wire [31:0] n_mag = num[31] ? (~num + 32'd1) : num;
    wire [31:0] d_mag = den[31] ? (~den + 32'd1) : den;
    wire        neg   = num[31] ^ den[31];

    // ---- datapath ----------------------------------------------------------
    reg  [31:0]  q;         // quotient being built (MSB first)
    reg  [32:0]  rem;       // partial remainder
    reg  [31:0]  dend;      // remaining dividend bits, MSB first
    reg  [5:0]   bit_i;     // 31 .. 0
    reg          neg_r;
    reg          busy;

    // one division step: shift the next dividend bit into the remainder
    wire [32:0] rem_shift = {rem[31:0], dend[31]};
    wire        ge       = (rem_shift >= {1'b0, d_mag});
    wire [32:0] rem_next = ge ? (rem_shift - {1'b0, d_mag}) : rem_shift;
    wire [31:0] q_next   = {q[30:0], ge};

    initial begin
        q = 32'd0;  rem = 33'd0;  dend = 32'd0;
        bit_i = 6'd0;  neg_r = 1'b0;  busy = 1'b0;
        quot = 32'd0;  done = 1'b0;
    end

    always @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            done <= 1'b0;
        end else begin
            done <= 1'b0;
            if (start && !busy) begin
                q      <= 32'd0;
                rem    <= 33'd0;
                dend   <= n_mag;
                bit_i  <= 6'd31;
                neg_r  <= neg;
                busy   <= 1'b1;
            end else if (busy) begin
                q    <= q_next;
                rem  <= rem_next;
                dend <= {dend[30:0], 1'b0};
                bit_i <= bit_i - 6'd1;
                if (bit_i == 6'd0) begin
                    // final step: publish, honouring the sign
                    quot <= neg_r ? (~q_next + 32'd1) : q_next;
                    done <= 1'b1;
                    busy <= 1'b0;
                end
            end
        end
    end

endmodule
