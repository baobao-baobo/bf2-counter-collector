# l3_rd_chain.plt - stacked composition, one bar per application.
#
# Style mirrors the paper bar charts (image/*.plt) via
# tools/gen_fig_plts.py: Arial 16/22/20, opaque fill with
# black segment borders, key outside top right, full
# 4-side frame (border 15), tics only on left y / bottom
# x (nomirror), xrange [0:8] centers the 7 applications.
#
# Stacked bars need the linear axis: values / 1e6,
# ylabel (e+6), coarse integer tics (1/2/5 x 10^k).
# Segments stack bottom -> top in the key order;
# boxxyerror draws each segment from the cumulative sum
# of the segments below up to the cumulative including
# itself, so zero-height segments are skipped.
#
# Data: fig/l3_rd_chain.dat (tools/path_data.py STACK_SPECS).
#
# Usage: gnuplot fig/l3_rd_chain.plt   (from the repo root)

set terminal pngcairo size 1200,600 enhanced font 'Arial,16'
set output 'fig/l3_rd_chain.png'

set ylabel 'L3 RD CHAIN: CACHE + EMEM (e+6)' font 'Arial,22'
set xlabel 'Applications' font 'Arial,22'

set style fill solid border -1

set yrange [0:65]
set format y '%.0f'
set ytics 10

set border 15
set ytics nomirror
set xtics nomirror

set xtics ('xz' 1, 'BFS' 2, 'Redis' 3, 'SQLite' 4, 'BS' 5, 'TFLite' 6, 'Mix' 7) font ',20'
set xrange [0:8]

set tmargin 4
set key outside top right horizontal font 'Arial,20'

C0 = "#C1A8E0"   # CACHE_RD_RES_IN
C1 = "#F7E6A0"   # EMEM_RD_REQ

plot 'fig/l3_rd_chain.dat' using ($0+1):((($2)) > (0) ? (($2))/1e6 : 1/0):(($0+1)-0.275):(($0+1)+0.275):(0/1e6):(($2)/1e6) with boxxyerror title 'CACHE\_RD\_RES\_IN' lc rgb C0, \
     '' using ($0+1):((($2+$3)) > (($2)) ? (($2+$3))/1e6 : 1/0):(($0+1)-0.275):(($0+1)+0.275):(($2)/1e6):(($2+$3)/1e6) with boxxyerror title 'EMEM\_RD\_REQ' lc rgb C1
