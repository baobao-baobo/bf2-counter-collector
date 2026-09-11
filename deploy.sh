#!/bin/bash
# deploy.sh - one-shot GitHub -> BF2 deploy. Run on fujian.
#
#   bash deploy.sh          sync repo to /root/bf2k on the device
#   bash deploy.sh -b       same, then rebuild the collector (make)
#
# The BF2 has no direct access to GitHub, so the repo is cloned on
# fujian ($CLONE_DIR) and shipped over the rshim link (192.168.100.2).
# Adjust CLONE_DIR/DEV_DIR if your paths differ.
set -e

CLONE_DIR=/tmp/bf2k
DEV_HOST=root@192.168.100.2
DEV_DIR=/root/bf2k
TARBALL=/tmp/bf2deploy.tar.gz

REBUILD=0
[ "$1" = "-b" ] && REBUILD=1

cd "$CLONE_DIR"
git pull

# Ship ONLY git-tracked files. This is what keeps the device safe: local
# build artifacts (e.g. an x86_64 code/collect_all from the Windows/WSL
# side) and pulled-back results are never tracked, so they can never be
# packed and overwrite the device's native aarch64 binaries.
git ls-files -z | tar --null -T - -czf "$TARBALL"

scp "$TARBALL" "$DEV_HOST:$DEV_DIR/"
if [ "$REBUILD" = 1 ]; then
    ssh "$DEV_HOST" "cd $DEV_DIR && tar xzf bf2deploy.tar.gz && rm bf2deploy.tar.gz && make"
else
    ssh "$DEV_HOST" "cd $DEV_DIR && tar xzf bf2deploy.tar.gz && rm bf2deploy.tar.gz"
fi
echo "deploy done -> $DEV_HOST:$DEV_DIR"
