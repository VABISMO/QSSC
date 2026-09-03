`timescale 1ns / 1ps
//============================================================================
// tb_top_level.v
// ----------------------------------------------------------------------------
// Self-checking testbench for the QSSC single-board parallel top_level.
// Event-driven: it polls the captured UART-TX byte count rather than sleeping
// for fixed real-time windows, so it runs fast under the FAST_SIM define
// (CLKS_PER_BIT=8, emitter 1 ms = 10 clk) as well as at production timing.
//
//   1. DRIVE_EMITTER <index> <ms>  -> one-hot emitter_en[index], OK\n
//   2. DRIVE_EMITTER 64 <ms>       -> out-of-range index, ERR\n
//   3. drive 12 rising edges on det_in[63:0] -> READ_DETECTOR 5 = "12\n"
//   4. READ_DETECTORS             -> "12 12 ... 12\n" (64 codes, 192 bytes)
//   5. CLEAR_DETECTORS            -> OK\n, then READ_DETECTOR 5 = "0\n"
//   6. SET_THRESHOLD 2048         -> OK\n (MCP4725 I2C write completes)
//   7. APPLY_PULSE ...            -> OK\n + mw_gate pulse
//   8. APPLY_PULSE freq=0         -> ERR\n
//   9. CALIBRATE                  -> OK\n (64-pixel clear/pump/read_all sweep)
// No camera, no analog MUX, no XADC: 64 parallel photon counters.
//============================================================================
module tb_top_level;
`ifdef FAST_SIM
    localparam integer   CLK_PERIOD   = 10;         // 100 MHz -> 10 ns
    localparam integer   CLKS_PER_BIT = 8;          // fast UART in sim
    localparam integer   BIT_TIME     = CLK_PERIOD * CLKS_PER_BIT;
    // fixed inter-test waits, in clock cycles (emitter finish, etc.)
    localparam integer   W_EM_DONE    = 3000;       // > emitter pulse (1000 cyc)
    localparam integer   W_GENERIC    = 50000;      // command dispatch + reply
    localparam integer   W_CAL        = 200000;    // 64-pixel calibrate sweep
    localparam integer   W_SAFE       = 400000;     // safety timeout
`else
    localparam integer   CLK_PERIOD   = 10;
    localparam integer   CLKS_PER_BIT = 868;        // 100 MHz / 115200
    localparam integer   BIT_TIME     = CLK_PERIOD * CLKS_PER_BIT;
    localparam integer   W_EM_DONE    = 600000;     // > 5 ms emitter pulse
    localparam integer   W_GENERIC    = 400000;
    localparam integer   W_CAL        = 100000000;  // ~64 ms + margin @ real clk
    localparam integer   W_SAFE       = 200000000;  // 200 ms safety net
`endif

    // ---- DUT signals -------------------------------------------------------
    reg         clk_100mhz = 1'b0;
    reg         reset      = 1'b1;
    reg         rx         = 1'b1;       // UART idle
    wire        tx;
    reg  [63:0] det_in     = 64'd0;      // 64 comparator outputs (stimulus)
    wire [63:0] emitter_en;
    wire        mod_sync;
    wire        mw_gate;
    wire [3:0]  spi_pins;
    wire        refclk_out;
    wire        i2c_sda;
    wire        i2c_scl;

    // ---- Captured TX bytes -------------------------------------------------
    reg [7:0]   rx_log [0:1023];
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
        .clk_100mhz (clk_100mhz),
        .reset      (reset),
        .rx         (rx),
        .tx         (tx),
        .det_in     (det_in),
        .emitter_en (emitter_en),
        .mod_sync   (mod_sync),
        .spi_pins   (spi_pins),
        .refclk_out (refclk_out),
        .mw_gate    (mw_gate),
        .i2c_sda    (i2c_sda),
        .i2c_scl    (i2c_scl)
    );

    // open-drain I2C bus with a weak pull-up so released lines read high.
    pullup sda_pu (i2c_sda);
    pullup scl_pu (i2c_scl);

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

    // ---- drive N rising edges on every det_in bit (a photon burst) --------
    task pulse_det_all;
        input integer n;
        integer j;
        begin
            for (j = 0; j < n; j = j + 1) begin
                det_in = 64'hFFFFFFFFFFFFFFFF;
                repeat(5) @(posedge clk_100mhz);
                det_in = 64'd0;
                repeat(5) @(posedge clk_100mhz);
            end
        end
    endtask

    // ---- wait until at least `n` TX bytes captured, or `tmo` cycles --------
    task wait_for_bytes;
        input integer n;
        input integer tmo;
        integer t;
        begin
            t = 0;
            while (rx_log_cnt < n && t < tmo) begin
                @(posedge clk_100mhz);
                t = t + 1;
            end
            if (t >= tmo)
                $display("TB: WARNING wait_for_bytes(%0d) timeout after %0d cyc (got %0d)",
                         n, tmo, rx_log_cnt);
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
                if (rx_log_cnt < 1024) begin
                    rx_log[rx_log_cnt] = rx_byte_cap;
                    rx_log_cnt = rx_log_cnt + 1;
                end
            end
        end
    end

    // ---- Main stimulus -----------------------------------------------------
    integer r;
    integer c;
    reg [7:0] cmd_bytes [0:39];
    initial begin
        // Reset
        reset = 1'b1;
        for (r=0;r<20;r=r+1) @(posedge clk_100mhz);
        reset = 1'b0;
        for (r=0;r<5;r=r+1) @(posedge clk_100mhz);

        // ---- 1) DRIVE_EMITTER 3 100 (index=3, 100 "ms" pump) ---------------
        // 100 ms > OK-TX time so the emitter is still firing when OK arrives.
        rx_log_cnt = 0;
        cmd_bytes[0]="D";  cmd_bytes[1]="R";  cmd_bytes[2]="I";  cmd_bytes[3]="V";
        cmd_bytes[4]="E";  cmd_bytes[5]="_";  cmd_bytes[6]="E";  cmd_bytes[7]="M";
        cmd_bytes[8]="I";  cmd_bytes[9]="T";  cmd_bytes[10]="T"; cmd_bytes[11]="E";
        cmd_bytes[12]="R"; cmd_bytes[13]=" "; cmd_bytes[14]="3"; cmd_bytes[15]=" ";
        cmd_bytes[16]="1"; cmd_bytes[17]="0"; cmd_bytes[18]="0"; cmd_bytes[19]=8'h0A;
        $display("TB: sending DRIVE_EMITTER 3 100");
        for (c=0;c<20;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(3, W_GENERIC);       // OK\n
        $display("TB: emitter_en = 0x%016h (expect bit 3 set, one-hot)", emitter_en);
        if (emitter_en[3] === 1'b1 && (emitter_en & ~64'h8) === 64'd0) begin
            ok_count = ok_count + 1;  $display("TB: emitter_en one-hot on bit 3  (PASS)");
        end else begin
            err_count = err_count + 1; $display("TB: FAIL emitter_en pattern");
        end
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h4F && rx_log[1] === 8'h4B && rx_log[2] === 8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: DRIVE_EMITTER reply = OK\\n  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL DRIVE_EMITTER reply (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end
        repeat(W_EM_DONE) @(posedge clk_100mhz);   // let the emitter pulse finish

        // ---- 2) DRIVE_EMITTER 64 100 -> ERR (index out of range 0..63) -----
        rx_log_cnt = 0;
        cmd_bytes[0]="D";  cmd_bytes[1]="R";  cmd_bytes[2]="I";  cmd_bytes[3]="V";
        cmd_bytes[4]="E";  cmd_bytes[5]="_";  cmd_bytes[6]="E";  cmd_bytes[7]="M";
        cmd_bytes[8]="I";  cmd_bytes[9]="T";  cmd_bytes[10]="T"; cmd_bytes[11]="E";
        cmd_bytes[12]="R"; cmd_bytes[13]=" "; cmd_bytes[14]="6"; cmd_bytes[15]="4";
        cmd_bytes[16]=" "; cmd_bytes[17]="1"; cmd_bytes[18]="0"; cmd_bytes[19]="0";
        cmd_bytes[20]=8'h0A;
        $display("TB: sending DRIVE_EMITTER 64 100 (expect ERR)");
        for (c=0;c<21;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(4, W_GENERIC);        // ERR\n
        if (rx_log_cnt >= 4 &&
            rx_log[0] === 8'h45 && rx_log[1] === 8'h52 &&
            rx_log[2] === 8'h52 && rx_log[3] === 8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: out-of-range DRIVE_EMITTER = ERR\\n  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL bounds-check ERR (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end

        // ---- 3) drive 12 photon events on all 64 channels, READ_DETECTOR 5
        pulse_det_all(12);
        rx_log_cnt = 0;
        cmd_bytes[0]="R";  cmd_bytes[1]="E";  cmd_bytes[2]="A";  cmd_bytes[3]="D";
        cmd_bytes[4]="_";  cmd_bytes[5]="D";  cmd_bytes[6]="E";  cmd_bytes[7]="T";
        cmd_bytes[8]="E";  cmd_bytes[9]="C";  cmd_bytes[10]="T"; cmd_bytes[11]="O";
        cmd_bytes[12]="R"; cmd_bytes[13]=" "; cmd_bytes[14]="5"; cmd_bytes[15]=8'h0A;
        $display("TB: sending READ_DETECTOR 5 (expect 12)");
        for (c=0;c<16;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(3, W_GENERIC);        // "12\n"
        $display("TB: READ_DETECTOR captured %0d bytes", rx_log_cnt);
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h31 && rx_log[1] === 8'h32 && rx_log[2] === 8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: READ_DETECTOR 5 = \"12\\n\"  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL READ_DETECTOR reply (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end

        // ---- 4) READ_DETECTORS -> 64 codes "12", space-separated ----------
        rx_log_cnt = 0;
        cmd_bytes[0]="R";  cmd_bytes[1]="E";  cmd_bytes[2]="A";  cmd_bytes[3]="D";
        cmd_bytes[4]="_";  cmd_bytes[5]="D";  cmd_bytes[6]="E";  cmd_bytes[7]="T";
        cmd_bytes[8]="E";  cmd_bytes[9]="C";  cmd_bytes[10]="T"; cmd_bytes[11]="O";
        cmd_bytes[12]="R"; cmd_bytes[13]="S"; cmd_bytes[14]=8'h0A;
        $display("TB: sending READ_DETECTORS (expect 64 x \"12\")");
        for (c=0;c<15;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(192, W_GENERIC*4);     // 64 codes * ("12" + sep) = 192 bytes
        $display("TB: READ_DETECTORS captured %0d bytes", rx_log_cnt);
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h31 && rx_log[1] === 8'h32 && rx_log[2] === 8'h20) begin
            ok_count = ok_count + 1;  $display("TB: detector stream starts \"12 \"  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL detector prefix (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end
        if (rx_log_cnt === 192) begin
            ok_count = ok_count + 1;  $display("TB: detector stream length = 192 bytes  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL detector stream length (got %0d, expect 192)", rx_log_cnt);
        end
        if (rx_log_cnt >= 1 && rx_log[rx_log_cnt-1] === 8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: detector stream ends '\\n'  (PASS)");
        end else begin
            err_count = err_count + 1;  $display("TB: FAIL detector stream terminator");
        end

        // ---- 5) CLEAR_DETECTORS -> OK, then READ_DETECTOR 5 = "0" ----------
        rx_log_cnt = 0;
        cmd_bytes[0]="C";  cmd_bytes[1]="L";  cmd_bytes[2]="E";  cmd_bytes[3]="A";
        cmd_bytes[4]="R";  cmd_bytes[5]="_";  cmd_bytes[6]="D";  cmd_bytes[7]="E";
        cmd_bytes[8]="T";  cmd_bytes[9]="E";  cmd_bytes[10]="C"; cmd_bytes[11]="T";
        cmd_bytes[12]="O"; cmd_bytes[13]="R"; cmd_bytes[14]="S"; cmd_bytes[15]=8'h0A;
        $display("TB: sending CLEAR_DETECTORS");
        for (c=0;c<16;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(3, W_GENERIC);         // OK\n
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h4F && rx_log[1] === 8'h4B && rx_log[2] === 8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: CLEAR_DETECTORS = OK\\n  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL CLEAR_DETECTORS reply (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end
        // counters now zero
        rx_log_cnt = 0;
        cmd_bytes[0]="R";  cmd_bytes[1]="E";  cmd_bytes[2]="A";  cmd_bytes[3]="D";
        cmd_bytes[4]="_";  cmd_bytes[5]="D";  cmd_bytes[6]="E";  cmd_bytes[7]="T";
        cmd_bytes[8]="E";  cmd_bytes[9]="C";  cmd_bytes[10]="T"; cmd_bytes[11]="O";
        cmd_bytes[12]="R"; cmd_bytes[13]=" "; cmd_bytes[14]="5"; cmd_bytes[15]=8'h0A;
        $display("TB: sending READ_DETECTOR 5 (expect 0 after clear)");
        for (c=0;c<16;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(2, W_GENERIC);         // "0\n"
        if (rx_log_cnt >= 2 &&
            rx_log[0] === 8'h30 && rx_log[1] === 8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: READ_DETECTOR 5 after clear = \"0\\n\"  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL post-clear read (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end

        // ---- 6) SET_THRESHOLD 2048 -> OK (MCP4725 I2C write) --------------
        rx_log_cnt = 0;
        cmd_bytes[0]="S";  cmd_bytes[1]="E";  cmd_bytes[2]="T";  cmd_bytes[3]="_";
        cmd_bytes[4]="T";  cmd_bytes[5]="H";  cmd_bytes[6]="R";  cmd_bytes[7]="E";
        cmd_bytes[8]="S";  cmd_bytes[9]="H";  cmd_bytes[10]="O"; cmd_bytes[11]="L";
        cmd_bytes[12]="D"; cmd_bytes[13]=" "; cmd_bytes[14]="2"; cmd_bytes[15]="0";
        cmd_bytes[16]="4"; cmd_bytes[17]="8"; cmd_bytes[18]=8'h0A;
        $display("TB: sending SET_THRESHOLD 2048 (expect OK)");
        for (c=0;c<19;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(3, W_GENERIC);         // OK\n (after I2C transaction)
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h4F && rx_log[1] === 8'h4B && rx_log[2] === 8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: SET_THRESHOLD = OK\\n  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL SET_THRESHOLD reply (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end

        // ---- 7) APPLY_PULSE valid -> OK + mw_gate pulse ----------------
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
        for (c=0;c<32;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(3, W_GENERIC);         // OK\n
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h4F && rx_log[1] === 8'h4B && rx_log[2] === 8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: APPLY_PULSE = OK\\n  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL APPLY_PULSE reply (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end
        if (mw_gate_seen) begin
            ok_count = ok_count + 1;  $display("TB: mw_gate pulsed after APPLY_PULSE  (PASS)");
        end else begin
            err_count = err_count + 1;  $display("TB: FAIL mw_gate did not pulse");
        end

        // ---- 8) APPLY_PULSE invalid freq (0) -> ERR --------------------
        rx_log_cnt = 0;
        cmd_bytes[0]="A";  cmd_bytes[1]="P";  cmd_bytes[2]="P";  cmd_bytes[3]="L";
        cmd_bytes[4]="Y";  cmd_bytes[5]="_";  cmd_bytes[6]="P";  cmd_bytes[7]="U";
        cmd_bytes[8]="L";  cmd_bytes[9]="S";  cmd_bytes[10]="E"; cmd_bytes[11]=" ";
        cmd_bytes[12]="0"; cmd_bytes[13]=" "; cmd_bytes[14]="0"; cmd_bytes[15]=" ";
        cmd_bytes[16]="1"; cmd_bytes[17]="0"; cmd_bytes[18]="0"; cmd_bytes[19]=" ";
        cmd_bytes[20]="5"; cmd_bytes[21]="0"; cmd_bytes[22]="0"; cmd_bytes[23]=" ";
        cmd_bytes[24]="0"; cmd_bytes[25]=8'h0A;
        $display("TB: sending APPLY_PULSE 0 0 100 500 0 (expect ERR)");
        for (c=0;c<26;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(4, W_GENERIC);         // ERR\n
        if (rx_log_cnt >= 4 &&
            rx_log[0] === 8'h45 && rx_log[1] === 8'h52 &&
            rx_log[2] === 8'h52 && rx_log[3] === 8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: invalid APPLY_PULSE = ERR\\n  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL APPLY_PULSE ERR (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end

        // ---- 9) CALIBRATE -> OK -----------------------------------------
        // 64 pixels * (clear + 1-ms-equivalent pump + 64-channel internal
        // read_all sweep). Under FAST_SIM the whole sweep is a few thousand
        // cycles; under real timing it is ~65 ms (wait_for_bytes with a large
        // timeout handles both).
        rx_log_cnt = 0;
        cmd_bytes[0]="C";  cmd_bytes[1]="A";  cmd_bytes[2]="L";  cmd_bytes[3]="I";
        cmd_bytes[4]="B";  cmd_bytes[5]="R";  cmd_bytes[6]="A";  cmd_bytes[7]="T";
        cmd_bytes[8]="E";  cmd_bytes[9]=8'h0A;
        $display("TB: sending CALIBRATE");
        for (c=0;c<10;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(3, W_CAL);              // OK\n
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h4F && rx_log[1] === 8'h4B && rx_log[2] === 8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: CALIBRATE = OK\\n  (PASS)");
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
        #(W_SAFE * CLK_PERIOD);
        $display("TB: TIMEOUT");
        $finish;
    end
endmodule