# l3_total_emem_rd_req.plt - one counter, one bar per application per data path.
#
# Style mirrors the paper bar charts (image/*.plt): clustered
# bars, Arial 16/22/20, opaque fill with black border, key
# outside top right.  Bars use `with boxes` with explicit
# width and offsets (see tools/gen_fig_plts.py); xrange
# [0:8] centers the 7 application groups.  Full 4-side
# frame (border 15), tics only on left y / bottom x
# (nomirror).  Colors: 2-series figures use #B2172B
# (left, dark) / #F5A682 (right, light); the 3-series
# figure keeps dark gray / light gray / red.
#
# Log y axis: raw counts/s, starts at 1, coarse decade
# tics 1, 10, 100, ... only (10^{%L}, no minor tics);
# the IH series would be invisible on a linear axis.
#
# Data: fig/l3_total_emem_rd_req.dat (raw differenced rates).
#
# Usage: gnuplot fig/l3_total_emem_rd_req.plt   (from the repo root)

set terminal pngcairo size 1200,600 enhanced font 'Arial,16'
set output 'fig/l3_total_emem_rd_req.png'

set ylabel 'L3 TOTAL\_EMEM\_RD\_REQ (counts/s)' font 'Arial,22'
set xlabel 'Applications' font 'Arial,22'

set style fill solid border -1
set boxwidth 0.30

set yrange [1:5e+07]
set logscale y 10
unset mytics
set format y '10^{%L}'
set ytics add ("1" 1)

set border 15
set ytics nomirror
set xtics nomirror

# xtics anchored at the cluster centers (1..7); the app
# names come from fig/l3_total_emem_rd_req.dat column 1.
set xtics ('xz' 1, 'BFS' 2, 'Redis' 3, 'SQLite' 4, 'BS' 5, 'TFLite' 6, 'Mix' 7) font ',20'
set xrange [0:8]

set tmargin 4
set key outside top right horizontal font 'Arial,20'

C_CR = "#B2172B"
C_IH = "#F5A682"

plot 'fig/l3_total_emem_rd_req.dat' using ($0+1-0.150):2 with boxes title 'CR' lc rgb C_CR, \
     '' using ($0+1+0.150):3 with boxes title 'IH' lc rgb C_IH
