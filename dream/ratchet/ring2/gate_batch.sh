#!/bin/bash
# Ring 2 batch gate — verify all four leaf candidates in ONE boot.
# Compiles the 4 Rust candidates (panic handlers localized; the woven tree's
# ptp object already owns the global one), links them with the batch probe, and
# boots once. The probe compares each cgir_* against the kernel's live exported
# symbol over wide input ranges. PASS iff every leaf reports DIFF_PASS.
set -euo pipefail
G="$(cd "$(dirname "$0")" && pwd)"
OUT="$(cd "$G/.." && pwd)/out"; VOL=cgir-kbuild; IMG=cgir-kernel-gate; GATE=crypto/lockstep_gate
mkdir -p "$OUT"

LEAVES="int_pow:int_pow hweight32:hweight32 hweight64:hweight64 gcd:gcd"

echo "[ring2] installing 4 candidates + batch probe..."
docker run --rm -v "$VOL":/build -v "$G":/g:ro -v "$G/probe":/p:ro "$IMG" bash -euc '
  cd /build/linux/crypto/lockstep_gate 2>/dev/null || { mkdir -p /build/linux/crypto/lockstep_gate && cd /build/linux/crypto/lockstep_gate; }
  grep -q "obj-y += lockstep_gate/" ../Makefile || echo "obj-y += lockstep_gate/" >> ../Makefile
  rm -f *.c *.o *.o_shipped
  cp /p/batch_probe.c .
  objs="batch_probe.o"
  for f in int_pow hweight32 hweight64 gcd; do
    rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort \
      -C relocation-model=static -O /g/$f.rs -o ${f}_cand.o_shipped
    test -s ${f}_cand.o_shipped
    # localize panic handler (woven ptp object owns the global one)
    aarch64-linux-gnu-objcopy --wildcard --localize-symbol "*rust_begin_unwind*" ${f}_cand.o_shipped
    objs="$objs ${f}_cand.o"
  done
  printf "obj-y := %s\n" "$objs" > Kbuild
'
echo "[ring2] building..."
docker run --rm -v "$VOL":/build "$IMG" bash -eo pipefail -uc '
  cd /build/linux; rm -f arch/arm64/boot/Image
  make -s -j$(nproc) Image 2>&1 | tail -3; test -f arch/arm64/boot/Image
' > "$OUT/ring2-build.txt" 2>&1 || { echo "[ring2] BUILD FAILED"; tail -10 "$OUT/ring2-build.txt"; exit 2; }
echo "[ring2] booting + batch-verifying..."
docker run --rm -v "$VOL":/build "$IMG" bash -c "
  timeout 300 qemu-system-aarch64 -M virt -cpu max -smp 2 -m 1024 -nographic -net none \
    -kernel /build/linux/arch/arm64/boot/Image \
    -append 'console=ttyAMA0 panic=-1 kunit.filter_glob=zz-none*' -no-reboot 2>&1 || true
" > "$OUT/ring2-console.txt"

grep -E "BATCH_PROBE:" "$OUT/ring2-console.txt" || echo "  (no BATCH_PROBE)"
pass=$(grep -c "verdict=DIFF_PASS" "$OUT/ring2-console.txt" || true)
fail=$(grep -c "verdict=DIFF_FAIL" "$OUT/ring2-console.txt" || true)
echo "[ring2] leaves passing: $pass / 4  (failing: $fail)"
: > "$OUT/ring2-verdicts.txt"
for f in int_pow hweight32 hweight64 gcd; do
  v=$(grep "BATCH_PROBE: $f " "$OUT/ring2-console.txt" | grep -oE "verdict=DIFF_(PASS|FAIL)" | tail -1 || true)
  echo "$f $v" >> "$OUT/ring2-verdicts.txt"
done
cat "$OUT/ring2-verdicts.txt"
[ "$pass" -eq 4 ] && [ "$fail" -eq 0 ] && { echo "RING 2 BATCH GATE: PASS"; exit 0; } || { echo "RING 2 BATCH GATE: FAIL"; exit 1; }
