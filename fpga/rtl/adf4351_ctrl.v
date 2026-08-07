`timescale 1ns / 1ps
//============================================================================
// adf4351_ctrl.v
// ----------------------------------------------------------------------------
// ADF4351 microwave PLL controller. Given a target frequency in kHz
// (target_freq_khz), computes the six R0..R5 register words per the
// Analog Devices ADF4351 datasheet and streams them to spi_master in the
// required order (R5,R4,R3,R2,R1,R0). The reference is 25 MHz
// (REF_IN = 100 MHz TCXO / 4 on the board), R-counter=1, doubler off,
// RDIV2 off, so fPFD = 25 MHz. A fixed modulus MOD=125 gives 200 kHz
// channel resolution on the VCO. RF divider is chosen so the VCO stays in
// 2.2-4.4 GHz.
//
// RFOUT = (INT + FRAC/MOD) * fPFD / (2^RF_DIV_SEL)
//
// Register field packing (DB31..DB0, MSB shifted out first):
//   R0: [31]=0, [30:15]=INT(16b), [14:3]=FRAC(12b), [2:0]=000
//   R1: [31:29]=0, [28]=PHASE_ADJ, [27]=PRESCALER, [26:15]=PHASE(12b),
//       [14:3]=MOD(12b), [2:0]=001
//   R2: [31]=0,[30:29]=NOISE,[28:26]=MUXOUT,[25]=DOUBLER,[24]=RDIV2,
//       [23:14]=R(10b),[13]=DBL_BUF,[12:9]=CP_CURR,[8]=LDF,[7]=LDP,
//       [6]=PD_POL,[5]=PD,[4]=CP_3ST,[3]=CNT_RST,[2:0]=010
//   R3: [31:24]=0,[23]=BSC,[22]=ABP,[21]=CC,[20:19]=0,[18]=CSR,
//       [17:16]=CLKDIV_MODE,[15:3]=CLKDIV(12b),[2:0]=011
//   R4: [31:24]=0,[23]=FBK,[22:20]=RF_DIV,[19:12]=BAND_SEL,[11]=VCO_PD,
//       [10]=MTLD,[9]=AUX_SEL,[8]=AUX_EN,[7:6]=AUX_PWR,[5]=RF_EN,
//       [4:3]=RF_PWR,[2:0]=100
//   R5: [31:24]=0,[23:22]=LD_PIN,[21:3]=0,[2:0]=101
//============================================================================
module adf4351_ctrl (
    input             clk,
    input             reset,
    input             start,          // pulse to (re)program the PLL
    input      [31:0] target_freq_khz,// desired RF output in kHz
    output            busy,
    output            done,
    // SPI master interface
    output reg        spi_start,
    output reg [31:0] spi_data,
    input             spi_busy,
    input             spi_done
);
    // ---- Reference / PLL constants -----------------------------------------
    localparam [31:0] PFD_KHZ   = 32'd25000;   // 25 MHz
    localparam [31:0] MOD       = 32'd125;     // modulus (12-bit)
    localparam [31:0] VCO_MIN   = 32'd2_200_000; // 2.2 GHz
    localparam [31:0] VCO_MAX   = 32'd4_400_000; // 4.4 GHz

    // ---- Computed register values ------------------------------------------
    // r0..r5 are combinational outputs of the divider below; r0_r..r5_r are
    // stable registered copies the sequencer actually shifts out, latched once
    // per `start` so the divide is a one-shot, not a continuously-evaluating
    // combinational path on a possibly-async input.
    reg [31:0] r0, r1, r2, r3, r4, r5;
    reg [31:0] r0_r, r1_r, r2_r, r3_r, r4_r, r5_r;
    reg [31:0] tgt_reg;          // registered target_freq_khz (sampled on start)
    reg [31:0] vco_khz;
    reg [2:0]  rf_div_sel;       // 0=/1 ... 6=/64
    reg [15:0] int_n;            // integer divider
    reg [15:0] frac_n;           // fractional numerator (0..MOD-1)

    // ---- Sequencer ----------------------------------------------------------
    localparam [3:0] ST_IDLE  = 4'd0;
    localparam [3:0] ST_CALC  = 4'd1;   // settle combinational divide, latch regs
    localparam [3:0] ST_LOAD  = 4'd2;
    localparam [3:0] ST_WAIT  = 4'd3;
    localparam [3:0] ST_GAP   = 4'd4;
    localparam [3:0] ST_DONE  = 4'd5;
    reg [3:0]  state;
    reg [2:0]  reg_idx;          // 5->4->3->2->1->0
    reg [15:0] gap_cnt;
    reg        done_reg;

    initial begin
        state      = ST_IDLE;
        spi_start  = 1'b0;
        spi_data   = 32'd0;
        reg_idx    = 3'd5;
        gap_cnt    = 16'd0;
        done_reg   = 1'b0;
        r0=0; r1=0; r2=0; r3=0; r4=0; r5=0;
        r0_r=0; r1_r=0; r2_r=0; r3_r=0; r4_r=0; r5_r=0;
        tgt_reg=0; vco_khz=0; rf_div_sel=0; int_n=0; frac_n=0;
    end

    assign busy = (state != ST_IDLE);
    assign done = done_reg;

    // ---- Combinational register computation --------------------------------
    // Pure function of the *registered* target (tgt_reg), so the divide only
    // re-evaluates when a new start latches tgt_reg — not on every input
    // glitch of target_freq_khz. Implemented with a bounded loop so it is
    // synthesizable as combinational logic (division is by the localparam
    // constant PFD_KHZ, which the synthesizer maps to a multiply-by-reciprocal,
    // not a ripple divider).
    integer i;
    reg [31:0] f_khz;
    reg [31:0] vco;
    reg [2:0]  divsel;
    reg [31:0] rem_khz;
    reg [31:0] frac_calc;
    always @(*) begin
        f_khz   = tgt_reg;
        vco     = f_khz;
        divsel  = 3'd0;
        // Increase divider until VCO is in range (max 6 doublings).
        for (i = 0; i < 6; i = i + 1) begin
            if (vco < VCO_MIN) begin
                vco    = vco << 1;
                divsel = divsel + 3'd1;
            end
        end
        // Integer and fractional parts at fPFD=25 MHz, MOD=125.
        int_n   = vco / PFD_KHZ;                       // integer N
        rem_khz = vco - (int_n * PFD_KHZ);             // remainder in kHz
        frac_calc = (rem_khz * MOD) / PFD_KHZ;         // FRAC numerator
        if (frac_calc >= MOD) frac_calc = MOD - 1;     // saturate guard

        // R0: INT(30:15) | FRAC(14:3) | addr 000
        r0 = {1'b0, int_n[15:0], frac_calc[11:0], 3'b000};
        // R1: PHASE_ADJ=0, PRESCALER=0 (4/5), PHASE=1, MOD=125, addr 001
        r1 = {3'b000, 1'b0, 1'b0, 12'd1, MOD[11:0], 3'b001};
        // R2: noise=00(low), MUXOUT=001(digital lock detect), doubler=0,
        //     rdiv2=0, R=1, dbl_buf=0, CP=7(~2.19mA), LDF=1(int-N),
        //     LDP=0, PD_POL=1, PD=0, CP3ST=0, CNT_RST=0, addr 010
        r2 = {1'b0, 2'b00, 3'b001, 1'b0, 1'b0, 10'd1, 1'b0, 4'd7,
              1'b1, 1'b0, 1'b1, 1'b0, 1'b0, 1'b0, 3'b010};
        // R3: BSC=0, ABP=0, CC=0, CSR=0, CLKDIV_MODE=00, CLKDIV=0, DB3=0, addr 011
        r3 = {8'd0, 1'b0, 1'b0, 1'b0, 2'b00, 1'b0, 2'b00, 12'd0, 1'b0, 3'b011};
        // R4: FBK=0(divided), RF_DIV=divsel, BAND_SEL=200, VCO_PD=0,
        //     MTLD=1, AUX_SEL=0, AUX_EN=0, AUX_PWR=0, RF_EN=1, RF_PWR=3(+5dBm)
        r4 = {8'd0, 1'b0, divsel, 8'd200, 1'b0, 1'b1, 1'b0, 1'b0,
              2'b00, 1'b1, 2'b11, 3'b100};
        // R5: LD_PIN=01(digital lock detect), reserved bits 0, addr 101
        r5 = {8'd0, 2'b01, 19'd0, 3'b101};
    end

    // ---- Programming sequencer ---------------------------------------------
    always @(posedge clk) begin
        if (reset) begin
            state     <= ST_IDLE;
            spi_start <= 1'b0;
            reg_idx   <= 3'd5;
            gap_cnt   <= 16'd0;
            done_reg  <= 1'b0;
            tgt_reg   <= 32'd0;
        end else begin
            done_reg  <= 1'b0;
            spi_start <= 1'b0;     // default pulse
            case (state)
                ST_IDLE : begin
                    if (start) begin
                        tgt_reg <= target_freq_khz;  // sample input once
                        reg_idx <= 3'd5;             // program R5 first
                        state  <= ST_CALC;
                    end
                end
                ST_CALC : begin
                    // combinational divide has now settled from tgt_reg; latch
                    // stable copies the sequencer will shift out.
                    r0_r <= r0; r1_r <= r1; r2_r <= r2;
                    r3_r <= r3; r4_r <= r4; r5_r <= r5;
                    state <= ST_LOAD;
                end
                ST_LOAD : begin
                    // Select the register word for the current index.
                    case (reg_idx)
                        3'd5: spi_data <= r5_r;
                        3'd4: spi_data <= r4_r;
                        3'd3: spi_data <= r3_r;
                        3'd2: spi_data <= r2_r;
                        3'd1: spi_data <= r1_r;
                        3'd0: spi_data <= r0_r;
                        default: spi_data <= 32'd0;
                    endcase
                    spi_start <= 1'b1;
                    state     <= ST_WAIT;
                end
                ST_WAIT : begin
                    if (spi_done) begin
                        if (reg_idx == 3'd0) begin
                            state <= ST_DONE;
                        end else begin
                            reg_idx  <= reg_idx - 3'd1;
                            gap_cnt  <= 16'd0;
                            state    <= ST_GAP;
                        end
                    end
                end
                ST_GAP : begin
                    // small idle gap between registers
                    if (gap_cnt == 16'd31) begin
                        state <= ST_LOAD;
                    end else begin
                        gap_cnt <= gap_cnt + 16'd1;
                    end
                end
                ST_DONE : begin
                    done_reg <= 1'b1;
                    state    <= ST_IDLE;
                end
                default : state <= ST_IDLE;
            endcase
        end
    end
endmodule