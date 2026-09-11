#!/bin/bash
# build_apps_device.sh - native build of app binaries on the BF2 device.
#
# Fallback when the WSL cross toolchain is unavailable: the device has
# its own gcc (collect_all is built there), so we build natively and
# skip static linking entirely (native glibc).
#
# Run on the device from /root/bf2k (source tarballs must be in
# apps/src/, they are shipped in the repository).
set -e

SRC=$(dirname "$0")/src
BIN=$(dirname "$0")/bin
JOBS=$(nproc)
XZ_VER=5.6.4
REDIS_VER=7.2.5

mkdir -p "$BIN"

echo "== xz $XZ_VER (native) =="
cd /tmp
rm -rf xz-$XZ_VER
tar xf "$SRC/xz-$XZ_VER.tar.gz"
cd xz-$XZ_VER
./configure --disable-nls >/dev/null
make -j"$JOBS" >/dev/null
cp src/xz/xz "$BIN/xz"
echo "xz: $(file -b "$BIN/xz")"

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

echo "== done, binaries in $BIN =="
ls -la "$BIN"
