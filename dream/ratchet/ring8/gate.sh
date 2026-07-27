#!/bin/bash
# Ring 8 depth gate — three legs proving struct-mirror transplant + its two guards.
#   correct     : the Tier-B table-walk transplant vs the C original -> DIFF_PASS
#   mirror-size : a size-wrong ClkDivTable mirror must FAIL TO COMPILE (the
#                 compile-time layout guard = BUILD_BUG_ON) — no boot needed
#   mirror-swap : a field-swapped mirror (SAME size, passes the guard) must be
#                 caught at runtime by the differential -> DIFF_FAIL
# Together: the guard catches size/offset drift before build; the differential
# catches same-size field confusion. Struct mirroring needs both.
#
# Usage: gate.sh <correct|mirror-size|mirror-swap>
set -euo pipefail
G="$(cd "$(dirname "$0")" && pwd)"
OUT="$(cd "$G/.." && pwd)/out"; VOL=cgir-kbuild; IMG=cgir-kernel-gate; GATE=crypto/lockstep_gate
mkdir -p "$OUT"
mode="${1:-correct}"

CAND="$G/clkdiv.rs"
if [ "$mode" = mirror-size ]; then
  # add a field -> size 12 -> the const-assert size==8 fails at compile
  sed 's/pub div: u32,/pub div: u32,\n    pub bogus: u32, \/\/ [neg: size-wrong mirror]/' "$G/clkdiv.rs" > "$OUT/clkdiv_size.rs"
  echo "[ring8:mirror-size] compiling a size-wrong mirror (expect COMPILE FAILURE)..."
  r=$(docker run --rm -v "$OUT":/w cgir-kernel-gate bash -c \
    "cd /w && rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort -C relocation-model=static -O clkdiv_size.rs -o /tmp/x.o 2>&1" || true)
  if echo "$r" | grep -qiE "evaluation of .*panicked|assert|size_of"; then
    echo "  ✓ layout guard fired at compile time:"; echo "$r" | grep -iE "assert|panicked|size_of" | head -2
    echo "RING 8 GATE (mirror-size): PASS"; exit 0
  else
    echo "  ✗ size-wrong mirror compiled (guard did not fire)"; echo "$r" | tail -3
    echo "RING 8 GATE (mirror-size): FAIL"; exit 1
  fi
fi
if [ "$mode" = mirror-swap ]; then
  # swap the two fields (same size -> guard passes; fields now misread)
  python3 - "$G/clkdiv.rs" "$OUT/clkdiv_swap.rs" <<'PY'
import sys
s = open(sys.argv[1]).read()
s = s.replace("pub struct ClkDivTable {\n    pub val: u32,\n    pub div: u32,\n}",
              "pub struct ClkDivTable {\n    pub div: u32,\n    pub val: u32,  // [neg: fields swapped]\n}")
assert "// [neg: fields swapped]" in s, "swap site not found"
open(sys.argv[2], "w").write(s)
PY
  CAND="$OUT/clkdiv_swap.rs"
fi

echo "[ring8:$mode] installing ksdk+transplant + C ref + probe..."
docker run --rm -v "$VOL":/build -v "$G":/g:ro -v "$G/probe":/p:ro -v "$(dirname "$CAND")":/c:ro "$IMG" bash -euc "
  cd /build/linux; mkdir -p $GATE
  grep -q 'obj-y += lockstep_gate/' crypto/Makefile || echo 'obj-y += lockstep_gate/' >> crypto/Makefile
  cd $GATE; rm -f *.c *.o *.o_shipped
  cp /p/clkdiv_probe.c /g/clkdiv_ref.c .
  rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort \
    -C relocation-model=static -O /c/$(basename "$CAND") -o clkdiv_cand.o_shipped
  test -s clkdiv_cand.o_shipped
  aarch64-linux-gnu-objcopy --wildcard --localize-symbol '*rust_begin_unwind*' clkdiv_cand.o_shipped
  printf 'obj-y := clkdiv_probe.o clkdiv_ref.o clkdiv_cand.o\n' > Kbuild
"
echo "[ring8:$mode] building..."
docker run --rm -v "$VOL":/build "$IMG" bash -eo pipefail -uc '
  cd /build/linux; rm -f arch/arm64/boot/Image
  make -s -j$(nproc) Image 2>&1 | tail -3; test -f arch/arm64/boot/Image
' > "$OUT/ring8-$mode-build.txt" 2>&1 || { echo "BUILD FAILED"; tail -8 "$OUT/ring8-$mode-build.txt"; exit 2; }
echo "[ring8:$mode] booting + verifying..."
docker run --rm -v "$VOL":/build "$IMG" bash -c "
  timeout 300 qemu-system-aarch64 -M virt -cpu max -smp 2 -m 1024 -nographic -net none \
    -kernel /build/linux/arch/arm64/boot/Image \
    -append 'console=ttyAMA0 panic=-1 kunit.filter_glob=zz-none*' -no-reboot 2>&1 || true
" > "$OUT/ring8-$mode-console.txt"
grep -E "CLKDIV_PROBE:" "$OUT/ring8-$mode-console.txt" || echo "  (no CLKDIV_PROBE)"
V=$(grep -oE "verdict=DIFF_(PASS|FAIL)" "$OUT/ring8-$mode-console.txt" | tail -1 || true)
echo "[ring8:$mode] verdict: $V"
if { [ "$mode" = correct ] && [ "$V" = "verdict=DIFF_PASS" ]; } || { [ "$mode" = mirror-swap ] && [ "$V" = "verdict=DIFF_FAIL" ]; }; then
  echo "RING 8 GATE ($mode): PASS"; exit 0
else echo "RING 8 GATE ($mode): FAIL"; exit 1; fi
