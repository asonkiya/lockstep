#!/bin/bash
# Ring 0.1 — differentially verify the WOVEN driver's Rust regions IN-SITU.
#
# Ring 0 proved the woven ptp_mock boots; but with no consumer, its regions
# don't execute during boot. This closes that gap: it adds the differential
# probe + the C reference to the already-woven kernel. The probe's
# lockstep_phc_* calls now resolve to the DRIVER's Rust object
# (drivers/ptp/ptp_mock_regions.o, linked via the real driver path), so we are
# verifying the exact regions that ship in the woven vmlinux — not a copy — bit
# for bit against the C original, inside the booting kernel.
#
# Assumes `weave.py apply` (or gate) has already run (tree is woven).
set -euo pipefail
G="$(cd "$(dirname "$0")" && pwd)"
DIFF="$(cd "$G/../diffgate" && pwd)"
OUT="$G/out"; VOL=cgir-kbuild; IMG=cgir-kernel-gate; GATE=crypto/lockstep_gate
mkdir -p "$OUT"

echo "[ring0.1] installing differential probe + C reference against the woven driver..."
docker run --rm -v "$VOL":/build -v "$DIFF/probe":/p:ro "$IMG" bash -euc "
  cd /build/linux/$GATE 2>/dev/null || { mkdir -p /build/linux/$GATE && cd /build/linux/$GATE; }
  grep -q 'obj-y += lockstep_gate/' ../Makefile || echo 'obj-y += lockstep_gate/' >> ../Makefile
  # probe + C reference only; the CANDIDATE is the driver's own ptp_mock_regions.o
  rm -f *.o *.o_shipped cand_rust.*
  cp /p/diff_probe.c /p/diff_ref.c .
  printf 'obj-y := diff_probe.o diff_ref.o\n' > Kbuild
"

echo "[ring0.1] building..."
docker run --rm -v "$VOL":/build "$IMG" bash -eo pipefail -uc '
  cd /build/linux; rm -f arch/arm64/boot/Image
  make -s -j$(nproc) Image 2>&1 | tail -3; test -f arch/arm64/boot/Image
' > "$OUT/ring0verify-build.txt" 2>&1 || { echo "[ring0.1] BUILD FAILED"; tail -8 "$OUT/ring0verify-build.txt"; exit 2; }

echo "[ring0.1] booting + differentially verifying the woven regions..."
docker run --rm -v "$VOL":/build "$IMG" bash -c "
  timeout 300 qemu-system-aarch64 -M virt -cpu max -smp 2 -m 1024 -nographic -net none \
    -kernel /build/linux/arch/arm64/boot/Image \
    -append 'console=ttyAMA0 panic=-1 kunit.filter_glob=zz-none*' -no-reboot 2>&1 || true
" > "$OUT/ring0verify-console.txt"

grep -E "DIFF_PROBE:" "$OUT/ring0verify-console.txt" || echo "  (no DIFF_PROBE — probe didn't run)"
V=$(grep -oE "verdict=DIFF_(PASS|FAIL)" "$OUT/ring0verify-console.txt" | tail -1 || true)
echo "[ring0.1] woven-driver regions verdict: $V"
if [ "$V" = "verdict=DIFF_PASS" ]; then
  echo "RING 0.1: PASS — the woven driver's Rust regions are bit-identical to C, in-kernel"
  exit 0
else
  echo "RING 0.1: FAIL"; exit 1
fi
