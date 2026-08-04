#!/usr/bin/env bash
# boot_hvf.sh — the HVF boot leg: build inside the kbuild container (unchanged),
# boot NATIVELY on the macOS host under Hypervisor.framework. Kills the TCG
# double-emulation penalty (measured 2026-08-04: 216.3s TCG-in-Docker -> 5.31s
# HVF, same Image booted to panic — 41x on the boot leg; the ccache'd
# incremental build is now the bottleneck).
#
# Usage:
#   boot_hvf.sh [--build] [--vol VOLUME] [--append 'extra args'] [--timeout S]
#   exit 0 iff the console shows a clean boot-to-panic (panic=-1, no rootfs —
#   the probe pattern all gates use); console saved next to the Image.
#
# Caveats (sourced in COSTDOWN §2): -cpu host is required under hvf; gdbstub
# breakpoints need TCG (debug with the old path); -nographic sidesteps the one
# open hvf display issue.
set -euo pipefail

VOL=cgir-kbuild
IMG_DOCKER=cgir-kernel-gate
BUILD=0
APPEND=""
TIMEOUT=120
while [ $# -gt 0 ]; do case "$1" in
    --build) BUILD=1;;
    --vol) VOL=$2; shift;;
    --append) APPEND=$2; shift;;
    --timeout) TIMEOUT=$2; shift;;
    *) echo "unknown arg $1" >&2; exit 2;;
esac; shift; done

HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$HERE/out; mkdir -p "$OUT"

if [ "$BUILD" = 1 ]; then
    echo "== incremental build in $VOL =="
    docker run --rm -v "$VOL:/build" "$IMG_DOCKER" sh -c \
        'cd /build/linux && rm -f arch/arm64/boot/Image && make -s -j$(nproc) Image 2>&1 | tail -2 && test -f arch/arm64/boot/Image'
fi

echo "== extract Image from $VOL =="
docker run --rm -v "$VOL:/build" alpine cat /build/linux/arch/arm64/boot/Image > "$OUT/Image.$VOL"

tmo() {  # portable timeout: gtimeout (brew coreutils) / timeout / watchdog
    if command -v gtimeout >/dev/null; then gtimeout "$@"
    elif command -v timeout >/dev/null; then timeout "$@"
    else
        local secs=$1; shift
        "$@" & local pid=$!
        ( sleep "$secs"; kill "$pid" 2>/dev/null ) & local wd=$!
        wait "$pid" 2>/dev/null; local rc=$?
        kill "$wd" 2>/dev/null; return $rc
    fi
}

CONSOLE=$OUT/console.$VOL.txt
echo "== boot (hvf) =="
SECONDS=0
tmo "$TIMEOUT" qemu-system-aarch64 -accel hvf -cpu host -M virt -smp 2 -m 1024 \
    -nographic -net none -kernel "$OUT/Image.$VOL" \
    -append "console=ttyAMA0 panic=-1 kunit.filter_glob=zz-none* $APPEND" \
    -no-reboot > "$CONSOLE" 2>&1 || true
T=$SECONDS

if grep -q "Linux version" "$CONSOLE" && grep -q "Kernel panic" "$CONSOLE"; then
    echo "BOOT OK (${T}s hvf) — console: $CONSOLE"
    exit 0
fi
echo "BOOT FAILED (${T}s) — tail of console:"; tail -5 "$CONSOLE"
exit 1
