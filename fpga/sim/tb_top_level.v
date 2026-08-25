`timescale 1ns / 1ps
//============================================================================
// tb_top_level.v
// ----------------------------------------------------------------------------
// Simple testbench for the QSSC top_level. Generates a 100 MHz clock and
// reset, sends "DRIVE_EMITTER 3 4 50\n" over the UART RX line at 115200 baud
// and checks the "OK\n" reply + the decoded emitter crossbar pattern, then
// sends "READ_DETECTORS\n" and checks the streamed 64-electrode photocurrent
// code sequence. Camera/SCCB/RF pins are gone (on-chip readout has no camera).
//============================================================================
module tb_top_level;
    localparam integer   CLK_PERIOD  = 10;         // 100 MHz -> 10 ns
    localparam integer   CLKS_PER_BIT = 868;       // 100 MHz / 115200
    localparam integer   BIT_TIME    = CLK_PERIOD * CLKS_PER_BIT;

    // ---- DUT signals -------------------------------------------------------
    reg         clk_100mhz = 1'b0;
    reg         reset      = 1'b1;
    reg         rx         = 1'b1;       // UART idle
    wire        tx;
    wire [7:0]  emitter_rows;
    wire [7:0]  emitter_cols;
    wire [5:0]  det_mux_sel;
    wire        mod_sync;
    wire        mw_gate;
    wire [3:0]  spi_pins;
    wire        refclk_out;

    // ---- Captured TX bytes -------------------------------------------------
    reg [7:0]   rx_log [0:511];
    integer     rx_log_cnt = 0;
    integer     i;
    reg [7:0]   rx_byte_cap;
    integer     ok_count = 0;
    integer     err_count = 0;

    // ---- MW RF-switch gate pulse seen flag --------------------------------
    reg mw_gate_seen = 1'b0;
    always @(posedge clk_100mhz) if (mw_gate) mw_gate_seen <= 1'b1;

    // ---- DUT ---------------------------------------------------------------
    top_level dut (
        .clk_100mhz  (clk_100mhz),
        .reset       (reset),
        .rx          (rx),
        .tx          (tx),
        .emitter_rows(emitter_rows),
        .emitter_cols(emitter_cols),
        .det_mux_sel (det_mux_sel),
        .mod_sync    (mod_sync),
        .mw_gate     (mw_gate),
        .spi_pins    (spi_pins),
        .refclk_out  (refclk_out)
    );

    // ---- 100 MHz clock -----------------------------------------------------
    always #(CLK_PERIOD/2) clk_100mhz = ~clk_100mhz;

    // ---- UART byte transmit task (drive rx line) ---------------------------
    task uart_send_byte;
        input [7:0] b;
        integer k;
        begin
            rx = 1'b0;                       // start bit
            #(BIT_TIME);
            for (k = 0; k < 8; k = k + 1) begin
                rx = b[k];                    // LSB first
                #(BIT_TIME);
            end
            rx = 1'b1;                       // stop bit
            #(BIT_TIME);
        end
    endtask

    // ---- UART RX monitor (sample tx line) ---------------------------------
    initial begin
        forever begin
            @(negedge tx);                   // detect start bit
            #(BIT_TIME/2);                   // middle of start bit
            if (tx === 1'b0) begin           // glitch filter
                #(BIT_TIME);                 // middle of first data bit
                rx_byte_cap = 8'd0;
                for (i = 0; i < 8; i = i + 1) begin
                    rx_byte_cap[i] = tx;
                    #(BIT_TIME);
                end
                // middle of stop bit now
                if (rx_log_cnt < 512) begin
                    rx_log[rx_log_cnt] = rx_byte_cap;
                    rx_log_cnt = rx_log_cnt + 1;
                end
            end
        end
    end

    // ---- Main stimulus -----------------------------------------------------
    integer c;
    integer r;
    reg [7:0] cmd_bytes [0:31];
    reg [7:0] sendb;        // scalar temp (pyverilog can't take a memory word as a task arg)
    initial begin
        // Reset
        reset = 1'b1;
        for (r=0;r<20;r=r+1) begin @(posedge clk_100mhz); end
        reset = 1'b0;
        for (r=0;r<5;r=r+1) begin @(posedge clk_100mhz); end

        // ---- 1) DRIVE_EMITTER 3 4 50 -------------------------------------
        // "DRIVE_EMITTER 3 4 50\n" (13 + space + 1 + space + 1 + space + 1 + 0 + \n = 21)
        cmd_bytes[0]="D";  cmd_bytes[1]="R";  cmd_bytes[2]="I";  cmd_bytes[3]="V";
        cmd_bytes[4]="E";  cmd_bytes[5]="_";  cmd_bytes[6]="E";  cmd_bytes[7]="M";
        cmd_bytes[8]="I";  cmd_bytes[9]="T";  cmd_bytes[10]="T"; cmd_bytes[11]="E";
        cmd_bytes[12]="R"; cmd_bytes[13]=" "; cmd_bytes[14]="3"; cmd_bytes[15]=" ";
        cmd_bytes[16]="4"; cmd_bytes[17]=" "; cmd_bytes[18]="5"; cmd_bytes[19]="0";
        cmd_bytes[20]=8'h0A;

        $display("TB: sending DRIVE_EMITTER 3 4 50");
        for (c = 0; c < 21; c = c + 1) begin
            sendb = cmd_bytes[c];
            uart_send_byte(sendb);
        end

        // Wait for the parser to react, the emitter to fire, and "OK\n" reply.
        #(200000*CLK_PERIOD);

        // ---- Self-check: emitter crossbar pattern -------------------------
        // row=3 -> rows = 1<<3 = 0x08 ; col=4 -> cols = ~(1<<4) = 0xEF
        $display("TB: emitter_rows=0x%02h (expect 0x08), emitter_cols=0x%02h (expect 0xEF)",
                 emitter_rows, emitter_cols);
        if (emitter_rows === 8'h08) ok_count = ok_count + 1;
        else begin err_count = err_count + 1; $display("TB: FAIL emitter_rows mismatch"); end
        if (emitter_cols === 8'hEF) ok_count = ok_count + 1;
        else begin err_count = err_count + 1; $display("TB: FAIL emitter_cols mismatch"); end

        // ---- Self-check: reply = "OK\n" (0x4F 0x4B 0x0A) -----------------
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h4F && rx_log[1] === 8'h4B && rx_log[2] === 8'h0A) begin
            ok_count = ok_count + 1;
            $display("TB: DRIVE_EMITTER reply = OK\\n  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL DRIVE_EMITTER reply (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end

        // ---- 2) READ_DETECTORS -------------------------------------------
        // Reset the capture log so the stream starts at index 0.
        rx_log_cnt = 0;
        cmd_bytes[0]="R";  cmd_bytes[1]="E";  cmd_bytes[2]="A";  cmd_bytes[3]="D";
        cmd_bytes[4]="_";  cmd_bytes[5]="D";  cmd_bytes[6]="E";  cmd_bytes[7]="T";
        cmd_bytes[8]="E";  cmd_bytes[9]="C";  cmd_bytes[10]="T"; cmd_bytes[11]="O";
        cmd_bytes[12]="R"; cmd_bytes[13]="S"; cmd_bytes[14]=8'h0A;

        $display("TB: sending READ_DETECTORS");
        for (c = 0; c < 15; c = c + 1) begin
            sendb = cmd_bytes[c];
            uart_send_byte(sendb);
        end

        // 64 conversions * ~100 cycles each + UART TX of ~320 bytes
        #(4000000*CLK_PERIOD);

        // ---- Self-check: detector stream ---------------------------------
        // Behavioural XADC: electrode n -> code 1000 + 40*n. All 4-digit, so the
        // stream is "1000 1040 1080 ... 3520\n" (64 codes, 63 spaces, 1 newline
        // => 256 + 63 + 1 = 320 bytes). Check the prefix and the byte count.
        $display("TB: READ_DETECTORS captured %0d bytes", rx_log_cnt);
        if (rx_log_cnt >= 5 &&
            rx_log[0] === 8'h31 && rx_log[1] === 8'h30 &&
            rx_log[2] === 8'h30 && rx_log[3] === 8'h30 &&
            rx_log[4] === 8'h20) begin
            ok_count = ok_count + 1;
            $display("TB: detector stream starts with \"1000 \"  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL detector stream prefix (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end
        // 64 codes (4 digits each) + 63 spaces + 1 newline = 320 bytes
        if (rx_log_cnt === 320) begin
            ok_count = ok_count + 1;
            $display("TB: detector stream length = 320 bytes  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL detector stream length (got %0d, expect 320)", rx_log_cnt);
        end
        // last byte must be '\n'
        if (rx_log_cnt >= 1 && rx_log[rx_log_cnt-1] === 8'h0A) begin
            ok_count = ok_count + 1;
            $display("TB: detector stream ends with '\\n'  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL detector stream terminator");
        end

        // ---- 3) READ_DETECTOR 5 (single-electrode read) -----------------
        // Behavioural XADC: electrode 5 -> code 1000 + 40*5 = 1200 -> "1200\n"
        rx_log_cnt = 0;
        cmd_bytes[0]="R";  cmd_bytes[1]="E";  cmd_bytes[2]="A";  cmd_bytes[3]="D";
        cmd_bytes[4]="_";  cmd_bytes[5]="D";  cmd_bytes[6]="E";  cmd_bytes[7]="T";
        cmd_bytes[8]="E";  cmd_bytes[9]="C";  cmd_bytes[10]="T"; cmd_bytes[11]="O";
        cmd_bytes[12]="R"; cmd_bytes[13]=" "; cmd_bytes[14]="5"; cmd_bytes[15]=8'h0A;
        $display("TB: sending READ_DETECTOR 5");
        for (c = 0; c < 16; c = c + 1) begin sendb = cmd_bytes[c]; uart_send_byte(sendb); end
        #(200000*CLK_PERIOD);
        $display("TB: READ_DETECTOR captured %0d bytes", rx_log_cnt);
        if (rx_log_cnt >= 5 &&
            rx_log[0] === 8'h31 && rx_log[1] === 8'h32 &&
            rx_log[2] === 8'h30 && rx_log[3] === 8'h30 &&
            rx_log[4] === 8'h0A) begin
            ok_count = ok_count + 1;
            $display("TB: READ_DETECTOR reply = \"1200\\n\"  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL READ_DETECTOR reply (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end

        // ---- 4) Out-of-range DRIVE_EMITTER 9 9 50 -> ERR ----------------
        // row=9 / col=9 are out of the 0..7 crossbar; bounds check must reject.
        rx_log_cnt = 0;
        cmd_bytes[0]="D";  cmd_bytes[1]="R";  cmd_bytes[2]="I";  cmd_bytes[3]="V";
        cmd_bytes[4]="E";  cmd_bytes[5]="_";  cmd_bytes[6]="E";  cmd_bytes[7]="M";
        cmd_bytes[8]="I";  cmd_bytes[9]="T";  cmd_bytes[10]="T"; cmd_bytes[11]="E";
        cmd_bytes[12]="R"; cmd_bytes[13]=" "; cmd_bytes[14]="9"; cmd_bytes[15]=" ";
        cmd_bytes[16]="9"; cmd_bytes[17]=" "; cmd_bytes[18]="5"; cmd_bytes[19]="0";
        cmd_bytes[20]=8'h0A;
        $display("TB: sending DRIVE_EMITTER 9 9 50 (expect ERR)");
        for (c = 0; c < 21; c = c + 1) begin sendb = cmd_bytes[c]; uart_send_byte(sendb); end
        #(200000*CLK_PERIOD);
        if (rx_log_cnt >= 4 &&
            rx_log[0] === 8'h45 && rx_log[1] === 8'h52 &&
            rx_log[2] === 8'h52 && rx_log[3] === 8'h0A) begin
            ok_count = ok_count + 1;
            $display("TB: out-of-range DRIVE_EMITTER reply = ERR\\n  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL bounds-check ERR (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end

        // ---- 5) APPLY_PULSE valid -> OK ---------------------------------
        // qubit=0, freq_khz=2870000 (2.87 GHz NV), amp=100, dur=500 ns, phase=0.
        rx_log_cnt = 0;
        cmd_bytes[0]="A";  cmd_bytes[1]="P";  cmd_bytes[2]="P";  cmd_bytes[3]="L";
        cmd_bytes[4]="Y";  cmd_bytes[5]="_";  cmd_bytes[6]="P";  cmd_bytes[7]="U";
        cmd_bytes[8]="L";  cmd_bytes[9]="S";  cmd_bytes[10]="E"; cmd_bytes[11]=" ";
        cmd_bytes[12]="0"; cmd_bytes[13]=" "; cmd_bytes[14]="2"; cmd_bytes[15]="8";
        cmd_bytes[16]="7"; cmd_bytes[17]="0"; cmd_bytes[18]="0"; cmd_bytes[19]="0";
        cmd_bytes[20]="0"; cmd_bytes[21]=" "; cmd_bytes[22]="1"; cmd_bytes[23]="0";
        cmd_bytes[24]="0"; cmd_bytes[25]=" "; cmd_bytes[26]="5"; cmd_bytes[27]="0";
        cmd_bytes[28]="0"; cmd_bytes[29]=" "; cmd_bytes[30]="0"; cmd_bytes[31]=8'h0A;
        $display("TB: sending APPLY_PULSE 0 2870000 100 500 0 (expect OK)");
        mw_gate_seen = 1'b0;
        for (c = 0; c < 32; c = c + 1) begin sendb = cmd_bytes[c]; uart_send_byte(sendb); end
        #(400000*CLK_PERIOD);
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h4F && rx_log[1] === 8'h4B && rx_log[2] === 8'h0A) begin
            ok_count = ok_count + 1;
            $display("TB: APPLY_PULSE reply = OK\\n  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL APPLY_PULSE reply (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end
        // The RF-switch gate must pulse for the requested 500 ns (50 cycles).
        if (mw_gate_seen) begin
            ok_count = ok_count + 1;
            $display("TB: mw_gate pulsed after APPLY_PULSE  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL mw_gate did not pulse");
        end

        // ---- 6) APPLY_PULSE invalid freq (0) -> ERR --------------------
        rx_log_cnt = 0;
        cmd_bytes[0]="A";  cmd_bytes[1]="P";  cmd_bytes[2]="P";  cmd_bytes[3]="L";
        cmd_bytes[4]="Y";  cmd_bytes[5]="_";  cmd_bytes[6]="P";  cmd_bytes[7]="U";
        cmd_bytes[8]="L";  cmd_bytes[9]="S";  cmd_bytes[10]="E"; cmd_bytes[11]=" ";
        cmd_bytes[12]="0"; cmd_bytes[13]=" "; cmd_bytes[14]="0"; cmd_bytes[15]=" ";
        cmd_bytes[16]="1"; cmd_bytes[17]="0"; cmd_bytes[18]="0"; cmd_bytes[19]=" ";
        cmd_bytes[20]="5"; cmd_bytes[21]="0"; cmd_bytes[22]="0"; cmd_bytes[23]=" ";
        cmd_bytes[24]="0"; cmd_bytes[25]=8'h0A;
        $display("TB: sending APPLY_PULSE 0 0 100 500 0 (expect ERR)");
        for (c = 0; c < 26; c = c + 1) begin sendb = cmd_bytes[c]; uart_send_byte(sendb); end
        #(200000*CLK_PERIOD);
        if (rx_log_cnt >= 4 &&
            rx_log[0] === 8'h45 && rx_log[1] === 8'h52 &&
            rx_log[2] === 8'h52 && rx_log[3] === 8'h0A) begin
            ok_count = ok_count + 1;
            $display("TB: invalid APPLY_PULSE reply = ERR\\n  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL APPLY_PULSE ERR (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end

        // ---- 7) CALIBRATE -> OK -----------------------------------------
        // Drives all 64 emitter pixels (1 ms each) + reads the 64-electrode
        // sweep per pixel, building the LUT. With the MUX_SETTLE this is ~64
        // pixels * 64 electrodes * ~150 cycles ~= 600k cycles + 64 ms pump.
        rx_log_cnt = 0;
        cmd_bytes[0]="C";  cmd_bytes[1]="A";  cmd_bytes[2]="L";  cmd_bytes[3]="I";
        cmd_bytes[4]="B";  cmd_bytes[5]="R";  cmd_bytes[6]="A";  cmd_bytes[7]="T";
        cmd_bytes[8]="E";  cmd_bytes[9]=8'h0A;
        $display("TB: sending CALIBRATE");
        for (c = 0; c < 10; c = c + 1) begin sendb = cmd_bytes[c]; uart_send_byte(sendb); end
        // CALIBRATE drives 64 pixels * 1 ms pump (64 ms) + 64x64-elec sweep
        // (~6 ms) ~= 70 ms; wait 80 ms for the final OK\n.
        #(8000000*CLK_PERIOD);
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h4F && rx_log[1] === 8'h4B && rx_log[2] === 8'h0A) begin
            ok_count = ok_count + 1;
            $display("TB: CALIBRATE reply = OK\\n  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL CALIBRATE reply (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end

        if (err_count == 0)
            $display("TB: === ALL CHECKS PASSED ===");
        else
            $display("TB: === %0d CHECK(S) FAILED ===", err_count);

        $finish;
    end

    // ---- Safety timeout ----------------------------------------------------
    initial begin
        #500_000_000;   // 500 ms safety net (CALIBRATE completes in ~70 ms)
        $display("TB: TIMEOUT");
        $finish;
    end
endmodule