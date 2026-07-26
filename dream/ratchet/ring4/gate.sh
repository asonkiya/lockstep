#!/bin/bash
# Ring 4 — real-driver (gpio-zevio) recorded-MMIO differential gate.
# Drives a 32-pin op script (dir_out, set, get, dir_in, get) through the C
# reference and the Rust transplant against a software register block, comparing
# the full register-access trace.
#   correct -> DIFF_PASS
#   wrong   -> DIFF_FAIL: a classic "wrong register" bug (writes OUTPUT ops to the
#              INPUT offset). Non-crashing; caught on the trace.
# Usage: gate.sh <correct|wrong>
set -euo pipefail
G="$(cd "$(dirname "$0")" && pwd)"
OUT="$(cd "$G/.." && pwd)/out"; VOL=cgir-kbuild; IMG=cgir-kernel-gate; GATE=crypto/lockstep_gate
mkdir -p "$OUT"
mode="${1:-correct}"

CAND="$G/zevio.rs"
if [ "$mode" = wrong ]; then
  # wrong register: OUTPUT ops land on the INPUT offset (0x14 -> 0x18)
  sed 's/ZEVIO_GPIO_OUTPUT: u32 = 0x14;/ZEVIO_GPIO_OUTPUT: u32 = 0x18; \/\/ [negative control: wrong register]/' \
    "$G/zevio.rs" > "$OUT/zevio_wrong.rs"
  grep -q "0x18; //" "$OUT/zevio_wrong.rs" || { echo "mutation failed"; exit 3; }
  CAND="$OUT/zevio_wrong.rs"
fi

echo "[ring4:$mode] installing real-driver ref + Rust transplant + probe..."
docker run --rm -v "$VOL":/build -v "$G":/g:ro -v "$G/probe":/p:ro -v "$(dirname "$CAND")":/c:ro "$IMG" bash -euc "
  cd /build/linux; mkdir -p $GATE
  grep -q 'obj-y += lockstep_gate/' crypto/Makefile || echo 'obj-y += lockstep_gate/' >> crypto/Makefile
  cd $GATE; rm -f *.c *.h *.o *.o_shipped
  cp /p/zevio_probe.c /g/zevio_ref.c /g/zevio.h .
  rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort \
    -C relocation-model=static -O /c/$(basename "$CAND") -o zevio_cand.o_shipped
  test -s zevio_cand.o_shipped
  aarch64-linux-gnu-objcopy --wildcard --localize-symbol '*rust_begin_unwind*' zevio_cand.o_shipped
  printf 'obj-y := zevio_probe.o zevio_ref.o zevio_cand.o\n' > Kbuild
"
echo "[ring4:$mode] building..."
docker run --rm -v "$VOL":/build "$IMG" bash -eo pipefail -uc '
  cd /build/linux; rm -f arch/arm64/boot/Image
  make -s -j$(nproc) Image 2>&1 | tail -3; test -f arch/arm64/boot/Image
' > "$OUT/ring4-$mode-build.txt" 2>&1 || { echo "BUILD FAILED"; tail -12 "$OUT/ring4-$mode-build.txt"; exit 2; }
echo "[ring4:$mode] booting + trace-verifying..."
docker run --rm -v "$VOL":/build "$IMG" bash -c "
  timeout 300 qemu-system-aarch64 -M virt -cpu max -smp 2 -m 1024 -nographic -net none \
    -kernel /build/linux/arch/arm64/boot/Image \
    -append 'console=ttyAMA0 panic=-1 kunit.filter_glob=zz-none*' -no-reboot 2>&1 || true
" > "$OUT/ring4-$mode-console.txt"
grep -E "ZEVIO_PROBE:" "$OUT/ring4-$mode-console.txt" || echo "  (no ZEVIO_PROBE)"
V=$(grep -oE "verdict=DIFF_(PASS|FAIL)" "$OUT/ring4-$mode-console.txt" | tail -1 || true)
echo "[ring4:$mode] verdict: $V"
if { [ "$mode" = correct ] && [ "$V" = "verdict=DIFF_PASS" ]; } || { [ "$mode" = wrong ] && [ "$V" = "verdict=DIFF_FAIL" ]; }; then
  echo "RING 4 GATE ($mode): PASS"; exit 0
else echo "RING 4 GATE ($mode): FAIL"; exit 1; fi
