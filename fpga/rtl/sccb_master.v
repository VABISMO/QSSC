`timescale 1ns / 1ps
//============================================================================
// sccb_master.v
// ----------------------------------------------------------------------------
// Minimal SCCB (I2C-like) write master used to configure the OV7670 camera.
// Performs a single write transaction: START, address-W, subaddr, data, STOP.
// The read path is implemented but unused by the camera configurator.
//
// CLK_DIV is the SCL *half-period* in system clocks, so SCL frequency is
//   fSCL = fclk / (2 * CLK_DIV)        (100 MHz, CLK_DIV=500 -> 100 kHz).
// Each bit is two ticks: a low half (drive SDA) and a high half (hold/sample),
// so SDA only changes while SCL is low — no spurious START/STOP conditions.
//============================================================================
module sccb_master #(
    parameter CLK_DIV = 500
) (
    input            clk,
    input            reset,
    inout            scl,
    inout            sda,
    input            start,
    input            read_mode,   // 1 = read transaction
    input      [7:0] addr,
    input      [7:0] subaddr,
    input      [7:0] data_in,
    output     [7:0] data_out,
    output           ready,
    output           error
);
    localparam [3:0] S_IDLE   = 4'd0;
    localparam [3:0] S_START  = 4'd1;
    localparam [3:0] S_ADDR_W = 4'd2;
    localparam [3:0] S_ACK1   = 4'd3;
    localparam [3:0] S_SUBA   = 4'd4;
    localparam [3:0] S_ACK2   = 4'd5;
    localparam [3:0] S_DATAW  = 4'd6;
    localparam [3:0] S_ACK3   = 4'd7;
    localparam [3:0] S_START2 = 4'd8;
    localparam [3:0] S_ADDR_R = 4'd9;
    localparam [3:0] S_ACK4   = 4'd10;
    localparam [3:0] S_DATAR  = 4'd11;
    localparam [3:0] S_NACK   = 4'd12;
    localparam [3:0] S_STOP_L = 4'd13;  // SDA low, SCL low
    localparam [3:0] S_STOP_H = 4'd14;  // SDA low, SCL high
    localparam [3:0] S_STOP_R = 4'd15;  // SDA rises (stop condition)

    reg [3:0]  state;
    reg [7:0]  bit_cnt;
    reg        sda_out;
    reg        scl_out;
    reg        scl_phase;   // 0 = SCL low half (drive SDA), 1 = SCL high half (hold/sample)
    reg        ready_reg;
    reg        error_reg;
    reg [7:0]  data_r;
    reg [15:0] divider;

    initial begin
        state      = S_IDLE;
        bit_cnt    = 8'd0;
        sda_out    = 1'b1;
        scl_out    = 1'b1;
        scl_phase  = 1'b0;
        ready_reg  = 1'b1;
        error_reg  = 1'b0;
        data_r     = 8'd0;
        divider    = 16'd0;
    end

    assign ready    = ready_reg;
    assign error    = error_reg;
    assign data_out = data_r;
    assign sda = (sda_out) ? 1'bz : 1'b0;
    assign scl = (scl_out) ? 1'bz : 1'b0;

    always @(posedge clk) begin
        if (reset) begin
            state      <= S_IDLE;
            ready_reg  <= 1'b1;
            error_reg  <= 1'b0;
            sda_out    <= 1'b1;
            scl_out    <= 1'b1;
            scl_phase  <= 1'b0;
            divider    <= 16'd0;
        end else begin
            divider <= divider + 16'd1;
            if (divider == CLK_DIV) begin
                divider <= 16'd0;
                case (state)
                    // --------------------------------------------------------
                    // Idle: bus idle (SCL/SDA high). A start request pulls SDA
                    // low while SCL is high (START condition), then preps the
                    // first byte.
                    // --------------------------------------------------------
                    S_IDLE : begin
                        if (start) begin
                            state     <= S_START;
                            ready_reg <= 1'b0;
                            sda_out   <= 1'b0;   // SDA falls while SCL high
                            scl_out   <= 1'b1;
                            scl_phase <= 1'b0;
                        end
                    end
                    S_START : begin
                        scl_out  <= 1'b0;        // prep first bit (SCL low)
                        bit_cnt  <= 8'd8;
                        scl_phase<= 1'b0;
                        state    <= S_ADDR_W;
                    end
                    // --------------------------------------------------------
                    // Write the address byte (MSB first). Two ticks per bit:
                    // low half drives SDA, high half holds SCL high.
                    // --------------------------------------------------------
                    S_ADDR_W : begin
                        if (!scl_phase) begin
                            sda_out   <= addr[bit_cnt-1];
                            scl_out   <= 1'b0;
                            scl_phase <= 1'b1;
                        end else begin
                            scl_out   <= 1'b1;
                            scl_phase <= 1'b0;
                            bit_cnt   <= bit_cnt - 8'd1;
                            if (bit_cnt == 8'd1) begin
                                sda_out <= 1'b1;  // release for ACK
                                state   <= S_ACK1;
                            end
                        end
                    end
                    // ACK1: sample the slave's ACK for the address byte.
                    S_ACK1 : begin
                        if (!scl_phase) begin
                            sda_out   <= 1'b1;   // stay released
                            scl_out   <= 1'b0;
                            scl_phase <= 1'b1;
                        end else begin
                            scl_out   <= 1'b1;
                            scl_phase <= 1'b0;
                            bit_cnt   <= 8'd8;   // prep next byte
                            if (sda == 1'b0) state <= S_SUBA;
                            else begin
                                error_reg <= 1'b1;
                                state     <= S_STOP_L;
                            end
                        end
                    end
                    // Sub-address byte (MSB first).
                    S_SUBA : begin
                        if (!scl_phase) begin
                            sda_out   <= subaddr[bit_cnt-1];
                            scl_out   <= 1'b0;
                            scl_phase <= 1'b1;
                        end else begin
                            scl_out   <= 1'b1;
                            scl_phase <= 1'b0;
                            bit_cnt   <= bit_cnt - 8'd1;
                            if (bit_cnt == 8'd1) begin
                                sda_out <= 1'b1;
                                state   <= S_ACK2;
                            end
                        end
                    end
                    // ACK2: sample ACK for the sub-address; branch on read_mode.
                    S_ACK2 : begin
                        if (!scl_phase) begin
                            sda_out   <= 1'b1;
                            scl_out   <= 1'b0;
                            scl_phase <= 1'b1;
                        end else begin
                            scl_out   <= 1'b1;
                            scl_phase <= 1'b0;
                            bit_cnt   <= 8'd8;
                            if (sda == 1'b0) state <= (read_mode) ? S_START2 : S_DATAW;
                            else begin
                                error_reg <= 1'b1;
                                state     <= S_STOP_L;
                            end
                        end
                    end
                    // Data byte (MSB first).
                    S_DATAW : begin
                        if (!scl_phase) begin
                            sda_out   <= data_in[bit_cnt-1];
                            scl_out   <= 1'b0;
                            scl_phase <= 1'b1;
                        end else begin
                            scl_out   <= 1'b1;
                            scl_phase <= 1'b0;
                            bit_cnt   <= bit_cnt - 8'd1;
                            if (bit_cnt == 8'd1) begin
                                sda_out <= 1'b1;
                                state   <= S_ACK3;
                            end
                        end
                    end
                    // ACK3: sample ACK for the data byte -> STOP.
                    S_ACK3 : begin
                        if (!scl_phase) begin
                            sda_out   <= 1'b1;
                            scl_out   <= 1'b0;
                            scl_phase <= 1'b1;
                        end else begin
                            scl_out   <= 1'b1;
                            scl_phase <= 1'b0;
                            if (sda != 1'b0) error_reg <= 1'b1;
                            state <= S_STOP_L;
                        end
                    end
                    // --------------------------------------------------------
                    // Read path: repeated START, then address-R byte.
                    // --------------------------------------------------------
                    S_START2 : begin
                        // SDA was released (high) at ACK2 high half; pull it
                        // low while SCL is high -> repeated START.
                        sda_out   <= 1'b0;
                        scl_out   <= 1'b1;
                        bit_cnt   <= 8'd8;
                        scl_phase <= 1'b0;
                        state     <= S_ADDR_R;
                    end
                    S_ADDR_R : begin
                        if (!scl_phase) begin
                            sda_out   <= ((addr | 8'h01) >> (bit_cnt-1)) & 1'b1;
                            scl_out   <= 1'b0;
                            scl_phase <= 1'b1;
                        end else begin
                            scl_out   <= 1'b1;
                            scl_phase <= 1'b0;
                            bit_cnt   <= bit_cnt - 8'd1;
                            if (bit_cnt == 8'd1) begin
                                sda_out <= 1'b1;
                                state   <= S_ACK4;
                            end
                        end
                    end
                    S_ACK4 : begin
                        if (!scl_phase) begin
                            sda_out   <= 1'b1;
                            scl_out   <= 1'b0;
                            scl_phase <= 1'b1;
                        end else begin
                            scl_out   <= 1'b1;
                            scl_phase <= 1'b0;
                            bit_cnt   <= 8'd8;
                            if (sda == 1'b0) state <= S_DATAR;
                            else begin
                                error_reg <= 1'b1;
                                state     <= S_STOP_L;
                            end
                        end
                    end
                    // Read data byte: master releases SDA, samples on SCL high.
                    S_DATAR : begin
                        if (!scl_phase) begin
                            sda_out   <= 1'b1;   // release so slave drives SDA
                            scl_out   <= 1'b0;
                            scl_phase <= 1'b1;
                        end else begin
                            scl_out           <= 1'b1;
                            data_r[bit_cnt-1] <= sda;
                            scl_phase         <= 1'b0;
                            bit_cnt           <= bit_cnt - 8'd1;
                            if (bit_cnt == 8'd1) begin
                                sda_out <= 1'b1;  // NACK after last byte
                                state   <= S_NACK;
                            end
                        end
                    end
                    // NACK: master holds SDA high for one SCL pulse, then STOP.
                    S_NACK : begin
                        if (!scl_phase) begin
                            sda_out   <= 1'b1;
                            scl_out   <= 1'b0;
                            scl_phase <= 1'b1;
                        end else begin
                            scl_out   <= 1'b1;
                            scl_phase <= 1'b0;
                            state     <= S_STOP_L;
                        end
                    end
                    // --------------------------------------------------------
                    // STOP: SDA low / SCL low -> SCL high -> SDA rises.
                    // --------------------------------------------------------
                    S_STOP_L : begin
                        sda_out <= 1'b0;
                        scl_out <= 1'b0;
                        state   <= S_STOP_H;
                    end
                    S_STOP_H : begin
                        sda_out <= 1'b0;
                        scl_out <= 1'b1;        // SCL high, SDA low
                        state   <= S_STOP_R;
                    end
                    S_STOP_R : begin
                        sda_out   <= 1'b1;      // SDA rises -> STOP
                        scl_out   <= 1'b1;
                        ready_reg <= 1'b1;
                        state     <= S_IDLE;
                    end
                    default : state <= S_IDLE;
                endcase
            end
        end
    end
endmodule