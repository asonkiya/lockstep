#!/bin/bash
# Exhaustive bounded verification gate: prove hweight8/16 equal to the kernel C
# over their ENTIRE input domain, one boot. Uses the already-synthesized
# candidates from the wide run.
set -euo pipefail
G="$(cd "$(dirname "$0")" && pwd)"; OUT="$G/out"; VOL=cgir-kbuild; IMG=cgir-kernel-gate; GATE=crypto/lockstep_gate
CAND="$(cd "$G/../widerun/cand" && pwd)"
mkdir -p "$OUT"
echo "[exhaustive] installing hweight8/16 candidates + probe..."
docker run --rm -v "$VOL":/build -v "$G/probe":/p:ro -v "$CAND":/c:ro "$IMG" bash -euc '
  cd /build/linux; mkdir -p '"$GATE"'
  grep -q "obj-y += lockstep_gate/" crypto/Makefile || echo "obj-y += lockstep_gate/" >> crypto/Makefile
  cd '"$GATE"'; rm -f *.c *.o *.o_shipped; cp /p/exhaustive_probe.c .
  i=0
  for f in __sw_hweight8 __sw_hweight16; do
    loc=""; [ $i -gt 0 ] && loc="&& aarch64-linux-gnu-objcopy --wildcard --localize-symbol *rust_begin_unwind* ${f}_c.o_shipped"
    rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort -C relocation-model=static -O /c/${f}.rs -o ${f}_c.o_shipped
    [ $i -gt 0 ] && aarch64-linux-gnu-objcopy --wildcard --localize-symbol "*rust_begin_unwind*" ${f}_c.o_shipped
    i=$((i+1))
  done
  printf "obj-y := exhaustive_probe.o __sw_hweight8_c.o __sw_hweight16_c.o\n" > Kbuild
'
echo "[exhaustive] building..."
docker run --rm -v "$VOL":/build "$IMG" bash -eo pipefail -uc '
  cd /build/linux; rm -f arch/arm64/boot/Image; make -s -j$(nproc) Image 2>&1 | tail -3; test -f arch/arm64/boot/Image
' > "$OUT/build.txt" 2>&1 || { echo BUILD FAILED; tail -8 "$OUT/build.txt"; exit 2; }
echo "[exhaustive] booting + proving over full domain..."
docker run --rm -v "$VOL":/build "$IMG" bash -c '
  cd /build/linux; timeout 300 qemu-system-aarch64 -M virt -cpu max -smp 2 -m 1024 -nographic -net none \
    -kernel arch/arm64/boot/Image -append "console=ttyAMA0 panic=-1 kunit.filter_glob=zz-none*" -no-reboot 2>&1 || true
' > "$OUT/console.txt"
grep -E "EXHAUSTIVE:" "$OUT/console.txt" || echo "  (no EXHAUSTIVE output)"
p=$(grep -c "verdict=PROVEN" "$OUT/console.txt" || true)
c=$(grep -c "verdict=COUNTEREXAMPLE" "$OUT/console.txt" || true)
echo "[exhaustive] PROVEN $p, counterexamples $c"
[ "$p" -eq 2 ] && [ "$c" -eq 0 ] && echo "EXHAUSTIVE GATE: PASS (2 fns proven over full domain)" || echo "EXHAUSTIVE GATE: FAIL"
[ "$p" -eq 2 ] && [ "$c" -eq 0 ]
