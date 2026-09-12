#!/bin/bash
# build_apps_device.sh - native build of app binaries on the BF2 device.
#
# Fallback when the WSL cross toolchain is unavailable: the device has
# its own gcc (collect_all is built there), so we build natively and
# skip static linking entirely (native glibc).
#
# Run on the device (any cwd; source tarballs must be in apps/src/,
# they are shipped in the repository).
#
# Usage: bash apps/build_apps_device.sh [xz|redis|gapbs|sqlite|blackscholes] ...
#        (no args = build all)
set -e

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
SRC="$SCRIPT_DIR/src"
BIN="$SCRIPT_DIR/bin"
JOBS=$(nproc)
XZ_VER=5.6.4
REDIS_VER=7.2.5
SQLITE_VER=3460100

TARGETS=${*:-all}
want() {
  [ "$TARGETS" = all ] && return 0
  case " $TARGETS " in
    *" $1 "*) return 0 ;;
    *) return 1 ;;
  esac
}

mkdir -p "$BIN"

if want xz; then
  echo "== xz $XZ_VER (native, static) =="
  cd /tmp
  rm -rf xz-$XZ_VER
  tar xf "$SRC/xz-$XZ_VER.tar.gz"
  cd xz-$XZ_VER
  # plain `cp src/xz/xz` copies the libtool wrapper script, which
  # execs .libs/xz at runtime and dies with "does not exist"; install
  # instead (real ELF) and link statically so the copied binary has
  # no liblzma.so runtime dependency
  ./configure --prefix="$BIN/xz-install" --disable-nls \
      --disable-shared --enable-static >/dev/null
  make -j"$JOBS" >/dev/null
  make install >/dev/null
  cp "$BIN/xz-install/bin/xz" "$BIN/xz"
  echo "xz: $(file -b "$BIN/xz")"
  "$BIN/xz" --version | head -1
fi

if want redis; then
  echo "== redis $REDIS_VER (native) =="
  cd /tmp
  rm -rf redis-$REDIS_VER
  tar xf "$SRC/redis-$REDIS_VER.tar.gz"
  cd redis-$REDIS_VER
  make -C src -j"$JOBS" MALLOC=libc BUILD_TLS=no \
      redis-server redis-benchmark redis-cli >/dev/null
  cp src/redis-server src/redis-benchmark src/redis-cli "$BIN/"
  echo "redis-server: $(file -b "$BIN/redis-server")"
  echo "redis-cli: $(file -b "$BIN/redis-cli")"
fi

if want gapbs; then
  echo "== gapbs (native) =="
  cd /tmp
  rm -rf gapbs
  tar xf "$SRC/gapbs-master.tar.gz"
  cd gapbs
  make -j"$JOBS" bfs pr cc >/dev/null
  cp bfs pr cc "$BIN/"
  echo "bfs: $(file -b "$BIN/bfs")"
fi

if want sqlite; then
  echo "== sqlite3 $SQLITE_VER (native) =="
  cd /tmp
  rm -rf sqlite-amalgamation-$SQLITE_VER
  tar xf "$SRC/sqlite-amalgamation-$SQLITE_VER.tar.gz"
  gcc -O2 -DSQLITE_THREADSAFE=0 \
      sqlite-amalgamation-$SQLITE_VER/sqlite3.c \
      sqlite-amalgamation-$SQLITE_VER/shell.c -ldl -lpthread -lm -o "$BIN/sqlite3"
  echo "sqlite3: $(file -b "$BIN/sqlite3")"
fi

if want blackscholes; then
  echo "== parsec blackscholes (native, pthreads) =="
  cd /tmp
  rm -rf bs
  mkdir bs
  tar xf "$SRC/parsec-blackscholes.tar.gz" -C bs
  # pthread version pre-expanded from c.m4.pthreads (device has no m4)
  g++ -O3 -DENABLE_THREADS -pthread bs/src/blackscholes_pthreads.c -o "$BIN/blackscholes"
  gcc -O3 bs/src/inputgen.c -o "$BIN/inputgen"
  echo "blackscholes: $(file -b "$BIN/blackscholes")"
fi

echo "== done, binaries in $BIN =="
ls -la "$BIN"
