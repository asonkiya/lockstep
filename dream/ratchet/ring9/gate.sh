#!/bin/bash
# Ring 9 — real subsystem sweep: verify the clk-divider math family (6 fns, one
# Rust object against ksdk) against the C originals, one boot, per-function.
set -euo pipefail
G="$(cd "$(dirname "$0")" && pwd)"
OUT="$(cd "$G/.." && pwd)/out"; VOL=cgir-kbuild; IMG=cgir-kernel-gate; GATE=crypto/lockstep_gate
mkdir -p "$OUT"

echo "[ring9] installing clk-divider family (6 fns) + C ref + probe..."
docker run --rm -v "$VOL":/build -v "$G":/g:ro -v "$G/probe":/p:ro "$IMG" bash -euc "
  cd /build/linux; mkdir -p $GATE
  grep -q 'obj-y += lockstep_gate/' crypto/Makefile || echo 'obj-y += lockstep_gate/' >> crypto/Makefile
  cd $GATE; rm -f *.c *.o *.o_shipped
  cp /p/clkfam_probe.c /g/clkfam_ref.c .
  rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort \
    -C relocation-model=static -O /g/clkfam.rs -o clkfam_cand.o_shipped
  test -s clkfam_cand.o_shipped
  aarch64-linux-gnu-objcopy --wildcard --localize-symbol '*rust_begin_unwind*' clkfam_cand.o_shipped
  printf 'obj-y := clkfam_probe.o clkfam_ref.o clkfam_cand.o\n' > Kbuild
"
echo "[ring9] building..."
docker run --rm -v "$VOL":/build "$IMG" bash -eo pipefail -uc '
  cd /build/linux; rm -f arch/arm64/boot/Image
  make -s -j$(nproc) Image 2>&1 | tail -3; test -f arch/arm64/boot/Image
' > "$OUT/ring9-build.txt" 2>&1 || { echo "BUILD FAILED"; tail -10 "$OUT/ring9-build.txt"; exit 2; }
echo "[ring9] booting + sweep-verifying..."
docker run --rm -v "$VOL":/build "$IMG" bash -c "
  timeout 300 qemu-system-aarch64 -M virt -cpu max -smp 2 -m 1024 -nographic -net none \
    -kernel /build/linux/arch/arm64/boot/Image \
    -append 'console=ttyAMA0 panic=-1 kunit.filter_glob=zz-none*' -no-reboot 2>&1 || true
" > "$OUT/ring9-console.txt"
grep -E "CLKFAM:" "$OUT/ring9-console.txt" || echo "  (no CLKFAM)"
npass=$(grep -c "CLKFAM: get.* verdict=DIFF_PASS" "$OUT/ring9-console.txt" || true)
total=$(grep -oE "CLKFAM: total bad=[0-9]+ verdict=DIFF_(PASS|FAIL)" "$OUT/ring9-console.txt" | tail -1 || true)
echo "[ring9] per-fn passing: $npass/6 | $total"
[ "$npass" -eq 6 ] && echo "RING 9 GATE: PASS" || echo "RING 9 GATE: FAIL"
[ "$npass" -eq 6 ]
