#!/bin/bash
# Ring 1 leaf gate — differentially verify the int_sqrt transplant in-kernel.
# Links the Rust candidate (cgir_int_sqrt) + the C original (int_sqrt_ref) + the
# probe; boots; the probe drives ~20k dense + boundary + large inputs through
# both and asserts bit-identical. correct -> DIFF_PASS; wrong (a +1 drift) ->
# DIFF_FAIL (the same rejection property proven for ptp; run with `wrong`).
#
# Usage: gate.sh <correct|wrong>
set -euo pipefail
G="$(cd "$(dirname "$0")" && pwd)"
OUT="$(cd "$G/.." && pwd)/out"; VOL=cgir-kbuild; IMG=cgir-kernel-gate; GATE=crypto/lockstep_gate
mkdir -p "$OUT"
mode="${1:-correct}"

CAND="$G/int_sqrt.rs"
if [ "$mode" = wrong ]; then
  # behavioral bug: correct result + 1 (non-crashing, non-racy, wrong)
  sed 's/^\ty\n}/\ty.wrapping_add(1)\n}/' "$G/int_sqrt.rs" > "$OUT/int_sqrt_wrong.rs" || true
  # sed can't match across the trailing newline reliably; do it in python
  python3 - "$G/int_sqrt.rs" "$OUT/int_sqrt_wrong.rs" <<'PY'
import sys, re
s = open(sys.argv[1]).read()
s = re.sub(r"\n\ty\n\}", "\n\ty.wrapping_add(1)\n}", s, count=1)
open(sys.argv[2], "w").write(s)
PY
  CAND="$OUT/int_sqrt_wrong.rs"
fi

echo "[isqrt:$mode] installing candidate + C reference + probe..."
docker run --rm -v "$VOL":/build -v "$G":/g:ro -v "$G/probe":/p:ro -v "$(dirname "$CAND")":/c:ro "$IMG" bash -euc "
  cd /build/linux
  mkdir -p $GATE
  grep -q 'obj-y += lockstep_gate/' crypto/Makefile || echo 'obj-y += lockstep_gate/' >> crypto/Makefile
  cd $GATE
  rm -f *.c *.o *.o_shipped
  cp /p/isqrt_probe.c /g/int_sqrt_ref.c .
  rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort \
    -C relocation-model=static -O /c/$(basename "$CAND") -o int_sqrt_cand.o_shipped
  test -s int_sqrt_cand.o_shipped
  # localize this object's panic handler so it does not collide with the
  # already-woven Rust object's rust_begin_unwind (the linking-research finding).
  aarch64-linux-gnu-objcopy --wildcard --localize-symbol '*rust_begin_unwind*' int_sqrt_cand.o_shipped
  printf 'obj-y := isqrt_probe.o int_sqrt_ref.o int_sqrt_cand.o\n' > Kbuild
"
echo "[isqrt:$mode] building..."
docker run --rm -v "$VOL":/build "$IMG" bash -eo pipefail -uc '
  cd /build/linux; rm -f arch/arm64/boot/Image
  make -s -j$(nproc) Image 2>&1 | tail -3; test -f arch/arm64/boot/Image
' > "$OUT/isqrt-$mode-build.txt" 2>&1 || { echo "BUILD FAILED"; tail -8 "$OUT/isqrt-$mode-build.txt"; exit 2; }
echo "[isqrt:$mode] booting + verifying..."
docker run --rm -v "$VOL":/build "$IMG" bash -c "
  timeout 300 qemu-system-aarch64 -M virt -cpu max -smp 2 -m 1024 -nographic -net none \
    -kernel /build/linux/arch/arm64/boot/Image \
    -append 'console=ttyAMA0 panic=-1 kunit.filter_glob=zz-none*' -no-reboot 2>&1 || true
" > "$OUT/isqrt-$mode-console.txt"
grep -E "ISQRT_PROBE:" "$OUT/isqrt-$mode-console.txt" || echo "  (no ISQRT_PROBE)"
V=$(grep -oE "verdict=DIFF_(PASS|FAIL)" "$OUT/isqrt-$mode-console.txt" | tail -1 || true)
echo "[isqrt:$mode] verdict: $V"
if { [ "$mode" = correct ] && [ "$V" = "verdict=DIFF_PASS" ]; } || { [ "$mode" = wrong ] && [ "$V" = "verdict=DIFF_FAIL" ]; }; then
  echo "ISQRT GATE ($mode): PASS"; exit 0
else echo "ISQRT GATE ($mode): FAIL"; exit 1; fi
