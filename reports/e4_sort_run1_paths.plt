set terminal pngcairo size 1280,960 font ",14"
set output "reports/e4_sort_run1_paths.png"
set encoding utf8
set border 15 lw 1.2
set xtics nomirror
set ytics nomirror
set boxwidth 0.72
set style fill solid 0.85 border -1
set multiplot layout 2,1 title "e4_sort_run1 -- dominant cr (med 0.216, wins cr=13/25)" font ",16"

# ---- panel 1: magnitude criterion ----
set xrange [0.5:7.5]
set yrange [0:0.350]
set format x ""
set ylabel "median L_p (window-mean median)" font ",14"
set title "magnitude criterion" font ",15"
set key outside top center horizontal
plot "reports/e4_sort_run1_paths_mag.dat" using 1:2 with boxes lc rgb "#D0D0D0" title "other paths", \
     "reports/e4_sort_run1_paths_mag.dat" using 1:($1==1 ? $2 : NaN) with boxes lc rgb "#B2172B" title "winner", \
     0.2 with lines dashtype 2 lc rgb "#555555" title "0.2 floor", \
     "reports/e4_sort_run1_paths_mag.dat" using 1:($2+0.035*0.350):($2>0.0005 ? sprintf("%.2f",$2) : "") with labels font ",11" notitle

# ---- panel 2: direction criterion ----
set xrange [0.5:7.5]
set yrange [0:15.600]
set format x
set xtics ("cr" 1, "ih" 2, "ib" 3, "wb" 4, "nad" 5, "nhd" 6, "tx" 7) font ",13"
set ylabel "row wins" font ",14"
set title "direction criterion (per-row votes)" font ",15"
set key outside top center horizontal
plot "reports/e4_sort_run1_paths_dir.dat" using 1:2 with boxes lc rgb "#D0D0D0" title "other paths", \
     "reports/e4_sort_run1_paths_dir.dat" using 1:($1==1 ? $2 : NaN) with boxes lc rgb "#F5A682" title "wins leader", \
     12.50 with lines dashtype 2 lc rgb "#555555" title "majority", \
     "reports/e4_sort_run1_paths_dir.dat" using 1:($2+0.035*15.600):($2>0 ? sprintf("%d",$2) : "") with labels font ",11" notitle

unset multiplot
