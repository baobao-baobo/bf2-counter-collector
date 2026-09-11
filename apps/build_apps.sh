#!/bin/bash
# build_apps.sh - cross-compile static aarch64 app binaries (run in WSL).
#
# Reads source tarballs from apps/src (downloaded on Windows via proxy)
# and writes static binaries into apps/bin.  Static linking is required:
# the device runs Ubuntu 20.04 (glibc 2.31) while this toolchain links
# against a newer glibc.
#
# Usage (WSL):  bash apps/build_apps.sh [xz|redis|all]
set -e

SRC=/mnt/d/bf2-collector/apps/src
BIN=/mnt/d/bf2-collector/apps/bin
JOBS=$(nproc)
XZ_VER=5.6.4
REDIS_VER=7.2.5
CC=aarch64-linux-gnu-gcc

mkdir -p "$BIN"
TARGET=${1:-all}

if [ "$TARGET" = all ] || [ "$TARGET" = xz ]; then
  echo "== xz $XZ_VER =="
  cd /tmp
  rm -rf xz-$XZ_VER
  tar xf "$SRC/xz-$XZ_VER.tar.gz"
  cd xz-$XZ_VER
  ./configure --host=aarch64-linux-gnu --disable-shared --disable-nls \
      CC=$CC CFLAGS="-O2" LDFLAGS="-static" >/dev/null
  make -j"$JOBS" >/dev/null
  cp src/xz/xz "$BIN/xz"
  echo "xz: $(file -b "$BIN/xz")"
fi

if [ "$TARGET" = all ] || [ "$TARGET" = redis ]; then
  echo "== redis $REDIS_VER =="
  cd /tmp
  rm -rf redis-$REDIS_VER
  tar xf "$SRC/redis-$REDIS_VER.tar.gz"
  cd redis-$REDIS_VER
  make -C src -j"$JOBS" MALLOC=libc BUILD_TLS=no CC=$CC \
      LDFLAGS="-static" redis-server redis-benchmark redis-cli >/dev/null
  cp src/redis-server src/redis-benchmark src/redis-cli "$BIN/"
  echo "redis-server: $(file -b "$BIN/redis-server")"
  echo "redis-benchmark: $(file -b "$BIN/redis-benchmark")"
  echo "redis-cli: $(file -b "$BIN/redis-cli")"
fi

echo "== done, binaries in $BIN =="
ls -la "$BIN"
