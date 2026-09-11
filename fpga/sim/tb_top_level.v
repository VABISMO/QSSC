`timescale 1ns / 1ps
//============================================================================
// tb_top_level.v
// ----------------------------------------------------------------------------
// Self-checking testbench for the QSSC single-board parallel top_level.
// Event-driven: it polls the captured UART-TX byte count rather than sleeping
// for fixed real-time windows, so it runs fast under the FAST_SIM define
// (CLKS_PER_BIT=8, emitter 1 ms = 10 clk) as well as at production timing.
//
// Detector readout is now 4x AD7616 parallel ADC (no photon counters, no
// det_in bus, no i2c threshold DAC). This bench instantiates a minimal
// behavioural AD7616 model: on each CONVST it asserts BUSY briefly, and when a
// chip CS is low it shifts a fixed 32-bit word (VA=1234, VB=1234) out on the
// shared SDOA (adc_miso), MSB first, on SCLK falling edges. adc_reader samples
// on SCLK rising edges -> every channel reads back 1234.
//
//   1. DRIVE_EMITTER <index> <ms>  -> TLC5947 gs[index]=4095 + em_blank low, OK\n
//   2. DRIVE_EMITTER 64 <ms>       -> out-of-range index, ERR\n
//   3. READ_DETECTOR 5             -> "1234\n" (AD7616 sample of channel 5)
//   4. READ_DETECTORS             -> "1234 1234 ... 1234\n" (64 samples)
//   5. CLEAR_DETECTORS -> OK\n; READ_DETECTOR 5 still "1234\n" (ADC no-op)
//   6. SET_THRESHOLD 2048         -> ERR\n (command removed by the ADC pivot)
//   7. APPLY_PULSE ...            -> OK\n + mw_gate pulse
//   8. APPLY_PULSE freq=0         -> ERR\n
//   9. SET_EMITTER / DRIVE_PATTERN -> parallel: arm 3 emitters, fire in parallel
//  10. CALIBRATE                  -> OK\n (64-pixel pump/read_all sweep)
// Emitters are 64 parallel CQD channels driven by 3x TLC5947 over 4-wire SPI
// (em_sclk/sin/xlat/blank) -- no 64-line emitter_en bus anymore.
//============================================================================
module tb_top_level;
`ifdef FAST_SIM
    localparam integer   CLK_PERIOD   = 10;         // 100 MHz -> 10 ns
    localparam integer   CLKS_PER_BIT = 8;          // fast UART in sim
    localparam integer   BIT_TIME     = CLK_PERIOD * CLKS_PER_BIT;
    localparam integer   W_EM_DONE    = 8000;       // > TLC load (~1728) + pump + end-load
    localparam integer   W_GENERIC    = 50000;      // command dispatch + reply
    localparam integer   W_ADC_SCAN   = 60000;      // one full 64-ch AD7616 scan
    localparam integer   W_CAL        = 900000;    // 64-pixel calibrate (each w/ TLC load + ADC sweep)
    localparam integer   W_SAFE       = 1500000;    // safety timeout
`else
    localparam integer   CLK_PERIOD   = 10;
    localparam integer   CLKS_PER_BIT = 868;        // 100 MHz / 115200
    localparam integer   BIT_TIME     = CLK_PERIOD * CLKS_PER_BIT;
    localparam integer   W_EM_DONE    = 600000;     // > 5 ms emitter pulse
    localparam integer   W_GENERIC    = 400000;
    localparam integer   W_ADC_SCAN   = 400000;     // full AD7616 scan at 25 MHz SCLK
    localparam integer   W_CAL        = 120000000;  // ~64 ms + margin @ real clk
    localparam integer   W_SAFE       = 220000000;  // 220 ms safety net
`endif

    // ---- DUT signals -------------------------------------------------------
    reg         clk_100mhz = 1'b0;
    reg         reset      = 1'b1;
    reg         rx         = 1'b1;       // UART idle
    wire        tx;
    // 4x AD7616 parallel ADC bus (9 wires) -- driven by the DUT, miso/busy
    // driven by the behavioural ADC model below.
    wire        adc_convst;
    wire        adc_sclk;
    reg         adc_miso    = 1'b0;
    reg         adc_busy    = 1'b0;
    wire [3:0]  adc_cs;
    wire        adc_resetn;
    wire        em_sclk;
    wire        em_sin;
    wire        em_xlat;
    wire        em_blank;
    wire        mod_sync;
    wire        mw_gate;
    wire [3:0]  spi_pins;
    wire        refclk_out;

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

    // ---- MW pulse-WIDTH timer: count cycles mw_gate is high, capture the
    // width of the last completed pulse. At 100 MHz the RTL gate timer ticks
    // every 10 ns (gate_cnt = duration_ns/10), so a 500 ns APPLY_PULSE must
    // produce a mw_gate pulse exactly 50 clk cycles wide. This is the timing
    // check that locks "the pulse lasts as long as qiskit asked for".
    integer mw_gate_high = 0;     // cycles high in the current pulse
    integer mw_gate_last = 0;     // width (cycles) of the last completed pulse
    always @(posedge clk_100mhz) begin
        if (mw_gate)        mw_gate_high <= mw_gate_high + 1;
        else if (mw_gate_high != 0) begin
            mw_gate_last <= mw_gate_high;
            mw_gate_high  <= 0;
        end
    end

    // ---- DUT ---------------------------------------------------------------
    top_level dut (
        .clk_100mhz (clk_100mhz),
        .reset      (reset),
        .rx         (rx),
        .tx         (tx),
        .adc_convst (adc_convst),
        .adc_sclk   (adc_sclk),
        .adc_miso   (adc_miso),
        .adc_busy   (adc_busy),
        .adc_cs     (adc_cs),
        .adc_resetn (adc_resetn),
        .em_sclk    (em_sclk),
        .em_sin     (em_sin),
        .em_xlat    (em_xlat),
        .em_blank   (em_blank),
        .mod_sync   (mod_sync),
        .spi_pins   (spi_pins),
        .refclk_out (refclk_out),
        .mw_gate    (mw_gate)
    );

    //========================================================================
    // Behavioural AD7616 model (4 chips share miso/busy; per-chip CS)
    //========================================================================
    // Every read returns a fixed 32-bit word: VA = ADC_VAL, VB = ADC_VAL, so
    // every channel's 16-bit sample = ADC_VAL. The model shifts MSB-first on
    // SCLK falling edges; adc_reader samples on SCLK rising edges.
    localparam [15:0] ADC_VAL  = 16'd1234;          // recognisable sample value
    localparam [31:0] ADC_WORD = {ADC_VAL, ADC_VAL};

    // QRNG test entropy: when entropy_mode is set, channel 0's VA sample (the
    // dark channel feeding qrng_entropy) gets a toggling LSB so the von-Neumann
    // extractor produces words. convst_cnt counts CONVST pulses; channel 0 is
    // refreshed on the first CONVST of each 8-CONVST scan, so convst_cnt[3]
    // toggles every scan -> alternating 0,1,0,1 LSB -> 100% von-Neumann keep.
    reg         entropy_mode = 1'b0;
    reg [19:0]  convst_cnt   = 20'd0;
    wire [15:0] va_word      = entropy_mode ? {ADC_VAL[15:1], convst_cnt[3]} : ADC_VAL;
    wire [31:0] adc_word_now = {va_word, ADC_VAL};

    reg [31:0] adc_sr;           // shift register driving adc_miso
    reg [6:0]  adc_bidx;         // bits shifted in the current CS-low session
    wire       any_cs_low = ~(&adc_cs);

    // adc_miso follows the MSB of the shift register.
    always @(*) adc_miso = adc_sr[31];

    // On each SCLK falling edge while a CS is low: load the word on the first
    // edge, then shift left so the next bit reaches the MSB.
    always @(negedge adc_sclk) begin
        if (any_cs_low) begin
            if (adc_bidx == 7'd0)  adc_sr <= adc_word_now;
            else                   adc_sr <= {adc_sr[30:0], 1'b0};
            adc_bidx <= adc_bidx + 7'd1;
        end
    end

    // Reset the bit counter whenever all CS deassert (gap between chips/pairs).
    always @(posedge adc_cs[0] or posedge adc_cs[1]
          or posedge adc_cs[2] or posedge adc_cs[3]) begin
        if (any_cs_low === 1'b0) adc_bidx <= 7'd0;
    end
    // Also reset on the convst edge so each conversion's readout starts clean.
    always @(posedge adc_convst) begin
        adc_bidx    <= 7'd0;
        convst_cnt  <= convst_cnt + 20'd1;
    end

    // CONVST -> BUSY: assert for a few cycles, then release (conversion done).
    reg [3:0] busy_cnt = 4'd0;
    always @(posedge clk_100mhz) begin
        if (reset) begin
            adc_busy <= 1'b0;
            busy_cnt <= 4'd0;
        end else if (adc_convst) begin
            adc_busy <= 1'b1;            // conversion starts
            busy_cnt <= 4'd0;
        end else if (adc_busy) begin
            busy_cnt <= busy_cnt + 4'd1;
            if (busy_cnt >= 4'd6) adc_busy <= 1'b0;   // ~6 cycles conversion
        end
    end

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

        // Let the adc_reader background FSM complete at least one full 64-ch
        // scan so sample_ram is populated before the first READ_DETECTOR.
        $display("TB: waiting for one AD7616 scan to populate sample_ram...");
        repeat(W_ADC_SCAN) @(posedge clk_100mhz);

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
        // The pump runs in the background after OK. The TLC5947 grayscale
        // register for channel 3 must be full (4095) and em_blank low (on).
        $display("TB: gs[3]=%0d em_blank=%b (expect 4095, 0)", dut.em_inst.tlc_inst.gs[3], em_blank);
        if (dut.em_inst.tlc_inst.gs[3] === 12'd4095 && em_blank === 1'b0) begin
            ok_count = ok_count + 1;  $display("TB: TLC5947 gs[3]=4095 + blank low  (PASS)");
        end else begin
            err_count = err_count + 1; $display("TB: FAIL emitter drive (gs[3] or blank)");
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

        // ---- 3) READ_DETECTOR 5 -> "1234\n" (AD7616 sample of channel 5)
        rx_log_cnt = 0;
        cmd_bytes[0]="R";  cmd_bytes[1]="E";  cmd_bytes[2]="A";  cmd_bytes[3]="D";
        cmd_bytes[4]="_";  cmd_bytes[5]="D";  cmd_bytes[6]="E";  cmd_bytes[7]="T";
        cmd_bytes[8]="E";  cmd_bytes[9]="C";  cmd_bytes[10]="T"; cmd_bytes[11]="O";
        cmd_bytes[12]="R"; cmd_bytes[13]=" "; cmd_bytes[14]="5"; cmd_bytes[15]=8'h0A;
        $display("TB: sending READ_DETECTOR 5 (expect 1234)");
        for (c=0;c<16;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(5, W_GENERIC);        // "1234\n"
        $display("TB: READ_DETECTOR captured %0d bytes", rx_log_cnt);
        if (rx_log_cnt >= 5 &&
            rx_log[0] === 8'h31 && rx_log[1] === 8'h32 &&
            rx_log[2] === 8'h33 && rx_log[3] === 8'h34 &&
            rx_log[4] === 8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: READ_DETECTOR 5 = \"1234\\n\"  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL READ_DETECTOR reply (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end

        // ---- 4) READ_DETECTORS -> 64 samples "1234", space-separated ------
        rx_log_cnt = 0;
        cmd_bytes[0]="R";  cmd_bytes[1]="E";  cmd_bytes[2]="A";  cmd_bytes[3]="D";
        cmd_bytes[4]="_";  cmd_bytes[5]="D";  cmd_bytes[6]="E";  cmd_bytes[7]="T";
        cmd_bytes[8]="E";  cmd_bytes[9]="C";  cmd_bytes[10]="T"; cmd_bytes[11]="O";
        cmd_bytes[12]="R"; cmd_bytes[13]="S"; cmd_bytes[14]=8'h0A;
        $display("TB: sending READ_DETECTORS (expect 64 x \"1234\")");
        for (c=0;c<15;c=c+1) uart_send_byte(cmd_bytes[c]);
        // 64 samples * ("1234" + sep) = 64*5 = 320 bytes (63 spaces + 1 newline)
        wait_for_bytes(320, W_GENERIC*6);
        $display("TB: READ_DETECTORS captured %0d bytes", rx_log_cnt);
        if (rx_log_cnt >= 5 &&
            rx_log[0] === 8'h31 && rx_log[1] === 8'h32 &&
            rx_log[2] === 8'h33 && rx_log[3] === 8'h34 &&
            rx_log[4] === 8'h20) begin
            ok_count = ok_count + 1;  $display("TB: detector stream starts \"1234 \"  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL detector prefix (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end
        if (rx_log_cnt === 320) begin
            ok_count = ok_count + 1;  $display("TB: detector stream length = 320 bytes  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL detector stream length (got %0d, expect 320)", rx_log_cnt);
        end
        if (rx_log_cnt >= 1 && rx_log[rx_log_cnt-1] === 8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: detector stream ends '\\n'  (PASS)");
        end else begin
            err_count = err_count + 1;  $display("TB: FAIL detector stream terminator");
        end

        // ---- 5) CLEAR_DETECTORS -> OK; READ_DETECTOR 5 STILL "1234" (ADC no-op)
        rx_log_cnt = 0;
        cmd_bytes[0]="C";  cmd_bytes[1]="L";  cmd_bytes[2]="E";  cmd_bytes[3]="A";
        cmd_bytes[4]="R";  cmd_bytes[5]="_";  cmd_bytes[6]="D";  cmd_bytes[7]="E";
        cmd_bytes[8]="T";  cmd_bytes[9]="E";  cmd_bytes[10]="C"; cmd_bytes[11]="T";
        cmd_bytes[12]="O"; cmd_bytes[13]="R"; cmd_bytes[14]="S"; cmd_bytes[15]=8'h0A;
        $display("TB: sending CLEAR_DETECTORS (ADC no-op, expect OK)");
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
        // The AD7616 keeps sampling live, so the sample is still 1234 after
        // "clear" -- this proves CLEAR_DETECTORS is an honest no-op on the ADC.
        rx_log_cnt = 0;
        cmd_bytes[0]="R";  cmd_bytes[1]="E";  cmd_bytes[2]="A";  cmd_bytes[3]="D";
        cmd_bytes[4]="_";  cmd_bytes[5]="D";  cmd_bytes[6]="E";  cmd_bytes[7]="T";
        cmd_bytes[8]="E";  cmd_bytes[9]="C";  cmd_bytes[10]="T"; cmd_bytes[11]="O";
        cmd_bytes[12]="R"; cmd_bytes[13]=" "; cmd_bytes[14]="5"; cmd_bytes[15]=8'h0A;
        $display("TB: sending READ_DETECTOR 5 (expect 1234, unchanged after clear)");
        for (c=0;c<16;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(5, W_GENERIC);         // "1234\n"
        if (rx_log_cnt >= 5 &&
            rx_log[0] === 8'h31 && rx_log[1] === 8'h32 &&
            rx_log[2] === 8'h33 && rx_log[3] === 8'h34 &&
            rx_log[4] === 8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: READ_DETECTOR 5 after clear still \"1234\\n\"  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL post-clear read (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end

        // ---- 6) SET_THRESHOLD 2048 -> ERR (command removed by the ADC pivot)
        rx_log_cnt = 0;
        cmd_bytes[0]="S";  cmd_bytes[1]="E";  cmd_bytes[2]="T";  cmd_bytes[3]="_";
        cmd_bytes[4]="T";  cmd_bytes[5]="H";  cmd_bytes[6]="R";  cmd_bytes[7]="E";
        cmd_bytes[8]="S";  cmd_bytes[9]="H";  cmd_bytes[10]="O"; cmd_bytes[11]="L";
        cmd_bytes[12]="D"; cmd_bytes[13]=" "; cmd_bytes[14]="2"; cmd_bytes[15]="0";
        cmd_bytes[16]="4"; cmd_bytes[17]="8"; cmd_bytes[18]=8'h0A;
        $display("TB: sending SET_THRESHOLD 2048 (removed -> expect ERR)");
        for (c=0;c<19;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(4, W_GENERIC);         // ERR\n (unknown keyword)
        if (rx_log_cnt >= 4 &&
            rx_log[0] === 8'h45 && rx_log[1] === 8'h52 &&
            rx_log[2] === 8'h52 && rx_log[3] === 8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: SET_THRESHOLD (removed) = ERR\\n  (PASS)");
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
        // Timing check: the 500 ns pulse (duration_ns=500) must gate the RF
        // switch for exactly 50 clk cycles (100 MHz -> 10 ns/tick). The OK
        // reply took ~240 cycles to clock out, so the 50-cycle gate has long
        // completed and mw_gate_last holds its width.
        if (mw_gate_last == 50) begin
            ok_count = ok_count + 1;
            $display("TB: mw_gate pulse width = %0d cycles (500 ns -> 50 @10ns/tick)  (PASS)", mw_gate_last);
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL mw_gate pulse width = %0d cycles (expect 50 for 500 ns)", mw_gate_last);
        end

        // ---- 7b) APPLY_PULSE non-zero phase: mrad -> ADF4351 R1 PHASE ----
        // Host sends phase in milliradians; the RTL must rescale to the 12-bit
        // R1 PHASE register (4095 = 360 deg). pi = 3142 mrad -> 180 deg ->
        // (3142*4096)/6283 = 2048. The old raw-truncation path wrote 3142
        // (-> ~276 deg); this locks the corrected mrad_to_phase scaling.
        rx_log_cnt = 0;
        cmd_bytes[0]="A";  cmd_bytes[1]="P";  cmd_bytes[2]="P";  cmd_bytes[3]="L";
        cmd_bytes[4]="Y";  cmd_bytes[5]="_";  cmd_bytes[6]="P";  cmd_bytes[7]="U";
        cmd_bytes[8]="L";  cmd_bytes[9]="S";  cmd_bytes[10]="E"; cmd_bytes[11]=" ";
        cmd_bytes[12]="0"; cmd_bytes[13]=" "; cmd_bytes[14]="2"; cmd_bytes[15]="8";
        cmd_bytes[16]="7"; cmd_bytes[17]="0"; cmd_bytes[18]="0"; cmd_bytes[19]="0";
        cmd_bytes[20]="0"; cmd_bytes[21]=" "; cmd_bytes[22]="1"; cmd_bytes[23]="0";
        cmd_bytes[24]="0"; cmd_bytes[25]=" "; cmd_bytes[26]="5"; cmd_bytes[27]="0";
        cmd_bytes[28]="0"; cmd_bytes[29]=" "; cmd_bytes[30]="3"; cmd_bytes[31]="1";
        cmd_bytes[32]="4"; cmd_bytes[33]="2"; cmd_bytes[34]=8'h0A;
        $display("TB: sending APPLY_PULSE 0 2870000 100 500 3142 (pi mrad -> R1 PHASE=2048)");
        for (c=0;c<35;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(3, W_GENERIC);         // OK\n
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h4F && rx_log[1] === 8'h4B && rx_log[2] === 8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: APPLY_Pulse pi-phase = OK\\n  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL APPLY_PULSE pi-phase reply (got %0d bytes)", rx_log_cnt);
        end
        if (dut.parser_inst.pll_phase === 12'd2048) begin
            ok_count = ok_count + 1;
            $display("TB: pll_phase = %0d (3142 mrad -> 2048 = 180 deg)  (PASS)", dut.parser_inst.pll_phase);
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL pll_phase = %0d (expect 2048 for 3142 mrad / pi)", dut.parser_inst.pll_phase);
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

        // ---- 9) SET_EMITTER + DRIVE_PATTERN: parallel multi-emitter -------
        // Arm emitters 0, 10 and 20 at full grayscale, then DRIVE_PATTERN to
        // fire all three SIMULTANEOUSLY (the parallel-at-FPGA-speed check).
        // SET_EMITTER 0 4095
        rx_log_cnt = 0;
        cmd_bytes[0]="S";  cmd_bytes[1]="E";  cmd_bytes[2]="T";  cmd_bytes[3]="_";
        cmd_bytes[4]="E";  cmd_bytes[5]="M";  cmd_bytes[6]="I";  cmd_bytes[7]="T";
        cmd_bytes[8]="T";  cmd_bytes[9]="E";  cmd_bytes[10]="R"; cmd_bytes[11]=" ";
        cmd_bytes[12]="0"; cmd_bytes[13]=" "; cmd_bytes[14]="4"; cmd_bytes[15]="0";
        cmd_bytes[16]="9"; cmd_bytes[17]="5"; cmd_bytes[18]=8'h0A;
        $display("TB: sending SET_EMITTER 0 4095");
        for (c=0;c<19;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(3, W_GENERIC);        // OK\n (after TLC load)
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h4F && rx_log[1] === 8'h4B && rx_log[2] === 8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: SET_EMITTER 0 = OK\\n  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL SET_EMITTER 0 reply (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end
        // SET_EMITTER 10 4095
        rx_log_cnt = 0;
        cmd_bytes[0]="S";  cmd_bytes[1]="E";  cmd_bytes[2]="T";  cmd_bytes[3]="_";
        cmd_bytes[4]="E";  cmd_bytes[5]="M";  cmd_bytes[6]="I";  cmd_bytes[7]="T";
        cmd_bytes[8]="T";  cmd_bytes[9]="E";  cmd_bytes[10]="R"; cmd_bytes[11]=" ";
        cmd_bytes[12]="1"; cmd_bytes[13]="0"; cmd_bytes[14]=" "; cmd_bytes[15]="4";
        cmd_bytes[16]="0"; cmd_bytes[17]="9"; cmd_bytes[18]="5"; cmd_bytes[19]=8'h0A;
        $display("TB: sending SET_EMITTER 10 4095");
        for (c=0;c<20;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(3, W_GENERIC);
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h4F && rx_log[1] === 8'h4B && rx_log[2] === 8'h0A)
            ok_count = ok_count + 1;
        else begin
            err_count = err_count + 1; $display("TB: FAIL SET_EMITTER 10 reply");
        end
        // SET_EMITTER 20 4095
        rx_log_cnt = 0;
        cmd_bytes[0]="S";  cmd_bytes[1]="E";  cmd_bytes[2]="T";  cmd_bytes[3]="_";
        cmd_bytes[4]="E";  cmd_bytes[5]="M";  cmd_bytes[6]="I";  cmd_bytes[7]="T";
        cmd_bytes[8]="T";  cmd_bytes[9]="E";  cmd_bytes[10]="R"; cmd_bytes[11]=" ";
        cmd_bytes[12]="2"; cmd_bytes[13]="0"; cmd_bytes[14]=" "; cmd_bytes[15]="4";
        cmd_bytes[16]="0"; cmd_bytes[17]="9"; cmd_bytes[18]="5"; cmd_bytes[19]=8'h0A;
        $display("TB: sending SET_EMITTER 20 4095");
        for (c=0;c<20;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(3, W_GENERIC);
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h4F && rx_log[1] === 8'h4B && rx_log[2] === 8'h0A)
            ok_count = ok_count + 1;
        else begin
            err_count = err_count + 1; $display("TB: FAIL SET_EMITTER 20 reply");
        end
        // DRIVE_PATTERN 100 -> fire all three armed emitters in parallel
        rx_log_cnt = 0;
        cmd_bytes[0]="D";  cmd_bytes[1]="R";  cmd_bytes[2]="I";  cmd_bytes[3]="V";
        cmd_bytes[4]="E";  cmd_bytes[5]="_";  cmd_bytes[6]="P";  cmd_bytes[7]="A";
        cmd_bytes[8]="T";  cmd_bytes[9]="T";  cmd_bytes[10]="E"; cmd_bytes[11]="R";
        cmd_bytes[12]="N"; cmd_bytes[13]=" "; cmd_bytes[14]="1"; cmd_bytes[15]="0";
        cmd_bytes[16]="0"; cmd_bytes[17]=8'h0A;
        $display("TB: sending DRIVE_PATTERN 100 (3 emitters in parallel)");
        for (c=0;c<18;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(3, W_GENERIC);        // OK\n (pump runs in background)
        // The parallel check: gs[0]=gs[10]=gs[20]=4095 AND em_blank low, all at
        // once -- multiple emitters armed and firing simultaneously.
        $display("TB: gs[0]=%0d gs[10]=%0d gs[20]=%0d blank=%b (expect 4095 each, 0)",
                 dut.em_inst.tlc_inst.gs[0], dut.em_inst.tlc_inst.gs[10],
                 dut.em_inst.tlc_inst.gs[20], em_blank);
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h4F && rx_log[1] === 8'h4B && rx_log[2] === 8'h0A &&
            dut.em_inst.tlc_inst.gs[0]  === 12'd4095 &&
            dut.em_inst.tlc_inst.gs[10] === 12'd4095 &&
            dut.em_inst.tlc_inst.gs[20] === 12'd4095 &&
            em_blank === 1'b0) begin
            ok_count = ok_count + 1;  $display("TB: 3 emitters fire in parallel  (PASS)");
        end else begin
            err_count = err_count + 1; $display("TB: FAIL parallel DRIVE_PATTERN");
        end
        repeat(W_EM_DONE) @(posedge clk_100mhz);   // let the pattern pump finish
        // CLEAR_EMITTERS -> OK, all gs back to 0
        rx_log_cnt = 0;
        cmd_bytes[0]="C";  cmd_bytes[1]="L";  cmd_bytes[2]="E";  cmd_bytes[3]="A";
        cmd_bytes[4]="R";  cmd_bytes[5]="_";  cmd_bytes[6]="E";  cmd_bytes[7]="M";
        cmd_bytes[8]="I";  cmd_bytes[9]="T";  cmd_bytes[10]="T"; cmd_bytes[11]="E";
        cmd_bytes[12]="R"; cmd_bytes[13]="S"; cmd_bytes[14]=8'h0A;
        $display("TB: sending CLEAR_EMITTERS");
        for (c=0;c<15;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(3, W_GENERIC);        // OK\n (after TLC load of all-zero)
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h4F && rx_log[1] === 8'h4B && rx_log[2] === 8'h0A &&
            dut.em_inst.tlc_inst.gs[0]  === 12'd0 &&
            dut.em_inst.tlc_inst.gs[10] === 12'd0 &&
            dut.em_inst.tlc_inst.gs[20] === 12'd0) begin
            ok_count = ok_count + 1;  $display("TB: CLEAR_EMITTERS = OK, gs=0  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL CLEAR_EMITTERS (got %0d bytes, gs0=%0d)",
                     rx_log_cnt, dut.em_inst.tlc_inst.gs[0]);
        end

        // ---- 10) CALIBRATE -> OK -----------------------------------------
        // 64 pixels * (1-ms-equivalent pump + 64-channel ADC read_all sweep).
        // Under FAST_SIM the whole sweep is tens of thousands of cycles (each
        // read_all waits on the background ADC scan); under real timing it is
        // ~65 ms. wait_for_bytes with a large timeout handles both.
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

        // ---- 11) READ_RNG 4 -> 8 hex nibbles + " 00064 0 0\n" ---------------
        // Enable the toggling-LSB entropy model so the dark channel (ch 0) feeds
        // the von-Neumann extractor. 4 bytes = 1 x 32-bit word = 8 hex chars,
        // then the stats footer " 00064 0 0\n" (11 bytes) -> 19 bytes total. The
        // footer's decimal field is the raw-sample count this request consumed:
        // 32 kept bits x 2 samples/pair = 64 samples -> "00064". kept/rate stay
        // "0" placeholders; the host derives them. 64 scans (~64*W_ADC_SCAN
        // cycles, well under W_SAFE and the parser's 1.34 s watchdog).
        entropy_mode = 1'b1;
        rx_log_cnt = 0;
        cmd_bytes[0]="R";  cmd_bytes[1]="E";  cmd_bytes[2]="A";  cmd_bytes[3]="D";
        cmd_bytes[4]="_";  cmd_bytes[5]="R";  cmd_bytes[6]="N";  cmd_bytes[7]="G";
        cmd_bytes[8]=" ";  cmd_bytes[9]="4";  cmd_bytes[10]=8'h0A;
        $display("TB: sending READ_RNG 4");
        for (c=0;c<11;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(19, W_SAFE);
        // Reply must be 8 hex chars (0-9a-f) followed by " 00064 0 0\n".
        if (rx_log_cnt >= 19 &&
            ((rx_log[0] >= 8'h30 && rx_log[0] <= 8'h39) ||
             (rx_log[0] >= 8'h61 && rx_log[0] <= 8'h66)) &&
            rx_log[8]  === 8'h20 && rx_log[9]  === 8'h30 &&
            rx_log[10] === 8'h30 && rx_log[11] === 8'h30 &&
            rx_log[12] === 8'h36 && rx_log[13] === 8'h34 &&
            rx_log[14] === 8'h20 && rx_log[15] === 8'h30 &&
            rx_log[16] === 8'h20 && rx_log[17] === 8'h30 &&
            rx_log[18] === 8'h0A) begin
            ok_count = ok_count + 1;
            $display("TB: READ_RNG 4 = 8 hex + \" 00064 0 0\" footer (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL READ_RNG 4 (got %0d bytes, b0=0x%02h)",
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