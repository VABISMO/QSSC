`timescale 1ns / 1ps
//============================================================================
// uart_rx.v
// ----------------------------------------------------------------------------
// Standard 8-N-1 UART receiver for the QSSC control board.
// Parameter CLKS_PER_BIT defaults to 868 for 100 MHz / 115200 baud.
// One start bit, 8 data bits (LSB first), one stop bit. Outputs a one-cycle
// data-valid strobe (o_Rx_DV) together with the decoded byte (o_Rx_Byte).
//============================================================================
module uart_rx #(
    parameter CLKS_PER_BIT = 868      // 100 MHz / 115200 ~= 868
) (
    input        i_Clock,
    input        i_Rx_Serial,
    output       o_Rx_DV,
    output [7:0] o_Rx_Byte
);
    localparam [2:0] s_IDLE          = 3'd0;
    localparam [2:0] s_RX_START_BIT  = 3'd1;
    localparam [2:0] s_RX_DATA_BITS  = 3'd2;
    localparam [2:0] s_RX_STOP_BIT   = 3'd3;
    localparam [2:0] s_CLEANUP       = 3'd4;

    reg        r_Rx_Data_R;
    reg        r_Rx_Data;
    reg [15:0] r_Clock_Count;
    reg [2:0]  r_Bit_Index;
    reg [7:0]  r_Rx_Byte;
    reg        r_Rx_DV;
    reg [2:0]  r_SM_Main;

    initial begin
        r_Rx_Data_R  = 1'b1;
        r_Rx_Data    = 1'b1;
        r_Clock_Count = 16'd0;
        r_Bit_Index  = 3'd0;
        r_Rx_Byte    = 8'd0;
        r_Rx_DV      = 1'b0;
        r_SM_Main    = s_IDLE;
    end

    // Two-stage input synchronizer / edge filter.
    always @(posedge i_Clock) begin
        r_Rx_Data_R <= i_Rx_Serial;
        r_Rx_Data   <= r_Rx_Data_R;
    end

    // Receive state machine.
    always @(posedge i_Clock) begin
        case (r_SM_Main)
            s_IDLE : begin
                r_Rx_DV       <= 1'b0;
                r_Clock_Count <= 16'd0;
                r_Bit_Index   <= 3'd0;
                if (r_Rx_Data == 1'b0)
                    r_SM_Main <= s_RX_START_BIT;
                else
                    r_SM_Main <= s_IDLE;
            end
            s_RX_START_BIT : begin
                if (r_Clock_Count == (CLKS_PER_BIT-1)/2) begin
                    if (r_Rx_Data == 1'b0) begin
                        r_Clock_Count <= 16'd0;
                        r_SM_Main     <= s_RX_DATA_BITS;
                    end else begin
                        r_SM_Main <= s_IDLE;
                    end
                end else begin
                    r_Clock_Count <= r_Clock_Count + 16'd1;
                    r_SM_Main     <= s_RX_START_BIT;
                end
            end
            s_RX_DATA_BITS : begin
                if (r_Clock_Count < CLKS_PER_BIT-1) begin
                    r_Clock_Count <= r_Clock_Count + 16'd1;
                    r_SM_Main     <= s_RX_DATA_BITS;
                end else begin
                    r_Clock_Count      <= 16'd0;
                    r_Rx_Byte[r_Bit_Index] <= r_Rx_Data;
                    if (r_Bit_Index < 7) begin
                        r_Bit_Index <= r_Bit_Index + 3'd1;
                        r_SM_Main   <= s_RX_DATA_BITS;
                    end else begin
                        r_Bit_Index <= 3'd0;
                        r_SM_Main   <= s_RX_STOP_BIT;
                    end
                end
            end
            s_RX_STOP_BIT : begin
                if (r_Clock_Count < CLKS_PER_BIT-1) begin
                    r_Clock_Count <= r_Clock_Count + 16'd1;
                    r_SM_Main     <= s_RX_STOP_BIT;
                end else begin
                    r_Rx_DV       <= 1'b1;
                    r_Clock_Count <= 16'd0;
                    r_SM_Main     <= s_CLEANUP;
                end
            end
            s_CLEANUP : begin
                r_SM_Main <= s_IDLE;
                r_Rx_DV   <= 1'b0;
            end
            default : r_SM_Main <= s_IDLE;
        endcase
    end

    assign o_Rx_DV   = r_Rx_DV;
    assign o_Rx_Byte = r_Rx_Byte;
endmodule