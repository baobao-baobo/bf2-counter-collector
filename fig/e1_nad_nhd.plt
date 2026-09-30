# e1_nad_nhd.plt - NAD/NHD eSwitch egress split: 5 workloads, Host vs Arm bars.
#
# Same style as fig/tile_mem_reads.plt: pngcairo 1200x600, Arial
# 16/22/20, opaque fill with black border, key outside top right
# horizontal, full 4-side frame (border 15), tics only on left y /
# bottom x (nomirror).  2-series colors: #B2172B (left, Host) /
# #F5A682 (right, Arm).
#
# Log y axis: workload rates span 8 Mbps (iperf-NAD ACK return) to
# 10 Gbps (iperf-NHD).  Coarse decade tics only (10^{%L}, no minor
# tics), plain "1" at 10^0.  The 2e-4 dat floor draws a visible
# stub for the ~0 bars (true zero is off a log axis).
#
# Data: fig/e1_nad_nhd.dat (tools/verify_nad_nhd.py output).
#
# Usage: tools/gnuplot fig/e1_nad_nhd.plt   (from the repo root)

set terminal pngcairo size 1200,600 enhanced font 'Arial,16'
set output 'fig/e1_nad_nhd.png'

set ylabel 'eSwitch egress rate (Gbps)' font 'Arial,22'
set xlabel 'Applications' font 'Arial,22'

set style fill solid border -1
set boxwidth 0.22

set yrange [1e-4:20]
set logscale y 10
unset mytics
set format y '10^{%L}'
set ytics add ("1" 1)

set border 15
set ytics nomirror
set xtics nomirror

set xtics ('iperf-NAD' 1, 'iperf-NHD' 2, 'Redis-Arm' 3, \
           'Redis+SQLite' 4, 'Redis-Host' 5) font ',20'
set xrange [0:6]

set tmargin 4
set key outside top right horizontal font 'Arial,20'

C_HOST = "#B2172B"
C_ARM  = "#F5A682"

plot 'fig/e1_nad_nhd.dat' using ($0+1-0.11):2 with boxes title 'NHD' lc rgb C_HOST, \
     '' using ($0+1+0.11):3 with boxes title 'NAD' lc rgb C_ARM
