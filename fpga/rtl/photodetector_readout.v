`timescale 1ns / 1ps
//============================================================================
// photodetector_readout.v
// ----------------------------------------------------------------------------
// On-chip graphene/MoS2 photodetector array readout controller.
//
// The chip has N_DET graphene/MoS2 photodetector electrodes (one per qubit
// site), each producing a small photocurrent (~nA-uA). An external TIA
// (transimpedance amplifier) converts each electrode's current to a voltage,
// an analog MUX selects one electrode, and the Artix-7's built-in **XADC**
// (12-bit, up to 1 MSPS) digitises it. This module controls the MUX select and
// the XADC conversion handshake, and provides two modes:
//
//   read_one  -- digitise one electrode (`site`), present code + code_valid
//   read_all  -- sweep all N_DET electrodes, streaming code/code_valid/code_idx
//
// The microwave drive is modulated at 1 kHz (LOCKIN_MOD_HZ) so the host can
// lock-in demodulate the photocurrent and reject background light; `mod_sync`
// is a 1 kHz square wave the host / MW chain uses as the modulation reference.
// NOTE: this module only *generates* the 1 kHz reference; the actual lock-in
// demodulation (multiply the photocurrent stream by mod_sync and low-pass) is
// done on the host side from the streamed ADC codes + this reference, not in
// the FPGA. The mod_sync square wave is a timing reference, not a synchronous
// gate on the XADC samples.
//
// The XADC primitive itself is instantiated in top_level (XADC wizard); this
// module drives the start/busy/data handshake so the analog front-end is
// kept in one place.
//============================================================================
module photodetector_readout #(
    parameter N_DET = 64,                 // electrodes (8x8 grid)
    parameter CLK_HZ = 100_000_000,
    // MUX + TIA settle time in clock cycles before an XADC conversion starts.
    // The analog MUX select changes in S_NEXT/S_IDLE; if the XADC starts the
    // very next cycle the TIA output has not settled and the first (and after
    // every index advance) code is wrong. ~50 cycles = 500 ns at 100 MHz, a
    // conservative settle for a CMOS analog MUX + TIA front-end.
    parameter MUX_SETTLE = 50
) (
    input             clk,
    input             reset,

    // host command interface (from command_parser)
    input             read_one,       // 1-cycle pulse: read electrode `site`
    input             read_all,       // 1-cycle pulse: sweep all electrodes
    input      [5:0]  site,           // electrode index for read_one (0..N_DET-1)
    output reg        busy,           // high while a read/sweep is in progress

    // code stream out (one code at a time)
    output reg [11:0] code,           // 12-bit XADC code
    output reg        code_valid,     // held high until code_ack: `code` is valid
    output reg [5:0]  code_idx,        // which electrode this code belongs to
    output reg        sweep_done,     // 1-cycle pulse: read_all sweep complete
    // consumer handshake: command_parser latched `code`, advance to the next
    input             code_ack,

    // external analog front-end
    output reg [5:0]  mux_sel,        // analog MUX select (which electrode)
    output reg        xadc_start,     // 1-cycle pulse: begin an XADC conversion
    input             xadc_busy,      // high while XADC converting
    input      [11:0] xadc_data,      // XADC conversion result

    // lock-in modulation reference (1 kHz square wave)
    output reg        mod_sync
);
    localparam [5:0]  N6 = N_DET - 1;            // 63 for an 8x8 grid
    // 1 kHz toggle period at CLK_HZ: toggle every CLK_HZ/2000 cycles
    localparam [31:0] MOD_HALF = (CLK_HZ / 2000);

    // FSM
    localparam [2:0] S_IDLE     = 3'd0;
    localparam [2:0] S_SETTLE   = 3'd1;   // let MUX + TIA settle after mux_sel change
    localparam [2:0] S_START    = 3'd2;   // assert xadc_start
    localparam [2:0] S_WAIT     = 3'd3;   // wait for XADC busy to rise then fall
    localparam [2:0] S_CAPTURE  = 3'd4;   // capture xadc_data, raise code_valid
    localparam [2:0] S_WAIT_ACK = 3'd5;   // hold code_valid until consumer acks
    localparam [2:0] S_NEXT     = 3'd6;   // advance sweep index

    reg [2:0]  state;
    reg [5:0]  idx;            // sweep index (read_all)
    reg        sweep;           // 1 = read_all sweep, 0 = read_one
    reg        busy_seen;       // XADC busy asserted this conversion (race fix)
    reg [31:0] mod_cnt;
    reg [9:0]  settle_cnt;      // MUX/TIA settle countdown (MUX_SETTLE cycles)

    initial begin
        state      = S_IDLE;
        idx        = 6'd0;
        sweep      = 1'b0;
        busy_seen  = 1'b0;
        busy       = 1'b0;
        code       = 12'd0;
        code_valid = 1'b0;
        code_idx   = 6'd0;
        sweep_done = 1'b0;
        mux_sel    = 6'd0;
        xadc_start = 1'b0;
        mod_sync   = 1'b0;
        mod_cnt    = 32'd0;
        settle_cnt = 10'd0;
    end

    // 1 kHz lock-in modulation reference
    always @(posedge clk) begin
        if (reset) begin
            mod_sync <= 1'b0;
            mod_cnt  <= 32'd0;
        end else begin
            if (mod_cnt >= MOD_HALF) begin
                mod_sync <= ~mod_sync;
                mod_cnt  <= 32'd0;
            end else begin
                mod_cnt <= mod_cnt + 32'd1;
            end
        end
    end

    always @(posedge clk) begin
        if (reset) begin
            state      <= S_IDLE;
            idx        <= 6'd0;
            sweep      <= 1'b0;
            busy_seen  <= 1'b0;
            busy       <= 1'b0;
            code_valid <= 1'b0;
            sweep_done <= 1'b0;
            xadc_start <= 1'b0;
        end else begin
            // default one-cycle pulses
            sweep_done <= 1'b0;
            xadc_start <= 1'b0;
            // code_valid is HELD (cleared only on ack in S_WAIT_ACK) so the
            // slow UART consumer can take many cycles to drain each code.

            case (state)
                S_IDLE: begin
                    if (read_one) begin
                        sweep      <= 1'b0;
                        idx        <= site;
                        mux_sel    <= site;
                        busy       <= 1'b1;
                        settle_cnt <= MUX_SETTLE;
                        state      <= S_SETTLE;
                    end else if (read_all) begin
                        sweep      <= 1'b1;
                        idx        <= 6'd0;
                        mux_sel    <= 6'd0;
                        busy       <= 1'b1;
                        settle_cnt <= MUX_SETTLE;
                        state      <= S_SETTLE;
                    end
                end

                S_SETTLE: begin
                    // MUX select changed this cycle (or last); let the analog
                    // MUX + TIA output settle before starting the XADC, otherwise
                    // the conversion samples a still-moving voltage.
                    if (settle_cnt >= MUX_SETTLE) begin
                        state <= S_START;
                    end else begin
                        settle_cnt <= settle_cnt + 10'd1;
                    end
                end

                S_START: begin
                    xadc_start <= 1'b1;          // 1-cycle pulse to the XADC
                    busy_seen  <= 1'b0;
                    state      <= S_WAIT;
                end

                S_WAIT: begin
                    // Race fix: the XADC asserts `busy` one cycle after `start`.
                    // Wait until busy has actually risen, THEN until it falls,
                    // before capturing -- otherwise we read data=0.
                    if (xadc_busy)        busy_seen <= 1'b1;
                    if (busy_seen && !xadc_busy) state <= S_CAPTURE;
                end

                S_CAPTURE: begin
                    code       <= xadc_data;
                    code_idx   <= idx;
                    code_valid <= 1'b1;         // held until code_ack
                    state      <= S_WAIT_ACK;
                end

                S_WAIT_ACK: begin
                    // hold the code stable until the consumer latches it
                    if (code_ack) begin
                        code_valid <= 1'b0;
                        if (sweep)  state <= S_NEXT;
                        else begin
                            busy  <= 1'b0;      // read_one done
                            state <= S_IDLE;
                        end
                    end
                end

                S_NEXT: begin
                    if (idx >= N6) begin
                        sweep_done <= 1'b1;
                        busy       <= 1'b0;
                        state      <= S_IDLE;
                    end else begin
                        idx        <= idx + 6'd1;
                        mux_sel    <= idx + 6'd1;
                        settle_cnt <= 10'd0;
                        state      <= S_SETTLE;   // settle before next conversion
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end
endmodule