`timescale 1ns / 1ps
// =============================================================================
// dec_fmt.v -- binary -> ASCII decimal, double-dabble (shift-add-3)
// -----------------------------------------------------------------------------
// Replaces the combinational `dec32_pad` / `dec5_pad` / `signed_dec48`
// helpers in command_parser.v, which chain repeated 32/48-bit `/10` and
// `%10` divisions.  Vivado expands those into 30 351 LUTs -- 81 % of the
// design and 359 % of an XC7A15T on its own, so the design cannot be placed
// at all ([DRC UTLZ-1]).
//
// OUTPUT LAYOUT matches the functions it replaces: digit 0 (ones) at [7:0],
// tens at [15:8], ...  With SIGNED=1 the top byte is the sign character, so
// the result is a drop-in for signed_dec48's 128-bit vector.
//
// TIMING -- selected by parameter SEQ:
//
//   SEQ = 0 (default)  FULLY COMBINATIONAL.  All W double-dabble steps are
//        unrolled in series.  Zero latency: `dout` is a pure function of `din`
//        exactly like the original functions, so nothing downstream can ever
//        observe a stale value.  Cost scales as W*D correction cells.
//
//   SEQ = 1            SEQUENTIAL, one step per clock.  ~25 LUT instead of
//        ~W*D, but `dout` lags `din` by up to 2*(W+1) cycles.  This broke
//        command_parser's reply FSM (tb_top_level: 5 failures), because the
//        FSM copies the digits into its line buffers in a single cycle.
//
// Correctness of BOTH modes against the original functions is proven by
// tb_dec_fmt (1259 vectors, bit-identical).
// =============================================================================
module dec_fmt #(
    parameter W       = 32,   // source width in bits
    parameter D       = 10,   // decimal digits produced (digit 0 = ones)
    parameter SIGNED  = 0,    // 1 => interpret din as two's complement
    parameter SEQ     = 0,    // 0 = combinational (no latency), 1 = registered
    parameter STEPS   = 1     // when SEQ=1: double-dabble steps applied per
                              // clock (1..W).  Cost is proportional to STEPS,
                              // latency is W/STEPS cycles.  STEPS must divide W.
)(
    input  wire                         clk,
    input  wire                         rst,     // synchronous, active high
    input  wire [W-1:0]                 din,
    output wire [8*(D+SIGNED)-1:0]      dout,
    output wire                         valid
);

    // ---- magnitude + sign ---------------------------------------------------
    wire       din_neg  = SIGNED ? din[W-1] : 1'b0;
    wire [W-1:0] mag    = din_neg ? (~din + 1'b1) : din;
    wire [7:0] din_sign = din_neg ? 8'h2D : 8'h20;      // '-' or ' '

    // ---- one double-dabble correction cell: digit>=5 ? digit+3 : digit -----
    // Independent per digit: 9+3 = 12 still fits in 4 bits, so there is no
    // carry between digits and no carry chain is required.
    function [4*D-1:0] correct;
        input [4*D-1:0] v;
        integer k;
        reg [3:0] d;
        begin
            correct = {4*D{1'b0}};
            for (k = 0; k < D; k = k + 1) begin
                d = v[4*k +: 4];
                correct[4*k +: 4] = (d >= 4'd5) ? (d + 4'd3) : d;
            end
        end
    endfunction

    // ---- ASCII packing: digit 0 (ones) lands at [7:0] -----------------------
    // NOTE: this is a pure bit splice, NOT an addition.  0x30 = 0011_0000 and
    // double-dabble guarantees each digit is 0..9, so 0x30 + d can never carry
    // out of the low nibble: 0011_0000 + 0000_dddd == 0011_dddd.  Writing it
    // as {4'h3, d} lets synthesis skip an 8-bit adder per output byte.
    function [8*D-1:0] to_ascii;
        input [4*D-1:0] v;
        integer k;
        begin
            to_ascii = {8*D{1'b0}};
            for (k = 0; k < D; k = k + 1)
                to_ascii[8*k +: 8] = {4'h3, v[4*k +: 4]};
        end
    endfunction

    // ========================================================================
    // SEQ = 0 : fully combinational -- every step unrolled
    // ========================================================================
    generate if (SEQ == 0) begin : g_comb
        // stage[0] = 0; stage[s+1] = {corr(stage[s])[MSB-1:0], mag[W-1-s]}
        wire [4*D*(W+1)-1:0] stage;
        assign stage[0 +: 4*D] = {(4*D){1'b0}};

        genvar s;
        for (s = 0; s < W; s = s + 1) begin : g_step
            // NOTE: an intermediate wire is required -- bit-selecting the
            // result of a function call (correct(...)[...]) is not legal
            // Verilog-2001.
            wire [4*D-1:0] cur  = stage[4*D*s +: 4*D];
            wire [4*D-1:0] corr = correct(cur);
            assign stage[4*D*(s+1) +: 4*D] = {corr[4*D-2:0], mag[W-1-s]};
        end

        assign dout  = SIGNED ? {din_sign, to_ascii(stage[4*D*(W) +: 4*D])}
                              : to_ascii(stage[4*D*(W) +: 4*D]);
        assign valid = 1'b1;

    // ========================================================================
    // SEQ = 1 : sequential, one step per clock
    // ========================================================================
    end else begin : g_seq
        // STEPS double-dabble steps are applied per clock: the combinational
        // cost is (W/STEPS) * STEPS = W... no: only STEPS correction layers
        // exist in hardware, reused every cycle.  So logic ~= STEPS layers
        // (proportional) and latency ~= W/STEPS cycles.
        localparam GROUPS = W / STEPS;      // STEPS must divide W
        localparam [W:0] CNT_INIT = GROUPS;

        reg  [W-1:0]   bin;
        reg  [4*D-1:0] bcd;
        reg  [W:0]     cnt;
        reg  [7:0]     sign_r;
        reg  [8*(D+SIGNED)-1:0] dout_r;
        reg            valid_r;

        // ---- one group: STEPS correction+shift layers, combinational --------
        wire [4*D*(STEPS+1)-1:0] grp;
        assign grp[0 +: 4*D] = bcd;

        genvar j;
        for (j = 0; j < STEPS; j = j + 1) begin : g_layer
            wire [4*D-1:0] cur  = grp[4*D*j +: 4*D];
            wire [4*D-1:0] corr = correct(cur);
            assign grp[4*D*(j+1) +: 4*D] = {corr[4*D-2:0], bin[W-1-j]};
        end

        wire [4*D-1:0] grp_out = grp[4*D*STEPS +: 4*D];

        initial begin
            cnt = {(W+1){1'b0}};  bcd = {(4*D){1'b0}};
            bin = {W{1'b0}};      sign_r = 8'h20;
            dout_r = {(8*(D+SIGNED)){1'b0}}; valid_r = 1'b0;
        end

        always @(posedge clk) begin
            if (rst) begin
                cnt <= {(W+1){1'b0}};  valid_r <= 1'b0;
            end else if (cnt == 0) begin
                // start a conversion on a fresh sample; sign is captured here
                // so it can never disagree with the digits that follow
                bin <= mag;  sign_r <= din_sign;
                bcd <= {(4*D){1'b0}};  cnt <= CNT_INIT;
                valid_r <= 1'b0;
            end else begin
                bcd <= grp_out;
                bin <= bin << STEPS;          // consume STEPS source bits
                cnt <= cnt - 1'b1;
                if (cnt == 1) begin
                    dout_r  <= {sign_r, to_ascii(grp_out)};
                    valid_r <= 1'b1;
                    cnt     <= {(W+1){1'b0}};
                end
            end
        end

        assign dout  = dout_r;
        assign valid = valid_r;

`ifdef FMT_DEBUG
        // temporary instrumentation: does a conversion ever complete, and
        // how long after reset?  Capped so the log stays readable.
        reg [31:0] dbg_n    = 32'd0;
        reg [31:0] dbg_done = 32'd0;
        always @(posedge clk) begin
            if (rst) begin
                if (dbg_n < 3)
                    $display("%0t FMT[W=%0d] RESET asserted (cnt=%0d dout=%h)",
                             $time, W, cnt, dout_r);
                dbg_n <= dbg_n + 1;
            end else if (valid_r && dbg_done < 3) begin
                dbg_done <= dbg_done + 1;
                $display("%0t FMT[W=%0d] VALID#%0d dout=%h",
                         $time, W, dbg_done, dout_r);
            end
        end
`endif
    end endgenerate

endmodule
