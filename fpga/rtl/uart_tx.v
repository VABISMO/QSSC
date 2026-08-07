`timescale 1ns / 1ps
//============================================================================
// uart_tx.v
// ----------------------------------------------------------------------------
// Standard 8-N-1 UART transmitter for the QSSC control board.
// Parameter CLKS_PER_BIT defaults to 868 for 100 MHz / 115200 baud.
// Assert i_Tx_DV with i_Tx_Byte for one clock to start a transfer.
// o_Tx_Active is high while a frame is being shifted out.
// o_Tx_Done pulses for one clock when the stop bit has been sent.
//============================================================================
module uart_tx #(
    parameter CLKS_PER_BIT = 868      // 100 MHz / 115200 ~= 868
) (
    input        i_Clock,
    input        i_Tx_DV,
    input [7:0]  i_Tx_Byte,
    output       o_Tx_Active,
    output reg   o_Tx_Serial,
    output       o_Tx_Done
);
    localparam [2:0] s_IDLE         = 3'd0;
    localparam [2:0] s_TX_START_BIT = 3'd1;
    localparam [2:0] s_TX_DATA_BITS = 3'd2;
    localparam [2:0] s_TX_STOP_BIT  = 3'd3;
    localparam [2:0] s_CLEANUP      = 3'd4;

    reg [2:0]  r_SM_Main;
    reg [15:0] r_Clock_Count;
    reg [2:0]  r_Bit_Index;
    reg [7:0]  r_Tx_Data;
    reg        r_Tx_Done;
    reg        r_Tx_Active;

    initial begin
        r_SM_Main     = s_IDLE;
        r_Clock_Count = 16'd0;
        r_Bit_Index   = 3'd0;
        r_Tx_Data     = 8'd0;
        r_Tx_Done     = 1'b0;
        r_Tx_Active   = 1'b0;
        o_Tx_Serial   = 1'b1;       // idle line high
    end

    always @(posedge i_Clock) begin
        case (r_SM_Main)
            s_IDLE : begin
                o_Tx_Serial   <= 1'b1;
                r_Tx_Done     <= 1'b0;
                r_Clock_Count <= 16'd0;
                r_Bit_Index   <= 3'd0;
                if (i_Tx_DV == 1'b1) begin
                    r_Tx_Active <= 1'b1;
                    r_Tx_Data   <= i_Tx_Byte;
                    r_SM_Main   <= s_TX_START_BIT;
                end else begin
                    r_SM_Main   <= s_IDLE;
                end
            end
            s_TX_START_BIT : begin
                o_Tx_Serial <= 1'b0;
                if (r_Clock_Count < CLKS_PER_BIT-1) begin
                    r_Clock_Count <= r_Clock_Count + 16'd1;
                    r_SM_Main     <= s_TX_START_BIT;
                end else begin
                    r_Clock_Count <= 16'd0;
                    r_SM_Main     <= s_TX_DATA_BITS;
                end
            end
            s_TX_DATA_BITS : begin
                o_Tx_Serial <= r_Tx_Data[r_Bit_Index];
                if (r_Clock_Count < CLKS_PER_BIT-1) begin
                    r_Clock_Count <= r_Clock_Count + 16'd1;
                    r_SM_Main     <= s_TX_DATA_BITS;
                end else begin
                    r_Clock_Count <= 16'd0;
                    if (r_Bit_Index < 7) begin
                        r_Bit_Index <= r_Bit_Index + 3'd1;
                        r_SM_Main   <= s_TX_DATA_BITS;
                    end else begin
                        r_Bit_Index <= 3'd0;
                        r_SM_Main   <= s_TX_STOP_BIT;
                    end
                end
            end
            s_TX_STOP_BIT : begin
                o_Tx_Serial <= 1'b1;
                if (r_Clock_Count < CLKS_PER_BIT-1) begin
                    r_Clock_Count <= r_Clock_Count + 16'd1;
                    r_SM_Main     <= s_TX_STOP_BIT;
                end else begin
                    r_Tx_Done     <= 1'b1;
                    r_Clock_Count <= 16'd0;
                    r_Tx_Active   <= 1'b0;
                    r_SM_Main     <= s_CLEANUP;
                end
            end
            s_CLEANUP : begin
                r_Tx_Done <= 1'b0;
                r_SM_Main <= s_IDLE;
            end
            default : r_SM_Main <= s_IDLE;
        endcase
    end

    assign o_Tx_Active = r_Tx_Active;
    assign o_Tx_Done   = r_Tx_Done;
endmodule