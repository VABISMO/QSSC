# Readout signal chain

Per-signal walkthrough of one `READ_DETECTORS` round trip — the view the
board block diagram (`HARDWARE_DESIGN.md` §1) compresses into nets. The
chain crosses four domains: host ASCII protocol → FPGA 100 MHz logic →
the 9-wire AD7616 SPI bus over J12 → the analog TIA domain on the head.

```mermaid
sequenceDiagram
    participant H as Host (qssc / API)
    participant U as uart_rx → command_parser
    participant A as adc_reader (FPGA)
    participant C as 4× AD7616 (head)
    participant T as 16× TLV9004 TIA
    participant D as die DET face (64 ch)

    H->>U: ASCII command frame @ 115200-8-N-1
    U->>A: parsed readout request
    A->>C: CONVST pulse (SEQEN=HIGH)
    Note over C: 8 CONVST pulses auto-scan<br/>all 16 channels per chip
    T->>C: DET_TIA[63:0] → VA/VB pairs
    D->>T: photocurrent per electrode
    C-->>A: BUSY → MISO bitstream (16-bit)
    A->>U: 64 samples collected
    U-->>H: response frame
    Note over H: lock-in demodulation vs<br/>mod_sync (1 kHz reference)
```

- Channel mapping: 4 chips × 16 channels = 64 detector electrodes; each
  CONVST returns one VA/VB pair = 2 channels.
- The 9-wire bus (`adc_convst/sclk/miso/busy/resetn/cs0..3`) is the only
  readout path across J12 — introduced by the AD7616 pivot (ADR-0002).
- RTL implementation: `fpga/rtl/adc_reader.v` (module graph:
  `fpga-rtl.md`).