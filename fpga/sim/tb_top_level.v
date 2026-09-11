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
    integer     adc_rst_pulses = 0;     // negedge adc_resetn count (RESET verb)
    integer     rst_pulses_before;

    // ---- MW RF-switch gate pulse seen flag --------------------------------
    reg mw_gate_seen = 1'b0;
    always @(posedge clk_100mhz) if (mw_gate) mw_gate_seen <= 1'b1;

    // ---- adc_resetn negedge counter (RESET verb re-strokes the ADC reset) --
    // adc_resetn is held low at boot (no negedge there), then released. A host
    // RESET verb drives peri_rst -> adc_reader reset -> adc_resetn falls again,
    // so this counter ticks once per RESET.
    always @(negedge adc_resetn) adc_rst_pulses = adc_rst_pulses + 1;

    // ---- MW pulse-WIDTH timer: count cycles mw_gate is high, capture the
    // width of the last completed pulse. At 100 MHz the RTL gate timer ticks
    // every 10 ns (gate_cnt = duration_ns/10), so a 500 ns APPLY_PULSE must
    // produce a mw_gate pulse exactly 50 clk cycles wide. This is the timing
    // check that locks "the pulse lasts as long as qiskit asked for".
    integer mw_gate_high = 0;     // cycles high in the current pulse
    integer mw_gate_last = 0;     // width (cycles) of the last completed pulse
    integer mw_gate_pulses = 0;   // total completed mw_gate pulses (rising->falling)
    always @(posedge clk_100mhz) begin
        if (mw_gate)        mw_gate_high <= mw_gate_high + 1;
        else if (mw_gate_high != 0) begin
            mw_gate_last  <= mw_gate_high;
            mw_gate_pulses <= mw_gate_pulses + 1;
            mw_gate_high   <= 0;
        end
    end

    // PLL SPI write counter: counts falling edges of LE (spi_pins[0]). Each
    // ADF4351 transaction (full R5->R0 program OR an R1-only phase step) is one
    // LE low->high pulse, so a falling edge marks one register-write burst. Used
    // to verify the Ramsey R1-only phase write fires mid-sequence (and that
    // Hahn/CPMG do NOT write the PLL during RUN_SEQUENCE).
    integer pll_wr_count = 0;
    integer pll_le_last  = 1;     // LE idles high
    always @(posedge clk_100mhz) begin
        if (spi_pins[0] === 1'b0 && pll_le_last === 1)
            pll_wr_count <= pll_wr_count + 1;
        pll_le_last <= spi_pins[0];
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
    integer k16, ok16;       // RUN_SEQUENCE verify loop
    integer li_k, li_ok;     // READ_LOCKIN verify loop
    integer k18, ok18;       // Ramsey RUN_SEQUENCE verify loop
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

        // ---- 12) ID -> "QSSC v1\n" (8-byte probe token) ---------------------
        rx_log_cnt = 0;
        cmd_bytes[0]="I";  cmd_bytes[1]="D";  cmd_bytes[2]=8'h0A;
        $display("TB: sending ID (expect QSSC v1\\n)");
        for (c=0;c<3;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(8, W_GENERIC);
        if (rx_log_cnt >= 8 &&
            rx_log[0]==="Q" && rx_log[1]==="S" && rx_log[2]==="S" &&
            rx_log[3]==="C" && rx_log[4]===" " && rx_log[5]==="v" &&
            rx_log[6]==="1" && rx_log[7]===8'h0A) begin
            ok_count = ok_count + 1;  $display("TB: ID = \"QSSC v1\\n\"  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL ID reply (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end

        // ---- 13) STATUS -> 79-byte self-status line ------------------------
        // "PLL_LOCKED <1> ADC_BUSY <0> VCC3V3_mv 3300 VCC5V_mv 4980
        //  VCC12V_mv 12050 TEMP_C 25\n". PLL_LOCKED=!pll_busy=1 (idle),
        //  ADC_BUSY=det_busy=0 (idle). Check the frame, the two live digits
        //  at template indices 11 and 22, and the 79-byte length + LF.
        rx_log_cnt = 0;
        cmd_bytes[0]="S";  cmd_bytes[1]="T";  cmd_bytes[2]="A";  cmd_bytes[3]="T";
        cmd_bytes[4]="U";  cmd_bytes[5]="S";  cmd_bytes[6]=8'h0A;
        $display("TB: sending STATUS (expect 79-byte self-status line)");
        for (c=0;c<7;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(79, W_GENERIC);
        if (rx_log_cnt == 79 &&
            rx_log[0]==="P" && rx_log[1]==="L" && rx_log[2]==="L" &&
            rx_log[3]==="_" && rx_log[4]==="L" && rx_log[5]==="O" &&
            rx_log[6]==="C" && rx_log[7]==="K" && rx_log[8]==="E" &&
            rx_log[9]==="D" && rx_log[10]===" " && rx_log[11]==="1" &&
            rx_log[12]===" " && rx_log[13]==="A" && rx_log[14]==="D" &&
            rx_log[15]==="C" && rx_log[16]==="_" && rx_log[17]==="B" &&
            rx_log[18]==="U" && rx_log[19]==="S" && rx_log[20]==="Y" &&
            rx_log[21]===" " && rx_log[22]==="0" && rx_log[78]===8'h0A) begin
            ok_count = ok_count + 1;
            $display("TB: STATUS = 79-byte line, PLL_LOCKED=1 ADC_BUSY=0  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL STATUS (got %0d bytes, [11]=0x%02h [22]=0x%02h [78]=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>11?rx_log[11]:8'h00),
                     (rx_log_cnt>22?rx_log[22]:8'h00),
                     (rx_log_cnt>78?rx_log[78]:8'h00));
        end

        // ---- 14) RESET -> OK + adc_resetn re-strobed + emitter cleared ------
        // Load gs[0]=4095 first so the soft reset has something to clear, then
        // send RESET: it must reply OK, pulse adc_resetn low (re-init ADC), and
        // clear the TLC5947 grayscale register (emitter on peri_rst).
        rx_log_cnt = 0;
        cmd_bytes[0]="S";  cmd_bytes[1]="E";  cmd_bytes[2]="T";  cmd_bytes[3]="_";
        cmd_bytes[4]="E";  cmd_bytes[5]="M";  cmd_bytes[6]="I";  cmd_bytes[7]="T";
        cmd_bytes[8]="T";  cmd_bytes[9]="E";  cmd_bytes[10]="R"; cmd_bytes[11]=" ";
        cmd_bytes[12]="0"; cmd_bytes[13]=" "; cmd_bytes[14]="4"; cmd_bytes[15]="0";
        cmd_bytes[16]="9"; cmd_bytes[17]="5"; cmd_bytes[18]=8'h0A;
        $display("TB: sending SET_EMITTER 0 4095 (pre-RESET load)");
        for (c=0;c<19;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(3, W_GENERIC);               // OK\n
        repeat(W_EM_DONE) @(posedge clk_100mhz);     // let the TLC load complete
        rst_pulses_before = adc_rst_pulses;
        rx_log_cnt = 0;
        cmd_bytes[0]="R";  cmd_bytes[1]="E";  cmd_bytes[2]="S";  cmd_bytes[3]="E";
        cmd_bytes[4]="T";  cmd_bytes[5]=8'h0A;
        $display("TB: sending RESET (expect OK + adc_resetn pulse + gs[0]=0)");
        for (c=0;c<6;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(3, W_GENERIC);               // OK\n
        repeat(400) @(posedge clk_100mhz);           // past the ~200-cyc adc_resetn hold
        if (rx_log_cnt >= 3 &&
            rx_log[0]===8'h4F && rx_log[1]===8'h4B && rx_log[2]===8'h0A &&
            adc_rst_pulses > rst_pulses_before &&
            dut.em_inst.tlc_inst.gs[0] === 12'd0) begin
            ok_count = ok_count + 1;
            $display("TB: RESET = OK + adc_resetn re-strobed + gs[0]=0  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL RESET (reply0=0x%02h rst %0d->%0d gs0=%0d)",
                     (rx_log_cnt>0?rx_log[0]:8'h00), rst_pulses_before,
                     adc_rst_pulses, dut.em_inst.tlc_inst.gs[0]);
        end

        // ---- 15) ODMR_SWEEP 0 2870000 2880000 4 -> 4 lines + OK ------------
        // Each point: reprogram ADF4351, gate RF, read detector site 0, stream
        // "fffffff ccccc\n" (7-digit kHz + ' ' + 5-digit code + LF = 14 bytes).
        // code = ADC_VAL = 1234 -> "01234"; step=(2880000-2870000)/3=3333 ->
        // 2870000, 2873333, 2876666, 2879999. 4*14 + "OK\n" = 59 bytes.
        // Disable the QRNG entropy toggle so channel 0 reads a constant 1234.
        entropy_mode = 1'b0;
        rx_log_cnt = 0;
        cmd_bytes[0]="O";  cmd_bytes[1]="D";  cmd_bytes[2]="M";  cmd_bytes[3]="R";
        cmd_bytes[4]="_";  cmd_bytes[5]="S";  cmd_bytes[6]="W";  cmd_bytes[7]="E";
        cmd_bytes[8]="E";  cmd_bytes[9]="P";  cmd_bytes[10]=" "; cmd_bytes[11]="0";
        cmd_bytes[12]=" "; cmd_bytes[13]="2"; cmd_bytes[14]="8"; cmd_bytes[15]="7";
        cmd_bytes[16]="0"; cmd_bytes[17]="0"; cmd_bytes[18]="0"; cmd_bytes[19]="0";
        cmd_bytes[20]=" "; cmd_bytes[21]="2"; cmd_bytes[22]="8"; cmd_bytes[23]="8";
        cmd_bytes[24]="0"; cmd_bytes[25]="0"; cmd_bytes[26]="0"; cmd_bytes[27]="0";
        cmd_bytes[28]=" "; cmd_bytes[29]="4"; cmd_bytes[30]=8'h0A;
        $display("TB: sending ODMR_SWEEP 0 2870000 2880000 4 (expect 4 lines + OK)");
        for (c=0;c<31;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(59, W_SAFE);
        // Line layout: 0..13 "2870000 01234\n", 14..27 "2870333 01234\n",
        // 28..41 "2870666 01234\n", 42..55 "2870999 01234\n", 56..58 "OK\n".
        if (rx_log_cnt == 59 &&
            rx_log[0]==="2" && rx_log[1]==="8" && rx_log[2]==="7" &&
            rx_log[3]==="0" && rx_log[4]==="0" && rx_log[5]==="0" &&
            rx_log[6]==="0" && rx_log[7]===" " && rx_log[8]==="0" &&
            rx_log[9]==="1" && rx_log[10]==="2" && rx_log[11]==="3" &&
            rx_log[12]==="4" && rx_log[13]===8'h0A &&
            rx_log[14]==="2" && rx_log[15]==="8" && rx_log[16]==="7" &&
            rx_log[17]==="3" && rx_log[18]==="3" && rx_log[19]==="3" &&
            rx_log[20]==="3" && rx_log[21]===" " && rx_log[22]==="0" &&
            rx_log[23]==="1" && rx_log[24]==="2" && rx_log[25]==="3" &&
            rx_log[26]==="4" && rx_log[27]===8'h0A &&
            rx_log[28]==="2" && rx_log[29]==="8" && rx_log[30]==="7" &&
            rx_log[31]==="6" && rx_log[32]==="6" && rx_log[33]==="6" &&
            rx_log[34]==="6" && rx_log[35]===" " && rx_log[36]==="0" &&
            rx_log[37]==="1" && rx_log[38]==="2" && rx_log[39]==="3" &&
            rx_log[40]==="4" && rx_log[41]===8'h0A &&
            rx_log[42]==="2" && rx_log[43]==="8" && rx_log[44]==="7" &&
            rx_log[45]==="9" && rx_log[46]==="9" && rx_log[47]==="9" &&
            rx_log[48]==="9" && rx_log[49]===" " && rx_log[50]==="0" &&
            rx_log[51]==="1" && rx_log[52]==="2" && rx_log[53]==="3" &&
            rx_log[54]==="4" && rx_log[55]===8'h0A &&
            rx_log[56]===8'h4F && rx_log[57]===8'h4B && rx_log[58]===8'h0A) begin
            ok_count = ok_count + 1;
            $display("TB: ODMR_SWEEP = 4 lines (2870000..2879999) + OK  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL ODMR_SWEEP (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end

        // ---- 16) RUN_SEQUENCE 0 1 4 500 -> "00004 01234 01234 01234 01234\n"
        // Hahn (kind=1), 4 windows, t_step=500 ns. The behavioural ADC returns a
        // constant 1234, so every readout code is "01234". Reply = 5-digit count
        // prefix + ' ' + 4 codes (5-digit, ' '-sep, final '\n') = 30 bytes, NO
        // OK (the '\n' terminates the single-line reply). A Hahn window fires
        // pi/2 + pi + pi/2(readout) = 3 mw_gate pulses, so 4 windows => >= 4.
        entropy_mode = 1'b0;
        mw_gate_pulses = 0;
        pll_wr_count  = 0;
        rx_log_cnt = 0;
        cmd_bytes[0]="R";  cmd_bytes[1]="U";  cmd_bytes[2]="N";  cmd_bytes[3]="_";
        cmd_bytes[4]="S";  cmd_bytes[5]="E";  cmd_bytes[6]="Q";  cmd_bytes[7]="U";
        cmd_bytes[8]="E";  cmd_bytes[9]="N";  cmd_bytes[10]="C"; cmd_bytes[11]="E";
        cmd_bytes[12]=" "; cmd_bytes[13]="0"; cmd_bytes[14]=" "; cmd_bytes[15]="1";
        cmd_bytes[16]=" "; cmd_bytes[17]="4"; cmd_bytes[18]=" "; cmd_bytes[19]="5";
        cmd_bytes[20]="0"; cmd_bytes[21]="0"; cmd_bytes[22]=8'h0A;
        $display("TB: sending RUN_SEQUENCE 0 1 4 500 (expect 00004 + 4x 01234 + LF)");
        for (c=0;c<23;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(30, W_SAFE);
        // Verify: "00004 " + 4x("01234" + sep) where sep=' ' for the first 3,
        // '\n' for the last. Hahn (kind=1) must NOT write the PLL mid-sequence
        // (no R1-only phase step), so pll_wr_count stays 0.
        ok16 = 1;
        if (rx_log_cnt != 30) ok16 = 0;
        if (!(rx_log[0]==="0" && rx_log[1]==="0" && rx_log[2]==="0" &&
              rx_log[3]==="0" && rx_log[4]==="4" && rx_log[5]===" ")) ok16 = 0;
        for (k16=0;k16<4;k16=k16+1) begin
            if (!(rx_log[6+k16*6]==="0" && rx_log[7+k16*6]==="1" &&
                  rx_log[8+k16*6]==="2" && rx_log[9+k16*6]==="3" &&
                  rx_log[10+k16*6]==="4")) ok16 = 0;
            if (k16<3 && rx_log[11+k16*6] !== " ") ok16 = 0;
            if (k16==3 && rx_log[11+k16*6] !== 8'h0A) ok16 = 0;
        end
        if (ok16 && mw_gate_pulses >= 4 && pll_wr_count == 0) begin
            ok_count = ok_count + 1;
            $display("TB: RUN_SEQUENCE = 00004 + 4x01234 + LF, %0d mw_gate pulses  (PASS)",
                     mw_gate_pulses);
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL RUN_SEQUENCE (got %0d bytes, ok=%0d, pulses=%0d, pll_wr=%0d)",
                     rx_log_cnt, ok16, mw_gate_pulses, pll_wr_count);
        end

        // ---- 17) READ_LOCKIN 0 4 -> "0000004936 0000004936 0000000004 0000000004\n"
        // 4 on/off cycles; the constant ADC code 1234 makes sum_on = sum_off =
        // 4*1234 = 4936, n_on = n_off = 4. Reply = four 10-digit dec32 ints,
        // ' '-sep, final '\n' = 44 bytes, NO OK.
        entropy_mode = 1'b0;
        rx_log_cnt = 0;
        cmd_bytes[0]="R";  cmd_bytes[1]="E";  cmd_bytes[2]="A";  cmd_bytes[3]="D";
        cmd_bytes[4]="_";  cmd_bytes[5]="L";  cmd_bytes[6]="O";  cmd_bytes[7]="C";
        cmd_bytes[8]="K";  cmd_bytes[9]="I";  cmd_bytes[10]="N"; cmd_bytes[11]=" ";
        cmd_bytes[12]="0"; cmd_bytes[13]=" "; cmd_bytes[14]="4"; cmd_bytes[15]=8'h0A;
        $display("TB: sending READ_LOCKIN 0 4 (expect 4936 4936 4 4 + LF)");
        for (c=0;c<16;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(44, W_SAFE);
        // Verify: four 10-digit fields ("0000004936","0000004936","0000000004",
        // "0000000004") separated by ' ' and terminated by '\n'.
        li_ok = 1;
        if (rx_log_cnt != 44) li_ok = 0;
        for (li_k=0;li_k<4;li_k=li_k+1) begin
            // field li_k base = li_k*11; 10 digits + sep at base+10.
            // fields 0,1 = "0000004936" (6 zeros + 4936); 2,3 = "0000000004".
            if (li_k < 2) begin
                if (!(rx_log[li_k*11+0]==="0" && rx_log[li_k*11+1]==="0" &&
                      rx_log[li_k*11+2]==="0" && rx_log[li_k*11+3]==="0" &&
                      rx_log[li_k*11+4]==="0" && rx_log[li_k*11+5]==="0" &&
                      rx_log[li_k*11+6]==="4" && rx_log[li_k*11+7]==="9" &&
                      rx_log[li_k*11+8]==="3" && rx_log[li_k*11+9]==="6")) li_ok = 0;
            end else begin
                if (!(rx_log[li_k*11+0]==="0" && rx_log[li_k*11+1]==="0" &&
                      rx_log[li_k*11+2]==="0" && rx_log[li_k*11+3]==="0" &&
                      rx_log[li_k*11+4]==="0" && rx_log[li_k*11+5]==="0" &&
                      rx_log[li_k*11+6]==="0" && rx_log[li_k*11+7]==="0" &&
                      rx_log[li_k*11+8]==="0" && rx_log[li_k*11+9]==="4")) li_ok = 0;
            end
            if (li_k<3 && rx_log[li_k*11+10] !== " ") li_ok = 0;
            if (li_k==3 && rx_log[li_k*11+10] !== 8'h0A) li_ok = 0;
        end
        if (li_ok) begin
            ok_count = ok_count + 1;
            $display("TB: READ_LOCKIN = 4936 4936 4 4 + LF  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL READ_LOCKIN (got %0d bytes)", rx_log_cnt);
        end

        // ---- 17b) READ_LOCKINP 0 4 10000 -> same 4936 4936 4 4 (prime-period
        // lock-in with a non-default half_ns; constant ADC 1234 -> sums 4*1234).
        // Proves the host-supplied half-period drives the on/off timing.
        entropy_mode = 1'b0;
        rx_log_cnt = 0;
        cmd_bytes[0]="R";  cmd_bytes[1]="E";  cmd_bytes[2]="A";  cmd_bytes[3]="D";
        cmd_bytes[4]="_";  cmd_bytes[5]="L";  cmd_bytes[6]="O";  cmd_bytes[7]="C";
        cmd_bytes[8]="K";  cmd_bytes[9]="I";  cmd_bytes[10]="N"; cmd_bytes[11]="P";
        cmd_bytes[12]=" "; cmd_bytes[13]="0"; cmd_bytes[14]=" "; cmd_bytes[15]="4";
        cmd_bytes[16]=" "; cmd_bytes[17]="1"; cmd_bytes[18]="0"; cmd_bytes[19]="0";
        cmd_bytes[20]="0"; cmd_bytes[21]="0"; cmd_bytes[22]=8'h0A;
        $display("TB: sending READ_LOCKINP 0 4 10000 (expect 4936 4936 4 4 + LF)");
        for (c=0;c<23;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(44, W_SAFE);
        li_ok = 1;
        if (rx_log_cnt != 44) li_ok = 0;
        for (li_k=0;li_k<4;li_k=li_k+1) begin
            if (li_k < 2) begin
                if (!(rx_log[li_k*11+0]==="0" && rx_log[li_k*11+1]==="0" &&
                      rx_log[li_k*11+2]==="0" && rx_log[li_k*11+3]==="0" &&
                      rx_log[li_k*11+4]==="0" && rx_log[li_k*11+5]==="0" &&
                      rx_log[li_k*11+6]==="4" && rx_log[li_k*11+7]==="9" &&
                      rx_log[li_k*11+8]==="3" && rx_log[li_k*11+9]==="6")) li_ok = 0;
            end else begin
                if (!(rx_log[li_k*11+0]==="0" && rx_log[li_k*11+1]==="0" &&
                      rx_log[li_k*11+2]==="0" && rx_log[li_k*11+3]==="0" &&
                      rx_log[li_k*11+4]==="0" && rx_log[li_k*11+5]==="0" &&
                      rx_log[li_k*11+6]==="0" && rx_log[li_k*11+7]==="0" &&
                      rx_log[li_k*11+8]==="0" && rx_log[li_k*11+9]==="4")) li_ok = 0;
            end
            if (li_k<3 && rx_log[li_k*11+10] !== " ") li_ok = 0;
            if (li_k==3 && rx_log[li_k*11+10] !== 8'h0A) li_ok = 0;
        end
        if (li_ok) begin
            ok_count = ok_count + 1;
            $display("TB: READ_LOCKINP = 4936 4936 4 4 + LF  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL READ_LOCKINP (got %0d bytes)", rx_log_cnt);
        end

        // ---- 17c) READ_LOCKINP 0 4 1000 -> ERR (half_ns below the 2000 min)
        entropy_mode = 1'b0;
        rx_log_cnt = 0;
        cmd_bytes[0]="R";  cmd_bytes[1]="E";  cmd_bytes[2]="A";  cmd_bytes[3]="D";
        cmd_bytes[4]="_";  cmd_bytes[5]="L";  cmd_bytes[6]="O";  cmd_bytes[7]="C";
        cmd_bytes[8]="K";  cmd_bytes[9]="I";  cmd_bytes[10]="N"; cmd_bytes[11]="P";
        cmd_bytes[12]=" "; cmd_bytes[13]="0"; cmd_bytes[14]=" "; cmd_bytes[15]="4";
        cmd_bytes[16]=" "; cmd_bytes[17]="1"; cmd_bytes[18]="0"; cmd_bytes[19]="0";
        cmd_bytes[20]="0"; cmd_bytes[21]=8'h0A;
        $display("TB: sending READ_LOCKINP 0 4 1000 (expect ERR, half_ns < 2000)");
        for (c=0;c<22;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(4, W_SAFE);
        li_ok = (rx_log_cnt==4 && rx_log[0]==="E" && rx_log[1]==="R" &&
                 rx_log[2]==="R" && rx_log[3]===8'h0A);
        if (li_ok) begin
            ok_count = ok_count + 1;
            $display("TB: READ_LOCKINP 1000 -> ERR (bounds)  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL READ_LOCKINP bounds (got %0d bytes)", rx_log_cnt);
        end

        // ---- 18) RUN_SEQUENCE 0 0 4 500 -> "00004 01234 01234 01234 01234\n"
        // Ramsey (kind=0), 4 windows, t_step=500 ns. Same reply shape as Hahn
        // (constant ADC -> 01234 every window), but the readout pi/2 of each
        // Ramsey window first applies a 90-deg R1-only PLL phase write (no
        // re-lock) before gating+reading. So pll_wr_count must be >= 4 (one
        // R1-only write per window) and mw_gate_pulses >= 4 (pi/2 + readout
        // pi/2 per window). This is the PR4b Ramsey phase-applied check.
        entropy_mode = 1'b0;
        mw_gate_pulses = 0;
        pll_wr_count  = 0;
        rx_log_cnt = 0;
        cmd_bytes[0]="R";  cmd_bytes[1]="U";  cmd_bytes[2]="N";  cmd_bytes[3]="_";
        cmd_bytes[4]="S";  cmd_bytes[5]="E";  cmd_bytes[6]="Q";  cmd_bytes[7]="U";
        cmd_bytes[8]="E";  cmd_bytes[9]="N";  cmd_bytes[10]="C"; cmd_bytes[11]="E";
        cmd_bytes[12]=" "; cmd_bytes[13]="0"; cmd_bytes[14]=" "; cmd_bytes[15]="0";
        cmd_bytes[16]=" "; cmd_bytes[17]="4"; cmd_bytes[18]=" "; cmd_bytes[19]="5";
        cmd_bytes[20]="0"; cmd_bytes[21]="0"; cmd_bytes[22]=8'h0A;
        $display("TB: sending RUN_SEQUENCE 0 0 4 500 (Ramsey: expect 00004 + 4x 01234 + >=4 PLL writes)");
        for (c=0;c<23;c=c+1) uart_send_byte(cmd_bytes[c]);
        wait_for_bytes(30, W_SAFE);
        ok18 = 1;
        if (rx_log_cnt != 30) ok18 = 0;
        if (!(rx_log[0]==="0" && rx_log[1]==="0" && rx_log[2]==="0" &&
              rx_log[3]==="0" && rx_log[4]==="4" && rx_log[5]===" ")) ok18 = 0;
        for (k18=0;k18<4;k18=k18+1) begin
            if (!(rx_log[6+k18*6]==="0" && rx_log[7+k18*6]==="1" &&
                  rx_log[8+k18*6]==="2" && rx_log[9+k18*6]==="3" &&
                  rx_log[10+k18*6]==="4")) ok18 = 0;
            if (k18<3 && rx_log[11+k18*6] !== " ") ok18 = 0;
            if (k18==3 && rx_log[11+k18*6] !== 8'h0A) ok18 = 0;
        end
        if (ok18 && mw_gate_pulses >= 4 && pll_wr_count >= 4) begin
            ok_count = ok_count + 1;
            $display("TB: Ramsey RUN_SEQUENCE = 00004 + 4x01234, %0d gates, %0d PLL writes  (PASS)",
                     mw_gate_pulses, pll_wr_count);
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL Ramsey RUN_SEQUENCE (got %0d bytes, ok=%0d, pulses=%0d, pll_wr=%0d)",
                     rx_log_cnt, ok18, mw_gate_pulses, pll_wr_count);
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