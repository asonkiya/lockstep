#!/bin/bash
# Differential-oracle gate (oracle-manufacturing prototype).
#
# Links the C original (diff_ref.c, _ref symbols) + a Rust candidate + the
# differential probe into one kernel, boots it, and compares the two behavior
# traces IN-KERNEL. Two legs:
#   correct  candidate = the verified winner        -> DIFF_PASS (bit-identical to C)
#   wrong    candidate = a behaviorally-wrong variant -> DIFF_FAIL (caught)
#
# The point: the wrong candidate BOOTS CLEANLY (so the weak gate — boot-survival
# + KCSAN — would PASS it), yet the manufactured oracle REJECTS it. That gap is
# the ~73% of the kernel this capability is for.
#
# Usage: gate.sh <correct|wrong|all>
set -euo pipefail
G="$(cd "$(dirname "$0")" && pwd)"
OUT="$G/out"; VOL=cgir-kbuild; IMG=cgir-kernel-gate; GATE=crypto/lockstep_gate
WINNER="$(cd "$G/../.." && pwd)/kernel-gate/breadth/winner_phc.rs"
mkdir -p "$OUT"

wire() {
  docker run --rm -v "$VOL":/build "$IMG" bash -euc "
    cd /build/linux; mkdir -p $GATE
    grep -q 'obj-y += lockstep_gate/' crypto/Makefile || echo 'obj-y += lockstep_gate/' >> crypto/Makefile
    rm -f $GATE/*.c $GATE/*.h $GATE/*.o $GATE/*.o_shipped
  "
}

install_leg() {  # $1 = path to candidate .rs
  local rs="$1"
  docker run --rm -v "$VOL":/build -v "$G/probe":/p:ro -v "$(cd "$(dirname "$rs")" && pwd)":/r:ro "$IMG" bash -euc "
    cd /build/linux/$GATE
    cp /p/diff_probe.c /p/diff_ref.c .
    rustc --target aarch64-unknown-none-softfloat --emit=obj \
      -C panic=abort -C relocation-model=static -O /r/$(basename "$rs") -o cand_rust.o_shipped
    test -s cand_rust.o_shipped
    printf 'obj-y := diff_probe.o diff_ref.o cand_rust.o\n' > Kbuild
  "
}

run_leg() {  # $1 = leg name
  local leg="$1"
  echo "[$leg] building Image..."
  docker run --rm -v "$VOL":/build "$IMG" bash -eo pipefail -uc "
    cd /build/linux; rm -f arch/arm64/boot/Image
    make -s -j\$(nproc) Image 2>&1 | tail -3; test -f arch/arm64/boot/Image
  " > "$OUT/$leg-build.txt" 2>&1 || { echo "[$leg] BUILD FAILED"; tail -8 "$OUT/$leg-build.txt"; return 2; }
  echo "[$leg] booting..."
  docker run --rm -v "$VOL":/build "$IMG" bash -c "
    timeout 300 qemu-system-aarch64 -M virt -cpu max -smp 2 -m 1024 -nographic -net none \
      -kernel /build/linux/arch/arm64/boot/Image -append 'console=ttyAMA0 panic=-1' -no-reboot 2>&1 || true
  " > "$OUT/$leg-console.txt"
  local reached verdict
  # "reached the probe" = booted past any crash point (the probe is a
  # late_initcall). This is the signal that the WEAK gate (boot-survival) would
  # have PASSED this candidate — the whole point of the wrong leg.
  reached=$(grep -c "DIFF_PROBE: ops=" "$OUT/$leg-console.txt" || true)
  verdict=$(grep -oE "verdict=DIFF_(PASS|FAIL)" "$OUT/$leg-console.txt" | tail -1 || true)
  grep -E "DIFF_PROBE:" "$OUT/$leg-console.txt" || echo "  (no DIFF_PROBE line — probe didn't run)"
  echo "[$leg] reached_probe=$([ "$reached" -gt 0 ] && echo yes || echo no) $verdict"
  echo "$leg $verdict $reached" >> "$OUT/verdicts.txt"
}

leg_correct() { wire; install_leg "$WINNER"; run_leg correct; }
leg_wrong()   { python3 "$G/mutate.py" "$WINNER" "$OUT/wrong_phc.rs" >/dev/null; wire; install_leg "$OUT/wrong_phc.rs"; run_leg wrong; }

case "${1:-all}" in
  correct) leg_correct ;;
  wrong)   leg_wrong ;;
  all)     : > "$OUT/verdicts.txt"; leg_correct; leg_wrong
           echo; echo "=== DIFFERENTIAL-ORACLE GATE ==="
           cat "$OUT/verdicts.txt"
           C=$(awk '$1=="correct"{print ($2=="verdict=DIFF_PASS")?"ok":"bad"}' "$OUT/verdicts.txt")
           # wrong leg must BOOT (weak gate would pass) yet DIFF_FAIL
           W=$(awk '$1=="wrong"{print ($2=="verdict=DIFF_FAIL" && $3>0)?"ok":"bad"}' "$OUT/verdicts.txt")
           echo "verified transplant accepted: $C | wrong-but-booting transplant rejected: $W"
           if [ "$C" = ok ] && [ "$W" = ok ]; then echo "DIFF GATE: PASS"; exit 0
           else echo "DIFF GATE: FAIL"; exit 1; fi ;;
  *) echo "usage: $0 correct|wrong|all"; exit 2 ;;
esac
