# pcie_total.plt - one counter, one bar per application per data path.
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
# Data: fig/pcie_total.dat (raw differenced rates).
#
# Usage: gnuplot fig/pcie_total.plt   (from the repo root)

set terminal pngcairo size 1200,600 enhanced font 'Arial,16'
set output 'fig/pcie_total.png'

set ylabel 'PCIe bytes (e+6)' font 'Arial,22'
set xlabel 'Applications' font 'Arial,22'

set style fill solid border -1
set boxwidth 0.30

set yrange [0:300]
set format y '%.0f'
set ytics 50

set border 15
set ytics nomirror
set xtics nomirror

# xtics anchored at the cluster centers (1..7); the app
# names come from fig/pcie_total.dat column 1.
set xtics ('xz' 1, 'BFS' 2, 'Redis' 3, 'SQLite' 4, 'BS' 5, 'TFLite' 6, 'Mix' 7) font ',20'
set xrange [0:8]

set tmargin 4
set key outside top right horizontal font 'Arial,20'

C_PCIE0 = "#B2172B"
C_PCIE1 = "#F5A682"

plot 'fig/pcie_total.dat' using ($0+1-0.150):($2/1e6) with boxes title 'PCIe0' lc rgb C_PCIE0, \
     '' using ($0+1+0.150):($3/1e6) with boxes title 'PCIe1' lc rgb C_PCIE1
