#!/bin/bash
# M4 breadth — in-kernel gate for the ptp_mock cluster transplant (4 regions).
# Same discipline as the depth leg (m4/gate.sh): three legs, each = install in
# crypto/lockstep_gate, incremental KCSAN Image build, QEMU SMP boot, verdicts
# from the console. "probe-KCSAN" = reports naming lockstep_* symbols.
#
# Usage: gate.sh <stock|rewrite|sabotaged|all>
set -euo pipefail
G="$(cd "$(dirname "$0")" && pwd)"
OUT="$G/out"; VOL=cgir-kbuild; IMG=cgir-kernel-gate; GATE=crypto/lockstep_gate
mkdir -p "$OUT"

wire() {
  docker run --rm -v "$VOL":/build "$IMG" bash -euc "
    cd /build/linux
    mkdir -p $GATE
    grep -q 'obj-y += lockstep_gate/' crypto/Makefile || echo 'obj-y += lockstep_gate/' >> crypto/Makefile
    # clear the depth leg's files out of the gate dir so only the phc set builds
    rm -f $GATE/lockstep_probe.c $GATE/lockstep_ring.h $GATE/lockstep_target.c \
          $GATE/lockstep_target.o $GATE/lockstep_target_rust.o_shipped $GATE/lockstep_target_rust.o
  "
}

install_common() {
  docker run --rm -v "$VOL":/build -v "$G/probe":/p:ro "$IMG" bash -euc "
    cd /build/linux/$GATE
    cp /p/lockstep_phc.h /p/lockstep_phc_probe.c .
  "
}

install_target() {  # c | rust:<path>
  case "$1" in
    c)
      docker run --rm -v "$VOL":/build -v "$G/probe":/p:ro "$IMG" bash -euc "
        cd /build/linux/$GATE
        rm -f lockstep_phc_rust.o lockstep_phc_rust.o_shipped
        cp /p/lockstep_phc_target.c .
        printf 'obj-y := lockstep_phc_probe.o lockstep_phc_target.o\n' > Kbuild
      " ;;
    rust:*)
      local rs="${1#rust:}"
      docker run --rm -v "$VOL":/build -v "$(cd "$(dirname "$rs")" && pwd)":/r:ro "$IMG" bash -euc "
        cd /build/linux/$GATE
        rm -f lockstep_phc_target.c lockstep_phc_target.o lockstep_phc_rust.o lockstep_phc_rust.o_shipped
        rustc --target aarch64-unknown-none-softfloat --emit=obj \
          -C panic=abort -C relocation-model=static -O \
          /r/$(basename "$rs") -o lockstep_phc_rust.o_shipped
        test -s lockstep_phc_rust.o_shipped
        printf 'obj-y := lockstep_phc_probe.o lockstep_phc_rust.o\n' > Kbuild
      " ;;
  esac
}

run_leg() {
  local leg="$1"
  echo "[$leg] building Image (incremental, KCSAN config)..."
  docker run --rm -v "$VOL":/build "$IMG" bash -eo pipefail -uc "
    cd /build/linux
    rm -f arch/arm64/boot/Image
    make -s -j\$(nproc) Image 2>&1 | tail -3
    test -f arch/arm64/boot/Image
  " > "$OUT/$leg-build.txt" 2>&1 || { echo "[$leg] BUILD FAILED"; tail -8 "$OUT/$leg-build.txt"; return 2; }

  echo "[$leg] booting QEMU SMP (cap 600s)..."
  docker run --rm -v "$VOL":/build "$IMG" bash -c "
    timeout 600 qemu-system-aarch64 -M virt -cpu max -smp 4 -m 2048 -nographic -net none \
      -kernel /build/linux/arch/arm64/boot/Image \
      -append 'console=ttyAMA0 panic=-1 kcsan.early_enable=1 kunit.filter_glob=zz-none*' \
      -no-reboot 2>&1 || true
  " > "$OUT/$leg-console.txt"

  local func kcsan_all kcsan_probe
  func=$(grep -oE "verdict=FUNC_(PASS|FAIL)" "$OUT/$leg-console.txt" | tail -1 || true)
  kcsan_all=$(grep -c "BUG: KCSAN" "$OUT/$leg-console.txt" || true)
  kcsan_probe=$(grep -A14 "BUG: KCSAN" "$OUT/$leg-console.txt" | grep -c "lockstep_" || true)
  grep -E "LOCKSTEP_PHC:" "$OUT/$leg-console.txt" | tail -2
  echo "[$leg] functional=$func kcsan_total=$kcsan_all kcsan_on_probe=$kcsan_probe"
  echo "$leg $func $kcsan_probe" >> "$OUT/verdicts.txt"
}

leg_stock()     { wire; install_common; install_target c; run_leg stock; }
leg_rewrite()   { wire; install_common; install_target "rust:$G/winner_phc.rs"; run_leg rewrite; }
leg_sabotaged() {
  python3 "$G/synthesize_phc.py" --emit-sabotaged "$OUT/sabotaged_phc.rs"
  wire; install_common; install_target "rust:$OUT/sabotaged_phc.rs"; run_leg sabotaged
}

case "${1:-all}" in
  stock)     leg_stock ;;
  rewrite)   leg_rewrite ;;
  sabotaged) leg_sabotaged ;;
  all)       : > "$OUT/verdicts.txt"; leg_stock; leg_rewrite; leg_sabotaged
             echo; echo "=== M4 BREADTH GATE (ptp_mock cluster, 4 regions) ==="
             cat "$OUT/verdicts.txt"
             S=$(awk '$1=="stock"     {print ($2=="verdict=FUNC_PASS" && $3==0) ? "ok":"bad"}' "$OUT/verdicts.txt")
             R=$(awk '$1=="rewrite"   {print ($2=="verdict=FUNC_PASS" && $3==0) ? "ok":"bad"}' "$OUT/verdicts.txt")
             N=$(awk '$1=="sabotaged" {print ($3>0) ? "ok":"bad"}' "$OUT/verdicts.txt")
             echo "stock clean: $S | cluster transplant accepted: $R | dropped-lock rejected by KCSAN: $N"
             if [ "$S" = ok ] && [ "$R" = ok ] && [ "$N" = ok ]; then
               echo "M4 BREADTH GATE: PASS"; exit 0
             else
               echo "M4 BREADTH GATE: FAIL"; exit 1
             fi ;;
  *) echo "usage: $0 stock|rewrite|sabotaged|all"; exit 2 ;;
esac
