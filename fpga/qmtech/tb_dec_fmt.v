`timescale 1ns / 1ps
// =============================================================================
// tb_dec_fmt.v -- prove that dec_fmt.v is BIT-IDENTICAL to the original
//                 combinational formatters in rtl/command_parser.v
// -----------------------------------------------------------------------------
// The three reference functions below are copied verbatim from
// fpga/rtl/command_parser.v.  dec_fmt must produce exactly the same digits.
//
// What is compared:
//   dec32_pad    [79:0]   vs dec_fmt #(.W(32), .D(10), .SIGNED(0))
//   dec5_pad     [39:0]   vs dec_fmt #(.W(16), .D(5),  .SIGNED(0))
//   signed_dec48 [119:0]  vs dec_fmt #(.W(48), .D(15), .SIGNED(1))
//                          (the sign byte [127:120] is checked separately)
//
// dec_fmt is free-running and only latches a result at the END of a
// conversion, so the testbench waits 3*(W+1) cycles after changing an input
// before sampling -- comfortably past the documented worst-case latency of
// 2*(W+1).
//
// Run:
//   cd fpga/qmtech && iverilog -g2012 -o /tmp/tb_dec_fmt \
//        tb_dec_fmt.v dec_fmt.v && vvp /tmp/tb_dec_fmt
// Exit code 0 = every vector matched.
// =============================================================================
module tb_dec_fmt;

    reg clk = 1'b0;
    reg rst = 1'b1;
    always #5 clk = ~clk;                      // 100 MHz

    // ---------------------------------------------------------------- inputs
    reg  [31:0] in32;   wire [79:0]  out32;  wire v32;
    reg  [15:0] in16;   wire [39:0]  out16;  wire v16;
    reg  [47:0] in48;   wire [127:0] out48;  wire v48;   // sign + 15 digits

    // SEQ_MODE selects combinational (0, no latency) or sequential (1)
`ifndef SEQ_MODE
`define SEQ_MODE 0
`endif
`ifndef STEPS_MODE
`define STEPS_MODE 1
`endif

    dec_fmt #(.W(32), .D(10), .SIGNED(0), .SEQ(`SEQ_MODE), .STEPS(`STEPS_MODE)) dut32 (
        .clk(clk), .rst(rst), .din(in32), .dout(out32), .valid(v32));
    dec_fmt #(.W(16), .D(5),  .SIGNED(0), .SEQ(`SEQ_MODE), .STEPS(`STEPS_MODE)) dut16 (
        .clk(clk), .rst(rst), .din(in16), .dout(out16), .valid(v16));
    dec_fmt #(.W(48), .D(15), .SIGNED(1), .SEQ(`SEQ_MODE), .STEPS(`STEPS_MODE)) dut48 (
        .clk(clk), .rst(rst), .din(in48), .dout(out48), .valid(v48));

    // ============================================ reference (verbatim copy)
    function [79:0] dec32_pad;
        input [31:0] v;
        reg [31:0] t;
        begin
            t = v;
            dec32_pad[7:0]    = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[15:8]   = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[23:16]  = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[31:24]  = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[39:32]  = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[47:40]  = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[55:48]  = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[63:56]  = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[71:64]  = 8'h30 + (t % 32'd10);  t = t / 32'd10;
            dec32_pad[79:72]  = 8'h30 + (t % 32'd10);
        end
    endfunction

    function [39:0] dec5_pad;
        input [15:0] v;
        reg [15:0] t;
        begin
            t = v;
            dec5_pad[7:0]    = 8'h30 + (t % 16'd10);  t = t / 16'd10;
            dec5_pad[15:8]   = 8'h30 + (t % 16'd10);  t = t / 16'd10;
            dec5_pad[23:16]  = 8'h30 + (t % 16'd10);  t = t / 16'd10;
            dec5_pad[31:24]  = 8'h30 + (t % 16'd10);  t = t / 16'd10;
            dec5_pad[39:32]  = 8'h30 + (t % 16'd10);
        end
    endfunction

    function [127:0] signed_dec48;
        input signed [47:0] v;
        integer i;
        reg [47:0] mag;
        reg [7:0]  sign_c;
        reg [7:0]  d [0:14];
        begin
            if (v < 48'sd0) begin sign_c = 8'h2D; mag = -v; end  // '-'
            else            begin sign_c = 8'h20; mag =  v; end  // ' ' pad
            for (i = 0; i < 15; i = i + 1) begin
                d[14 - i] = 8'h30 + (mag % 48'sd10);
                mag       = mag / 48'sd10;
            end
            signed_dec48 = {sign_c,
                            d[0], d[1], d[2], d[3], d[4], d[5], d[6],
                            d[7], d[8], d[9], d[10], d[11], d[12], d[13], d[14]};
        end
    endfunction
    // ============================================ /reference

    integer errors = 0;
    integer n32 = 0, n16 = 0, n48 = 0;
    integer i;

    // ASCII of the whole bus, for readable failure messages
    function [8*17:0] ascii_of80;
        input [127:0] v;
        integer k;
        begin
            ascii_of80 = {(8*17){1'b0}};
            for (k = 0; k < 16; k = k + 1)
                ascii_of80[8*k +: 8] = v[8*k +: 8];
        end
    endfunction

    task check32;
        input [31:0] v;
        reg   [79:0] exp;
        begin
            @(negedge clk); in32 = v;
            repeat (3*(32+1)) @(posedge clk);
            exp = dec32_pad(v);
            n32 = n32 + 1;
            if (out32 !== exp) begin
                errors = errors + 1;
                $display("FAIL  dec32_pad(%0d) = exp %02x.. got %02x..",
                         $signed({1'b0, v}), exp[79:72], out32[79:72]);
                $display("      exp ascii: %s", ascii_of80({48'd0, exp}));
                $display("      got ascii: %s", ascii_of80({48'd0, out32}));
            end
        end
    endtask

    task check16;
        input [15:0] v;
        reg   [39:0] exp;
        begin
            @(negedge clk); in16 = v;
            repeat (3*(16+1)) @(posedge clk);
            exp = dec5_pad(v);
            n16 = n16 + 1;
            if (out16 !== exp) begin
                errors = errors + 1;
                $display("FAIL  dec5_pad(%0d) exp %02x.. got %02x..",
                         v, exp[39:32], out16[39:32]);
                $display("      exp: %s   got: %s",
                         ascii_of80({88'd0, exp}), ascii_of80({88'd0, out16}));
            end
        end
    endtask

    task check48;
        input signed [47:0] v;
        // NOTE: not named `ref` -- that is a SystemVerilog reserved word and
        // iverilog -g2012 rejects it.
        reg   [127:0] refv;
        begin
            @(negedge clk); in48 = v;
            repeat (3*(48+1)) @(posedge clk);
            refv = signed_dec48(v);
            n48 = n48 + 1;
            // full 128-bit compare: sign byte AND all 15 digits
            if (out48 !== refv) begin
                errors = errors + 1;
                $display("FAIL  signed_dec48(%0d)", $signed(v));
                $display("      exp: %s", ascii_of80(refv));
                $display("      got: %s", ascii_of80(out48));
            end
        end
    endtask

    // ---- 32-bit vectors -----------------------------------------------------
    task run32;
        begin
            check32(32'd0);
            check32(32'd1);
            check32(32'd9);
            check32(32'd10);
            check32(32'd99);
            check32(32'd100);
            check32(32'd999);
            check32(32'd1000);
            check32(32'd9999);
            check32(32'd10000);
            check32(32'd99999);
            check32(32'd100000);
            check32(32'd999999);
            check32(32'd1000000);
            check32(32'd9999999);
            check32(32'd10000000);
            check32(32'd99999999);
            check32(32'd100000000);
            check32(32'd999999999);
            check32(32'd1000000000);
            check32(32'd4000000000);
            check32(32'hFFFFFFFF);        // 4294967295, max
            check32(32'h80000000);
            check32(32'h0000FFFF);
            check32(32'd65535);
            check32(32'd65536);
            for (i = 0; i < 400; i = i + 1) check32($random);
        end
    endtask

    // ---- 16-bit vectors -----------------------------------------------------
    task run16;
        begin
            check16(16'd0);     check16(16'd1);
            check16(16'd9);     check16(16'd10);
            check16(16'd99);    check16(16'd100);
            check16(16'd999);   check16(16'd1000);
            check16(16'd9999);  check16(16'd10000);
            check16(16'd65535); // max, 5 digits
            check16(16'd50000);
            for (i = 0; i < 400; i = i + 1) check16($random);
        end
    endtask

    // ---- 48-bit signed vectors ---------------------------------------------
    task run48;
        reg signed [47:0] r;
        begin
            check48(48'sd0);
            check48(48'sd1);        check48(-48'sd1);
            check48(48'sd9);        check48(-48'sd9);
            check48(48'sd10);       check48(-48'sd10);
            check48(48'sd99);       check48(-48'sd99);
            check48(48'sd100);      check48(-48'sd100);
            check48(48'sd9999);     check48(-48'sd9999);
            check48(48'sd1000000);  check48(-48'sd1000000);
            check48(48'sh7FFFFFFFFFFF);   // +2^47-1 = 140737488355327
            check48(-48'sh800000000000);  //    -2^47 = -140737488355328
            check48(48'sh400000000000);
            check48(-48'sh400000000000);
            // NOTE: a 48-bit signed magnitude tops out at 2^47-1 ~= 1.407e14
            // (15 digits), so 999999999999999 does NOT fit -- values must
            // stay below 2^47 or the constant silently truncates.
            check48(48'sd100000000000000);      // 1e14
            check48(-48'sd100000000000000);
            for (i = 0; i < 400; i = i + 1) begin
                r = {$random, $random};
                r = r >>> 16;               // keep inside 48-bit range
                check48(r);
            end
        end
    endtask

    initial begin
        in32 = 32'd0;  in16 = 16'd0;  in48 = 48'sd0;
        repeat (4) @(posedge clk);
        @(negedge clk); rst = 1'b0;

        run32;
        run16;
        run48;

        $display("");
        $display("=================================================");
        $display("vectors: dec32_pad=%0d  dec5_pad=%0d  signed_dec48=%0d",
                 n32, n16, n48);
        $display("errors : %0d", errors);
        if (errors == 0)
            $display("RESULT : PASS - dec_fmt is bit-identical");
        else
            $display("RESULT : FAIL");
        $display("=================================================");
        $finish;
    end
endmodule
