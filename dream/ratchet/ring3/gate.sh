#!/bin/bash
# Ring 3 — recorded-I/O differential gate.
# Links the C reference driver + the Rust candidate + the register-model probe;
# boots; the probe drives 256 transfers through each and compares the full
# register-access traces.
#   correct  -> DIFF_PASS (identical register program)
#   wrong    -> DIFF_FAIL: the "skip the status poll" variant. In this model the
#              result is set synchronously, so it returns the SAME value — a
#              value-only check would pass it — but the trace oracle catches the
#              missing STATUS reads. Strictly stronger than comparing returns.
#
# Usage: gate.sh <correct|wrong>
set -euo pipefail
G="$(cd "$(dirname "$0")" && pwd)"
OUT="$(cd "$G/.." && pwd)/out"; VOL=cgir-kbuild; IMG=cgir-kernel-gate; GATE=crypto/lockstep_gate
mkdir -p "$OUT"
mode="${1:-correct}"

CAND="$G/mockdev.rs"
if [ "$mode" = wrong ]; then
  python3 - "$G/mockdev.rs" "$OUT/mockdev_wrong.rs" <<'PY'
import sys, re
s = open(sys.argv[1]).read()
# remove the status poll entirely (value stays identical in this model; the
# register trace loses its STATUS reads -> a real bug the trace oracle catches)
s2 = re.sub(r"\n\s*while reg_read\(m, REG_STATUS\)[^\n]*\{\}\n",
            "\n        // [negative control: status poll dropped]\n", s, count=1)
assert s2 != s, "poll line not found"
open(sys.argv[2], "w").write(s2)
PY
  CAND="$OUT/mockdev_wrong.rs"
fi

echo "[ring3:$mode] installing ref + candidate + register-model probe..."
docker run --rm -v "$VOL":/build -v "$G":/g:ro -v "$G/probe":/p:ro -v "$(dirname "$CAND")":/c:ro "$IMG" bash -euc "
  cd /build/linux; mkdir -p $GATE
  grep -q 'obj-y += lockstep_gate/' crypto/Makefile || echo 'obj-y += lockstep_gate/' >> crypto/Makefile
  cd $GATE; rm -f *.c *.h *.o *.o_shipped
  cp /p/mockdev_probe.c /g/mockdev_ref.c /g/mockdev.h .
  rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort \
    -C relocation-model=static -O /c/$(basename "$CAND") -o mockdev_cand.o_shipped
  test -s mockdev_cand.o_shipped
  aarch64-linux-gnu-objcopy --wildcard --localize-symbol '*rust_begin_unwind*' mockdev_cand.o_shipped
  printf 'obj-y := mockdev_probe.o mockdev_ref.o mockdev_cand.o\nCFLAGS_mockdev_probe.o := -I\$(src)\n' > Kbuild
"
echo "[ring3:$mode] building..."
docker run --rm -v "$VOL":/build "$IMG" bash -eo pipefail -uc '
  cd /build/linux; rm -f arch/arm64/boot/Image
  make -s -j$(nproc) Image 2>&1 | tail -3; test -f arch/arm64/boot/Image
' > "$OUT/ring3-$mode-build.txt" 2>&1 || { echo "BUILD FAILED"; tail -12 "$OUT/ring3-$mode-build.txt"; exit 2; }
echo "[ring3:$mode] booting + trace-verifying..."
docker run --rm -v "$VOL":/build "$IMG" bash -c "
  timeout 300 qemu-system-aarch64 -M virt -cpu max -smp 2 -m 1024 -nographic -net none \
    -kernel /build/linux/arch/arm64/boot/Image \
    -append 'console=ttyAMA0 panic=-1 kunit.filter_glob=zz-none*' -no-reboot 2>&1 || true
" > "$OUT/ring3-$mode-console.txt"
grep -E "MOCKDEV_PROBE:" "$OUT/ring3-$mode-console.txt" || echo "  (no MOCKDEV_PROBE)"
V=$(grep -oE "verdict=DIFF_(PASS|FAIL)" "$OUT/ring3-$mode-console.txt" | tail -1 || true)
echo "[ring3:$mode] verdict: $V"
if { [ "$mode" = correct ] && [ "$V" = "verdict=DIFF_PASS" ]; } || { [ "$mode" = wrong ] && [ "$V" = "verdict=DIFF_FAIL" ]; }; then
  echo "RING 3 GATE ($mode): PASS"; exit 0
else echo "RING 3 GATE ($mode): FAIL"; exit 1; fi
