#!/bin/bash
# Concurrency gate — the differentiated, in-kernel race oracle (the prior-art
# whitespace). Two Rust legs under KCSAN + a coupled-invariant reader:
#   correct : both fields updated inside the guard -> KCSAN clean, invariant holds
#   subtle  : `mirror` escapes the lock (narrowed critical section — the REALISTIC
#             transplant bug, not a total drop) -> KCSAN fires on the reader's
#             mirror read AND the mirror==count invariant breaks. Both oracles.
# Usage: gate.sh <correct|subtle>
set -euo pipefail
G="$(cd "$(dirname "$0")" && pwd)"; OUT="$G/out"; VOL=cgir-kbuild; IMG=cgir-kernel-gate; GATE=crypto/lockstep_gate
mkdir -p "$OUT"; mode="${1:-correct}"

CAND="$G/acct.rs"
if [ "$mode" = subtle ]; then
  python3 - "$G/acct.rs" "$OUT/acct_subtle.rs" <<'PY'
import sys, re
s = open(sys.argv[1]).read()
# narrow the critical section: count under the guard, mirror AFTER it (escapes)
old = ("    let _g = Guard::new(lock);\n    unsafe {\n        (*f).count += delta;\n"
       "        (*f).mirror += delta;\n    }")
new = ("    {\n        let _g = Guard::new(lock);\n        unsafe { (*f).count += delta; }\n"
       "    } // guard dropped here\n    unsafe { (*f).mirror += delta; } // [subtle: mirror escapes the lock]")
assert old in s, "critical-section block not found"
open(sys.argv[2], "w").write(s.replace(old, new, 1))
PY
  CAND="$OUT/acct_subtle.rs"
fi

echo "[acct:$mode] installing probe(+Rust) + stock ref..."
docker run --rm -v "$VOL":/build -v "$G":/g:ro -v "$G/probe":/p:ro -v "$(dirname "$CAND")":/c:ro "$IMG" bash -euc "
  cd /build/linux; mkdir -p $GATE
  grep -q 'obj-y += lockstep_gate/' crypto/Makefile || echo 'obj-y += lockstep_gate/' >> crypto/Makefile
  cd $GATE; rm -f *.c *.h *.o *.o_shipped
  cp /p/acct_probe.c /g/acct_ref.c /g/acct.h .
  rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort \
    -C relocation-model=static -O /c/$(basename "$CAND") -o acct_cand.o_shipped
  test -s acct_cand.o_shipped
  aarch64-linux-gnu-objcopy --wildcard --localize-symbol '*rust_begin_unwind*' acct_cand.o_shipped
  printf 'ccflags-y += -DACCT_USE_RUST\nobj-y := acct_probe.o acct_ref.o acct_cand.o\n' > Kbuild
"
echo "[acct:$mode] building (KCSAN config)..."
docker run --rm -v "$VOL":/build "$IMG" bash -eo pipefail -uc '
  cd /build/linux; rm -f arch/arm64/boot/Image; make -s -j$(nproc) Image 2>&1 | tail -3; test -f arch/arm64/boot/Image
' > "$OUT/$mode-build.txt" 2>&1 || { echo BUILD FAILED; tail -8 "$OUT/$mode-build.txt"; exit 2; }
echo "[acct:$mode] booting SMP under KCSAN..."
docker run --rm -v "$VOL":/build "$IMG" bash -c "
  timeout 600 qemu-system-aarch64 -M virt -cpu max -smp 4 -m 2048 -nographic -net none \
    -kernel /build/linux/arch/arm64/boot/Image \
    -append 'console=ttyAMA0 panic=-1 kcsan.early_enable=1 kunit.filter_glob=zz-none*' -no-reboot 2>&1 || true
" > "$OUT/$mode-console.txt"
grep -E "ACCT_PROBE:" "$OUT/$mode-console.txt" || echo "  (no ACCT_PROBE)"
func=$(grep -oE "verdict=FUNC_(PASS|FAIL)" "$OUT/$mode-console.txt" | tail -1 || true)
kc=$(grep -A14 "BUG: KCSAN" "$OUT/$mode-console.txt" | grep -c "acct_" || true)
echo "[acct:$mode] functional=$func  probe-KCSAN=$kc"
if [ "$mode" = correct ]; then
  { [ "$func" = "verdict=FUNC_PASS" ] && [ "$kc" -eq 0 ]; } && echo "CONC GATE (correct): PASS (clean + invariant holds)" || { echo "CONC GATE (correct): FAIL"; exit 1; }
else
  { [ "$func" = "verdict=FUNC_FAIL" ] && [ "$kc" -gt 0 ]; } && echo "CONC GATE (subtle): PASS (KCSAN caught the escaped field + invariant broke)" || { echo "CONC GATE (subtle): FAIL"; exit 1; }
fi
