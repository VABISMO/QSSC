; =====================================================================
; QSSC CNC DEMO TOOLPATH — carried over from the QNOS project, NOT CAM-validated.
; VERIFY in your CAM before EVERY run: stock size, workpiece zero, fixturing,
;   tooling, feeds/speeds, and travel clearance. Do not run this file "as-is".
; SAFETY: the spindle starts immediately (M03 S8000) with no safe-start block —
;   keep the work area clear and confirm Z zero before cycle start.
; The final "Outline cut" is a THROUGH-cut (Z = -3.0 mm on a 3 mm sheet) that
;   travels OUTSIDE the 30 mm part (-1..31) — ensure NO clamps or fixturing
;   lie in the -1..31 outline path, or delete that section in CAM.
; Pocketing leaves 64 (8x8) 2 mm-dia cylinders on a 1 mm base; verify the 2 mm
;   flat bit and that the pocket passes clear chips between Z-steps.
; =====================================================================
G21 G90 G94 G17
; G-code for pocketing to leave an array of 2mm diameter cylinders on a 1mm base from a 30mm x 30mm x 3mm acrylic sheet using a 2mm bit
; Number of cylinders: 64 (8x8)
M03 S8000
G0 X0.0 Y3.0 Z10.0
G1 Z-1.0 F100
G1 X30.0 F200
G0 Z10.0
G0 X0.0 Y7.0 Z10.0
G1 Z-1.0 F100
G1 X30.0 F200
G0 Z10.0
G0 X0.0 Y11.0 Z10.0
G1 Z-1.0 F100
G1 X30.0 F200
G0 Z10.0
G0 X0.0 Y15.0 Z10.0
G1 Z-1.0 F100
G1 X30.0 F200
G0 Z10.0
G0 X0.0 Y19.0 Z10.0
G1 Z-1.0 F100
G1 X30.0 F200
G0 Z10.0
G0 X0.0 Y23.0 Z10.0
G1 Z-1.0 F100
G1 X30.0 F200
G0 Z10.0
G0 X0.0 Y27.0 Z10.0
G1 Z-1.0 F100
G1 X30.0 F200
G0 Z10.0
G0 X3.0 Y0.0 Z10.0
G1 Z-1.0 F100
G1 Y30.0 F200
G0 Z10.0
G0 X7.0 Y0.0 Z10.0
G1 Z-1.0 F100
G1 Y30.0 F200
G0 Z10.0
G0 X11.0 Y0.0 Z10.0
G1 Z-1.0 F100
G1 Y30.0 F200
G0 Z10.0
G0 X15.0 Y0.0 Z10.0
G1 Z-1.0 F100
G1 Y30.0 F200
G0 Z10.0
G0 X19.0 Y0.0 Z10.0
G1 Z-1.0 F100
G1 Y30.0 F200
G0 Z10.0
G0 X23.0 Y0.0 Z10.0
G1 Z-1.0 F100
G1 Y30.0 F200
G0 Z10.0
G0 X27.0 Y0.0 Z10.0
G1 Z-1.0 F100
G1 Y30.0 F200
G0 Z10.0
G0 X0.0 Y3.0 Z10.0
G1 Z-2.0 F100
G1 X30.0 F200
G0 Z10.0
G0 X0.0 Y7.0 Z10.0
G1 Z-2.0 F100
G1 X30.0 F200
G0 Z10.0
G0 X0.0 Y11.0 Z10.0
G1 Z-2.0 F100
G1 X30.0 F200
G0 Z10.0
G0 X0.0 Y15.0 Z10.0
G1 Z-2.0 F100
G1 X30.0 F200
G0 Z10.0
G0 X0.0 Y19.0 Z10.0
G1 Z-2.0 F100
G1 X30.0 F200
G0 Z10.0
G0 X0.0 Y23.0 Z10.0
G1 Z-2.0 F100
G1 X30.0 F200
G0 Z10.0
G0 X0.0 Y27.0 Z10.0
G1 Z-2.0 F100
G1 X30.0 F200
G0 Z10.0
G0 X3.0 Y0.0 Z10.0
G1 Z-2.0 F100
G1 Y30.0 F200
G0 Z10.0
G0 X7.0 Y0.0 Z10.0
G1 Z-2.0 F100
G1 Y30.0 F200
G0 Z10.0
G0 X11.0 Y0.0 Z10.0
G1 Z-2.0 F100
G1 Y30.0 F200
G0 Z10.0
G0 X15.0 Y0.0 Z10.0
G1 Z-2.0 F100
G1 Y30.0 F200
G0 Z10.0
G0 X19.0 Y0.0 Z10.0
G1 Z-2.0 F100
G1 Y30.0 F200
G0 Z10.0
G0 X23.0 Y0.0 Z10.0
G1 Z-2.0 F100
G1 Y30.0 F200
G0 Z10.0
G0 X27.0 Y0.0 Z10.0
G1 Z-2.0 F100
G1 Y30.0 F200
G0 Z10.0
G0 X-1.0 Y-1.0 Z10.0
G1 Z-1.0 F100
G1 X31.0 F200
G1 Y31.0
G1 X-1.0
G1 Y-1.0
G0 Z10.0
G0 X-1.0 Y-1.0 Z10.0
G1 Z-2.0 F100
G1 X31.0 F200
G1 Y31.0
G1 X-1.0
G1 Y-1.0
G0 Z10.0
G0 X-1.0 Y-1.0 Z10.0
G1 Z-3.0 F100
G1 X31.0 F200
G1 Y31.0
G1 X-1.0
G1 Y-1.0
G0 Z10.0
G0 Z50.0
M05
G0 X0.0 Y0.0
M02