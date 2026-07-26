#!/bin/bash
# Lockstep M4 (depth leg) — the IN-KERNEL gate for the model's region transplant.
#
# Three legs, each: install probe + target in crypto/lockstep_gate, incremental
# Image build (the volume kernel is the M0 KCSAN+lockdep config), boot QEMU
# arm64 SMP, capture console:
#   stock      lockstep_target.c (C)          expect FUNC_PASS + 0 probe-KCSAN
#   rewrite    winner_kernel.rs (model Rust)  expect FUNC_PASS + 0 probe-KCSAN -> PASS
#   sabotaged  dropped-lock variant           expect probe-KCSAN race (and/or
#                                             FUNC_FAIL) -> REJECT
#
# "probe-KCSAN" = a BUG: KCSAN report whose window names a lockstep_* symbol —
# the transplant is judged only on findings it ADDS (M0 baseline discipline).
#
# Usage: gate.sh <leg: stock|rewrite|sabotaged|all>
set -euo pipefail
G="$(cd "$(dirname "$0")" && pwd)"
OUT="$G/out"; VOL=cgir-kbuild; IMG=cgir-kernel-gate; GATE=crypto/lockstep_gate
mkdir -p "$OUT"

wire() {
  docker run --rm -v "$VOL":/build "$IMG" bash -euc "
    cd /build/linux
    mkdir -p $GATE
    grep -q 'obj-y += lockstep_gate/' crypto/Makefile || echo 'obj-y += lockstep_gate/' >> crypto/Makefile
    # neutralize any stale cgir_gate hook whose dir/Kbuild is gone
    if grep -q 'obj-y += cgir_gate/' crypto/Makefile && [ ! -f crypto/cgir_gate/Kbuild ]; then
      mkdir -p crypto/cgir_gate && printf 'obj-y :=\n' > crypto/cgir_gate/Kbuild
    fi
  "
}

install_common() {
  docker run --rm -v "$VOL":/build -v "$G/probe":/p:ro "$IMG" bash -euc "
    cd /build/linux/$GATE
    cp /p/lockstep_ring.h /p/lockstep_probe.c .
  "
}

install_target() {  # $1 = c | rust:<path>
  case "$1" in
    c)
      docker run --rm -v "$VOL":/build -v "$G/probe":/p:ro "$IMG" bash -euc "
        cd /build/linux/$GATE
        rm -f lockstep_target_rust.o lockstep_target_rust.o_shipped lockstep_target.o
        cp /p/lockstep_target.c .
        printf 'obj-y := lockstep_probe.o lockstep_target.o\n' > Kbuild
      " ;;
    rust:*)
      local rs="${1#rust:}"
      docker run --rm -v "$VOL":/build -v "$(cd "$(dirname "$rs")" && pwd)":/r:ro "$IMG" bash -euc "
        cd /build/linux/$GATE
        rm -f lockstep_target.c lockstep_target.o lockstep_target_rust.o lockstep_target_rust.o_shipped
        rustc --target aarch64-unknown-none-softfloat --emit=obj \
          -C panic=abort -C relocation-model=static -O \
          /r/$(basename "$rs") -o lockstep_target_rust.o_shipped
        test -s lockstep_target_rust.o_shipped
        printf 'obj-y := lockstep_probe.o lockstep_target_rust.o\n' > Kbuild
      " ;;
  esac
}

run_leg() {  # $1 = leg name
  local leg="$1"
  echo "[$leg] building Image (incremental, KCSAN config)..."
  docker run --rm -v "$VOL":/build "$IMG" bash -eo pipefail -uc "
    cd /build/linux
    rm -f arch/arm64/boot/Image
    make -s -j\$(nproc) Image 2>&1 | tail -3
    test -f arch/arm64/boot/Image
  " > "$OUT/$leg-build.txt" 2>&1 || { echo "[$leg] BUILD FAILED"; tail -8 "$OUT/$leg-build.txt"; return 2; }

  echo "[$leg] booting QEMU SMP (KUnit filtered out; cap 600s)..."
  docker run --rm -v "$VOL":/build "$IMG" bash -c "
    timeout 600 qemu-system-aarch64 -M virt -cpu max -smp 4 -m 2048 -nographic -net none \
      -kernel /build/linux/arch/arm64/boot/Image \
      -append 'console=ttyAMA0 panic=-1 kcsan.early_enable=1 kunit.filter_glob=zz-none*' \
      -no-reboot 2>&1 || true
  " > "$OUT/$leg-console.txt"

  # verdict extraction
  local func kcsan_probe kcsan_all
  func=$(grep -oE "verdict=FUNC_(PASS|FAIL)" "$OUT/$leg-console.txt" | tail -1 || true)
  kcsan_all=$(grep -c "BUG: KCSAN" "$OUT/$leg-console.txt" || true)
  # a KCSAN report is OURS if its 14-line window names a lockstep symbol
  kcsan_probe=$(grep -A14 "BUG: KCSAN" "$OUT/$leg-console.txt" | grep -c "lockstep_" || true)
  grep -E "LOCKSTEP_PROBE:" "$OUT/$leg-console.txt" | tail -2
  echo "[$leg] functional=$func kcsan_total=$kcsan_all kcsan_on_probe=$kcsan_probe"
  echo "$leg $func $kcsan_probe" >> "$OUT/verdicts.txt"
}

leg_stock()     { wire; install_common; install_target c; run_leg stock; }
leg_rewrite()   { wire; install_common; install_target "rust:$G/winner_kernel.rs"; run_leg rewrite; }
leg_sabotaged() {
  python3 "$G/synthesize_kernel.py" --emit-sabotaged "$G/out/sabotaged_kernel.rs"
  wire; install_common; install_target "rust:$G/out/sabotaged_kernel.rs"; run_leg sabotaged
}

case "${1:-all}" in
  stock)     leg_stock ;;
  rewrite)   leg_rewrite ;;
  sabotaged) leg_sabotaged ;;
  all)       : > "$OUT/verdicts.txt"; leg_stock; leg_rewrite; leg_sabotaged
             echo; echo "=== M4 IN-KERNEL GATE ==="
             cat "$OUT/verdicts.txt"
             S=$(awk '$1=="stock"     {print ($2=="verdict=FUNC_PASS" && $3==0) ? "ok":"bad"}' "$OUT/verdicts.txt")
             R=$(awk '$1=="rewrite"   {print ($2=="verdict=FUNC_PASS" && $3==0) ? "ok":"bad"}' "$OUT/verdicts.txt")
             N=$(awk '$1=="sabotaged" {print ($3>0 || $2=="verdict=FUNC_FAIL") ? "ok":"bad"}' "$OUT/verdicts.txt")
             echo "stock clean: $S | transplant accepted: $R | dropped-lock rejected: $N"
             if [ "$S" = ok ] && [ "$R" = ok ] && [ "$N" = ok ]; then
               echo "M4 IN-KERNEL GATE: PASS"; exit 0
             else
               echo "M4 IN-KERNEL GATE: FAIL"; exit 1
             fi ;;
  *) echo "usage: $0 stock|rewrite|sabotaged|all"; exit 2 ;;
esac
