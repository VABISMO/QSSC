`timescale 1ns / 1ps
//============================================================================
// adc_reader.v
// ----------------------------------------------------------------------------
// 64-channel parallel ANALOG intensity readout -- replaces photon_counter_array.
//
// The QSSC readout pivot (2026-09): the user wants the photoluminescence
// SIGNAL, not a photon count ("yo krio la señal no un contador"). The 16x
// LMV339 comparator + 64x pull-up + MCP4725 threshold DAC + 64 fabric photon
// counters are gone; instead the 64 TLV9004 TIA outputs feed 4x AD7616
// 16-bit dual simultaneous-sampling ADCs, and this module digitises all 64
// channels over a 9-wire SPI bus and serves 16-bit intensity samples to the
// host. The 1 kHz lock-in demodulation stays host-side (mod_sync is the
// reference), exactly as before -- only the per-channel quantity changes
// from a 16-bit photon count to a 16-bit ADC sample of the PL intensity.
//
// AD7616 configuration (hardwired on the board, see gen_qssc_kicad.py):
//   * serial/SPI mode (SER/PAR=HIGH), 1-wire (SER1W=LOW) -> all data on SDOA
//   * sequencer enabled (SEQEN=HIGH) -> 8 CONVST pulses auto-scan all 16
//     channels per chip (each CONVST returns one VA/VB pair = 2 channels)
//   * +/-5 V bipolar range via HW pin-strapping (HW_RNGSEL1=1, HW_RNGSEL0=0)
//     -> hardware mode, no register writes, SDI/MOSI unused
//   * internal reference (REFSEL=LOW), REFCAP/REFINOUT/REGCAP/REGCAPD decoupled
//
// Readout protocol (per chip, 1-wire):
//   CONVST rising -> BUSY high -> conversion -> BUSY falling; then CS low,
//   32 SCLK rising edges shift out VAx(16) then VBx(16), MSB first; CS high.
//   The 4 chips share CONVST/SCLK/MISO/RESETn and finish a simultaneous CONVST
//   together (only chip0 BUSY is read); each chip has its own CS, so the FPGA
//   reads the 4 chips one after another after each CONVST (MISO is shared --
//   only the CS-selected chip drives SDOA).
//
// A free-running background FSM continuously refreshes a 64x16 sample RAM
// (8 CONVST per full scan). The host read FSM (read_one/read_all) snapshots
// the latest RAM contents -- identical handshake to photon_counter_array
// (code/code_valid/code_idx/sweep_done/code_ack), so command_parser.v is
// unchanged for READ_DETECTOR / READ_DETECTORS / CALIBRATE. `clear` is a
// no-op (the ADC has no counters to zero); CLEAR_DETECTORS just replies OK.
//============================================================================
module adc_reader #(
    parameter N_DET = 64,
    parameter CLK_HZ = 100_000_000
) (
    input             clk,
    input             reset,

    // host command interface (IDENTICAL to photon_counter_array ->
    // command_parser.v read/CALIBRATE logic is reused unchanged)
    input             read_one,       // 1-cycle pulse: snapshot sample[site]
    input             read_all,       // 1-cycle pulse: stream all 64 samples
    input      [5:0]  site,           // channel index for read_one (0..N_DET-1)
    input             clear,          // ignored (ADC has no counters to clear)
    output reg        busy,           // high while a read/sweep is in progress
    output reg [15:0] code,           // 16-bit ADC sample snapshot
    output reg        code_valid,     // held until code_ack: `code` is valid
    output reg [5:0]  code_idx,       // which channel this sample belongs to
    output reg        sweep_done,     // 1-cycle pulse: read_all sweep complete
    input             code_ack,       // consumer latched the sample; advance

    // 4x AD7616 parallel ADC SPI bus (9 wires, top_level pins)
    output reg        adc_convst,     // shared conversion start (rising edge)
    output reg        adc_sclk,       // shared SPI clock (<=40 MHz, idles HIGH)
    input             adc_miso,       // shared SDOA (per-chip CS selects driver)
    input             adc_busy,       // chip0 BUSY (push-pull; all 4 finish together)
    output reg [3:0]  adc_cs,         // per-chip chip select (active low)
    output reg        adc_resetn,     // shared reset (active low)

    // lock-in modulation reference (1 kHz square wave)
    output reg        mod_sync
);
    localparam [5:0] N6 = N_DET - 1;          // 63

    // ---- 1 kHz mod_sync (same as photon_counter_array) --------------------
    localparam [31:0] MOD_HALF = (CLK_HZ / 2000);
    reg [31:0] mod_cnt;

    // ---- 64x16 sample RAM (refreshed by the background scan FSM) ----------
    reg [15:0] sample_ram [0:63];

    // ---- background scan FSM: 8 CONVST pulses, each reads 4 chips x32 bits -
    // SCLK = clk/4 = 25 MHz (< 40 MHz max). SCLK idles HIGH (AD7616 CPOL=1);
    // data is shifted out by the ADC on SCLK falling, sampled here on rising.
    localparam [3:0] SM_IDLE       = 4'd0;   // idle / between scans
    localparam [3:0] SM_RESET      = 4'd1;   // hold adc_resetn low at boot
    localparam [3:0] SM_CONVST     = 4'd2;   // pulse CONVST high
    localparam [3:0] SM_WAIT_BUSY  = 4'd3;   // wait BUSY high then low
    localparam [3:0] SM_CS_LOW     = 4'd4;   // assert CS[chip], prep read
    localparam [3:0] SM_BIT        = 4'd5;   // shift 32 bits (SCLK toggle)
    localparam [3:0] SM_STORE      = 4'd6;   // store VA/VB, next chip/pair
    localparam [3:0] SM_GAP        = 4'd7;   // brief CS-high gap between chips

    reg [3:0]  sm_state;
    reg [2:0]  pair;            // sequencer pair 0..7 (VA pair index)
    reg [1:0]  chip;            // chip 0..3
    reg [5:0]  bit_cnt;         // 0..32 bits read
    reg [31:0] shift;           // MSB-first shift register (VA[31:16], VB[15:0])
    reg [2:0]  sclk_div;        // SCLK half-period divider
    reg        sclk_phase;      // 0 = about-to-fall, 1 = about-to-rise
    reg [7:0]  reset_cnt;       // reset hold timer at boot
    reg [7:0]  wait_cnt;        // generic cycle counter (CONVST width, CS gap)
    reg        busy_seen;       // saw BUSY high during SM_WAIT_BUSY

    // ---- host read FSM (same shape as photon_counter_array) ---------------
    localparam [1:0] S_IDLE     = 2'd0;
    localparam [1:0] S_CAPTURE  = 2'd1;
    localparam [1:0] S_WAIT_ACK = 2'd2;
    localparam [1:0] S_NEXT     = 2'd3;
    reg [1:0]  rstate;
    reg [5:0]  idx;
    reg        sweep;

    integer p;
    initial begin
        // mod_sync
        mod_sync   <= 1'b0;
        mod_cnt    <= 32'd0;
        // ADC bus
        adc_convst <= 1'b0;
        adc_sclk   <= 1'b1;       // idle HIGH
        adc_cs     <= 4'b1111;    // all deselected (active low)
        adc_resetn <= 1'b0;       // hold in reset at boot
        // scan FSM
        sm_state   <= SM_RESET;
        pair       <= 3'd0;
        chip       <= 2'd0;
        bit_cnt    <= 6'd0;
        shift      <= 32'd0;
        sclk_div   <= 3'd0;
        sclk_phase <= 1'b0;
        reset_cnt  <= 8'd0;
        wait_cnt   <= 8'd0;
        busy_seen  <= 1'b0;
        // read FSM
        rstate     <= S_IDLE;
        idx        <= 6'd0;
        sweep      <= 1'b0;
        busy       <= 1'b0;
        code       <= 16'd0;
        code_valid <= 1'b0;
        code_idx   <= 6'd0;
        sweep_done <= 1'b0;
        for (p = 0; p < 64; p = p + 1) sample_ram[p] <= 16'd0;
    end

    // ---- 1 kHz lock-in modulation reference --------------------------------
    always @(posedge clk) begin
        if (reset) begin
            mod_sync <= 1'b0;
            mod_cnt  <= 32'd0;
        end else if (mod_cnt >= MOD_HALF) begin
            mod_sync <= ~mod_sync;
            mod_cnt  <= 32'd0;
        end else begin
            mod_cnt <= mod_cnt + 32'd1;
        end
    end

    // ---- background ADC scan FSM -------------------------------------------
    // Continuously refreshes sample_ram: 8 CONVST pairs x 4 chips x 32 bits.
    // Channel mapping (matches gen_qssc_kicad.py wiring):
    //   pair p, chip c -> VA = sample_ram[16*c + p]      (bank A channel p)
    //                  -> VB = sample_ram[16*c + 8 + p]  (bank B channel p)
    always @(posedge clk) begin
        if (reset) begin
            sm_state   <= SM_RESET;
            adc_convst <= 1'b0;
            adc_sclk   <= 1'b1;
            adc_cs     <= 4'b1111;
            adc_resetn <= 1'b0;
            reset_cnt  <= 8'd0;
            wait_cnt   <= 8'd0;
            sclk_div   <= 3'd0;
            sclk_phase <= 1'b0;
            bit_cnt    <= 6'd0;
            busy_seen  <= 1'b0;
            pair       <= 3'd0;
            chip       <= 2'd0;
        end else begin
            case (sm_state)
                // hold adc_resetn low for ~256 cycles at boot, then release
                SM_RESET: begin
                    adc_resetn <= 1'b0;
                    if (reset_cnt >= 8'd200) begin
                        adc_resetn <= 1'b1;
                        sm_state   <= SM_CONVST;
                        wait_cnt   <= 8'd0;
                        pair       <= 3'd0;
                    end else begin
                        reset_cnt <= reset_cnt + 8'd1;
                    end
                end

                // pulse CONVST high for a few cycles (rising edge starts conv)
                SM_CONVST: begin
                    adc_convst <= 1'b1;
                    if (wait_cnt >= 8'd4) begin
                        adc_convst <= 1'b0;
                        busy_seen  <= 1'b0;
                        sm_state   <= SM_WAIT_BUSY;
                        wait_cnt   <= 8'd0;
                    end else begin
                        wait_cnt <= wait_cnt + 8'd1;
                    end
                end

                // wait for BUSY high (conversion started) then low (done)
                SM_WAIT_BUSY: begin
                    if (adc_busy) busy_seen <= 1'b1;
                    if (busy_seen && !adc_busy) begin
                        sm_state <= SM_CS_LOW;
                        chip     <= 2'd0;
                        wait_cnt <= 8'd0;
                    end else begin
                        // timeout guard: if BUSY never asserts, proceed anyway
                        // (keeps the scan alive if chip0 BUSY is miswired)
                        if (wait_cnt >= 8'd250) begin
                            sm_state <= SM_CS_LOW;
                            chip     <= 2'd0;
                            wait_cnt <= 8'd0;
                        end else begin
                            wait_cnt <= wait_cnt + 8'd1;
                        end
                    end
                end

                // assert CS[chip] low, prepare to clock 32 bits (SCLK idles high)
                SM_CS_LOW: begin
                    adc_cs     <= ~(4'b0001 << chip);   // only chip `chip` low
                    adc_sclk   <= 1'b1;                  // idle high
                    sclk_div   <= 3'd0;
                    sclk_phase <= 1'b0;
                    bit_cnt    <= 6'd0;
                    shift      <= 32'd0;
                    sm_state   <= SM_BIT;
                end

                // shift 32 bits: SCLK falls (ADC drives next bit) then rises
                // (sample MISO into shift reg, MSB first). 32 rising edges.
                SM_BIT: begin
                    if (sclk_div >= 3'd3) begin      // 4 clk cycles per half -> 25 MHz
                        sclk_div <= 3'd0;
                        if (sclk_phase == 1'b0) begin
                            // falling edge: ADC shifts the next bit out
                            adc_sclk   <= 1'b0;
                            sclk_phase <= 1'b1;
                        end else begin
                            // rising edge: sample MISO, advance bit count
                            adc_sclk   <= 1'b1;
                            shift      <= {shift[30:0], adc_miso};
                            bit_cnt    <= bit_cnt + 6'd1;
                            sclk_phase <= 1'b0;
                            if (bit_cnt == 6'd31) begin
                                sm_state <= SM_STORE;
                            end
                        end
                    end else begin
                        sclk_div <= sclk_div + 3'd1;
                    end
                end

                // store VA[31:16] and VB[15:0] for this chip+pair, deselect CS
                SM_STORE: begin
                    adc_cs <= 4'b1111;
                    sample_ram[16*chip + pair]     <= shift[31:16];  // VA (bank A)
                    sample_ram[16*chip + 8 + pair] <= shift[15:0];   // VB (bank B)
                    sm_state <= SM_GAP;
                    wait_cnt <= 8'd0;
                end

                // brief CS-high gap, then next chip or next pair
                SM_GAP: begin
                    if (wait_cnt >= 8'd4) begin
                        wait_cnt <= 8'd0;
                        if (chip == 2'd3) begin
                            chip <= 2'd0;
                            if (pair == 3'd7) begin
                                pair     <= 3'd0;     // full scan done -> restart
                                sm_state <= SM_CONVST;
                            end else begin
                                pair     <= pair + 3'd1;
                                sm_state <= SM_CONVST;
                            end
                        end else begin
                            chip     <= chip + 2'd1;
                            sm_state <= SM_CS_LOW;
                        end
                    end else begin
                        wait_cnt <= wait_cnt + 8'd1;
                    end
                end

                default: sm_state <= SM_RESET;
            endcase
        end
    end

    // ---- host read FSM: snapshot sample_ram on read_one / read_all --------
    // Identical handshake to photon_counter_array (code_valid held until ack),
    // so command_parser.v reads ADC samples exactly as it read photon counts.
    always @(posedge clk) begin
        if (reset) begin
            rstate     <= S_IDLE;
            idx        <= 6'd0;
            sweep      <= 1'b0;
            busy       <= 1'b0;
            code_valid <= 1'b0;
            sweep_done <= 1'b0;
        end else begin
            sweep_done <= 1'b0;            // default 1-cycle pulse
            case (rstate)
                S_IDLE: begin
                    if (read_one) begin
                        sweep <= 1'b0;
                        idx   <= site;
                        busy  <= 1'b1;
                        rstate <= S_CAPTURE;
                    end else if (read_all) begin
                        sweep <= 1'b1;
                        idx   <= 6'd0;
                        busy  <= 1'b1;
                        rstate <= S_CAPTURE;
                    end
                end

                S_CAPTURE: begin
                    code       <= sample_ram[idx];   // snapshot the latest sample
                    code_idx   <= idx;
                    code_valid <= 1'b1;
                    rstate     <= S_WAIT_ACK;
                end

                S_WAIT_ACK: begin
                    if (code_ack) begin
                        code_valid <= 1'b0;
                        if (sweep) rstate <= S_NEXT;
                        else begin
                            busy   <= 1'b0;   // read_one done
                            rstate <= S_IDLE;
                        end
                    end
                end

                S_NEXT: begin
                    if (idx >= N6) begin
                        sweep_done <= 1'b1;
                        busy       <= 1'b0;
                        rstate     <= S_IDLE;
                    end else begin
                        idx    <= idx + 6'd1;
                        rstate <= S_CAPTURE;  // snapshot next channel
                    end
                end

                default: rstate <= S_IDLE;
            endcase
        end
    end
endmodule