`timescale 1ns / 1ps
//============================================================================
// i2c_master.v
// ----------------------------------------------------------------------------
// Minimal single-master I2C writer for the on-board MCP4725 comparator-
// threshold DAC (device address 0x60).
//
// A `start` pulse with `data[11:0]` writes the 12-bit DAC code in fast mode:
//   START, [addr+W = 0xC0] + ACK, [data[11:4]] + ACK, [{data[3:0],4'b0}] + ACK, STOP
// SDA/SCL are open-drain: the master drives them low or releases them (high
// via the board's 4.7k pull-ups). `busy` is high for the whole transaction,
// `done` pulses for one cycle at the end. SCL ~= 100 kHz (100 MHz / 2*CLK_DIV).
//
// Bit plan (one SCL half-period per phase; 0=SCL low/setup, 1=SCL high/hold):
//   phase 0      START  (SDA falls while SCL high, set up in S_IDLE)
//   phases 1..8  byte 0 = {DEV_ADDR, W=0}  (8 data bits, MSB first)
//   phase 9      ACK 0  (release SDA, sample -- not enforced)
//   phases 10..17 byte 1 = data[11:4]       (8 data bits)
//   phase 18     ACK 1
//   phases 19..26 byte 2 = {data[3:0],4'b0} (8 data bits)
//   phase 27     ACK 2
//   phase 28     STOP   (SDA low while SCL low -> release SDA while SCL high)
//
// Data bits are shifted out of a 24-bit shift register (shreg[23] = MSB),
// one shift per *data* phase (ACK/START/STOP phases do not shift), so the
// MSB-first ordering is independent of the ACK phase positions.
//
// QSSC is the only master and the DAC the only target; written rarely (host
// SET_THRESHOLD), so arbitration / clock-stretching are not handled. The ACK
// bits are released and sampled but not enforced (the DAC always ACKs in
// practice; a stuck bus would be caught by a board-level probe, not here).
//============================================================================
module i2c_master #(
    parameter CLK_DIV = 500,                       // 100 MHz/(2*500) = 100 kHz
    parameter [6:0] DEV_ADDR = 7'b1100000          // MCP4725 = 0x60
) (
    input             clk,
    input             reset,
    input             start,
    input      [11:0] data,        // 12-bit DAC code
    output reg        busy,
    output reg        done,
    inout             sda,
    inout             scl
);
    localparam [4:0] PH_STOP = 5'd28;

    reg [4:0]  phase;
    reg [9:0]  div;          // SCL half-period divider
    reg [2:0]  state;
    reg [23:0] shreg;        // 24-bit payload shift register (MSB-first)
    reg        sda_drv;      // 1 = drive SDA low, 0 = release
    reg        scl_drv;      // 1 = drive SCL low, 0 = release (high)
    reg        half;         // 0 = SCL-low half (set up data), 1 = SCL-high half

    localparam [2:0] S_IDLE = 3'd0;
    localparam [2:0] S_RUN  = 3'd1;
    localparam [2:0] S_DONE = 3'd2;

    // phase is a data bit phase (vs START/ACK/STOP) -> shift on its high half
    wire is_data_ph =
        (phase >= 5'd1  && phase <= 5'd8)  ||
        (phase >= 5'd10 && phase <= 5'd17) ||
        (phase >= 5'd19 && phase <= 5'd26);

    initial begin
        phase   = 5'd0;
        div     = 10'd0;
        state   = S_IDLE;
        shreg   = 24'd0;
        busy    = 1'b0;
        done    = 1'b0;
        sda_drv <= 1'b0;
        scl_drv <= 1'b0;
        half    = 1'b0;
    end

    // open-drain bus: drive low or release (external pull-up gives high)
    assign sda = sda_drv ? 1'b0 : 1'bz;
    assign scl = scl_drv ? 1'b0 : 1'bz;

    always @(posedge clk) begin
        if (reset) begin
            state   <= S_IDLE;
            phase   <= 5'd0;
            div     <= 10'd0;
            busy    <= 1'b0;
            done    <= 1'b0;
            sda_drv <= 1'b0;
            scl_drv <= 1'b0;
            half    <= 1'b0;
        end else begin
            done <= 1'b0;
            case (state)
                S_IDLE: begin
                    sda_drv <= 1'b0;     // idle: SDA released (high)
                    scl_drv <= 1'b0;     // idle: SCL released (high)
                    if (start) begin
                        // {addr(7), W(1)=0, dhi(8)=data[11:4], dlo(8)={data[3:0],4'b0}}
                        shreg <= {DEV_ADDR, 1'b0, data[11:4], {data[3:0], 4'b0000}};
                        phase <= 5'd0;
                        div   <= 10'd0;
                        half  <= 1'b0;
                        busy  <= 1'b1;
                        state <= S_RUN;
                        // START condition: SDA falls while SCL stays high.
                        scl_drv <= 1'b0;  // SCL high (released)
                        sda_drv <= 1'b1;  // SDA low (falling edge)
                    end
                end

                S_RUN: begin
                    if (div < CLK_DIV) begin
                        div <= div + 10'd1;
                    end else begin
                        div <= 10'd0;
                        if (half == 1'b0) begin
                            // ---- SCL low half: set up SDA for this phase ----
                            half    <= 1'b1;
                            scl_drv <= 1'b1;              // pull SCL low
                            if (phase >= PH_STOP)
                                sda_drv <= 1'b1;            // STOP setup: SDA low
                            else if (phase == 5'd0)
                                sda_drv <= 1'b1;            // START hold: SDA low
                            else if (phase == 5'd9 || phase == 5'd18 ||
                                     phase == 5'd27)
                                sda_drv <= 1'b0;            // ACK: release SDA
                            else
                                sda_drv <= ~shreg[23];       // data bit (MSB first)
                        end else begin
                            // ---- SCL high half: release SCL, hold/sample ----
                            half    <= 1'b0;
                            scl_drv <= 1'b0;               // release SCL (high)
                            if (phase >= PH_STOP) begin
                                // STOP: SCL high, release SDA -> low-to-high.
                                sda_drv <= 1'b0;
                                state   <= S_DONE;
                            end else begin
                                if (is_data_ph)
                                    shreg <= shreg << 1;    // shift after data bit
                                phase <= phase + 5'd1;       // advance to next phase
                            end
                        end
                    end
                end

                S_DONE: begin
                    sda_drv <= 1'b0;     // SDA released (STOP complete)
                    scl_drv <= 1'b0;     // SCL released
                    busy   <= 1'b0;
                    done   <= 1'b1;
                    state  <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end
endmodule