# net_traffic.plt - one counter, one bar per application per data path.
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
# Linear y axis: values / 1e6 (see the using clause),
# ylabel unit (e+6), yrange just above the data max,
# coarse integer tics (1/2/5 x 10^k step).
#
# Data: fig/net_traffic.dat (raw differenced rates).
#
# Usage: gnuplot fig/net_traffic.plt   (from the repo root)

set terminal pngcairo size 1200,600 enhanced font 'Arial,16'
set output 'fig/net_traffic.png'

set ylabel 'Network bytes (e+6)' font 'Arial,22'
set xlabel 'Applications' font 'Arial,22'

set style fill solid border -1
set boxwidth 0.30

set yrange [0:20]
set format y '%.0f'
set ytics 2

set border 15
set ytics nomirror
set xtics nomirror

# xtics anchored at the cluster centers (1..7); the app
# names come from fig/net_traffic.dat column 1.
set xtics ('xz' 1, 'BFS' 2, 'Redis' 3, 'SQLite' 4, 'BS' 5, 'TFLite' 6, 'Mix' 7) font ',20'
set xrange [0:8]

set tmargin 4
set key outside top right horizontal font 'Arial,20'

C_RX = "#B2172B"
C_TX = "#F5A682"

plot 'fig/net_traffic.dat' using ($0+1-0.150):($2/1e6) with boxes title 'RX' lc rgb C_RX, \
     '' using ($0+1+0.150):($3/1e6) with boxes title 'TX' lc rgb C_TX
