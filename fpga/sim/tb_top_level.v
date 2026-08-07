`timescale 1ns / 1ps
//============================================================================
// tb_top_level.v
// ----------------------------------------------------------------------------
// Simple testbench for the QSSC top_level. Generates a 100 MHz clock and
// reset, sends the ASCII command "FIRE_LASER 3 4 50\n" over the UART RX
// line at 115200 baud, then observes the UART TX reply and the laser row/col
// outputs. Self-checks the reply ("OK\n") and the decoded laser pattern and
// reports PASS/FAIL. Camera/SCCB/RF pins are left idle for this test.
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
    wire [7:0]  laser_rows;
    wire [7:0]  laser_cols;
    wire        scl, sda;
    wire        xclk;
    reg         pclk  = 1'b0;
    reg         vsync = 1'b0;
    reg         href  = 1'b0;
    reg  [7:0]  d     = 8'd0;
    wire [3:0]  spi_pins;
    wire        refclk_out;

    // ---- Captured TX bytes -------------------------------------------------
    reg [7:0]   rx_log [0:15];
    integer     rx_log_cnt = 0;
    integer     i;
    reg [7:0]   rx_byte_cap;
    integer     ok_count = 0;
    integer     err_count = 0;

    // ---- DUT ---------------------------------------------------------------
    top_level dut (
        .clk_100mhz (clk_100mhz),
        .reset      (reset),
        .rx         (rx),
        .tx         (tx),
        .laser_rows (laser_rows),
        .laser_cols (laser_cols),
        .scl        (scl),
        .sda        (sda),
        .xclk       (xclk),
        .pclk       (pclk),
        .vsync      (vsync),
        .href       (href),
        .d          (d),
        .spi_pins   (spi_pins),
        .refclk_out (refclk_out)
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
                rx = b[k];                   // LSB first
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
                if (rx_log_cnt < 16) begin
                    rx_log[rx_log_cnt] = rx_byte_cap;
                    rx_log_cnt = rx_log_cnt + 1;
                    $display("TB: TX byte %0d = 0x%02h (%c)",
                             rx_log_cnt, rx_byte_cap,
                             ((rx_byte_cap >= 8'h20 && rx_byte_cap < 8'h7F) ? rx_byte_cap : 8'h2E));
                end
            end
        end
    end

    // ---- Main stimulus -----------------------------------------------------
    integer c;
    integer r;
    reg [7:0] cmd_bytes [0:15];
    initial begin
        // Build the command string "FIRE_LASER 3 4 50\n"
        cmd_bytes[0]="F"; cmd_bytes[1]="I"; cmd_bytes[2]="R"; cmd_bytes[3]="E";
        cmd_bytes[4]="_"; cmd_bytes[5]="L"; cmd_bytes[6]="A"; cmd_bytes[7]="S";
        cmd_bytes[8]="E"; cmd_bytes[9]="R"; cmd_bytes[10]=" "; cmd_bytes[11]="3";
        cmd_bytes[12]=" "; cmd_bytes[13]="4"; cmd_bytes[14]=" "; cmd_bytes[15]="5";
        // (the '0' and '\n' are sent explicitly below to fit 17 chars)

        // Reset
        reset = 1'b1;
        for (r=0;r<20;r=r+1) begin @(posedge clk_100mhz); end
        reset = 1'b0;
        for (r=0;r<5;r=r+1) begin @(posedge clk_100mhz); end

        $display("TB: sending FIRE_LASER 3 4 50");
        for (c = 0; c < 16; c = c + 1) begin
            uart_send_byte(cmd_bytes[c]);
        end
        uart_send_byte("0");
        uart_send_byte(8'h0A);   // '\n'

        // Wait for the parser to react, the laser to fire, and the "OK\n"
        // reply (3 bytes ~= 26000 cycles at 868 clk/bit) to be transmitted.
        for (r=0;r<100000;r=r+1) begin @(posedge clk_100mhz); end

        // ---- Self-checks ---------------------------------------------------
        $display("TB: laser_rows=0x%02h (expect 0x08), laser_cols=0x%02h (expect 0xEF)",
                 laser_rows, laser_cols);

        if (laser_rows === 8'h08) ok_count = ok_count + 1;
        else begin
            err_count = err_count + 1;
            $display("TB: FAIL laser_rows mismatch");
        end
        if (laser_cols === 8'hEF) ok_count = ok_count + 1;
        else begin
            err_count = err_count + 1;
            $display("TB: FAIL laser_cols mismatch");
        end

        // Check the reply contained "OK\n" (0x4F 0x4B 0x0A)
        if (rx_log_cnt >= 3 &&
            rx_log[0] === 8'h4F && rx_log[1] === 8'h4B && rx_log[2] === 8'h0A) begin
            ok_count = ok_count + 1;
            $display("TB: reply = OK\\n  (PASS)");
        end else begin
            err_count = err_count + 1;
            $display("TB: FAIL reply (got %0d bytes, first=0x%02h)",
                     rx_log_cnt, (rx_log_cnt>0 ? rx_log[0] : 8'h00));
        end

        // Let the laser timer expire (~50 ms = 5,000,000 cycles) and check off.
        // (This is a long wait; we only sample shortly after to confirm on-state.)
        for (r=0;r<200000;r=r+1) begin @(posedge clk_100mhz); end
        $display("TB: after wait, laser_rows=0x%02h", laser_rows);

        if (err_count == 0)
            $display("TB: === ALL CHECKS PASSED ===");
        else
            $display("TB: === %0d CHECK(S) FAILED ===", err_count);

        $finish;
    end

    // ---- Safety timeout ----------------------------------------------------
    initial begin
        #200_000_000;   // 200 ms
        $display("TB: TIMEOUT");
        $finish;
    end
endmodule