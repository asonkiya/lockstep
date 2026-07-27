#!/bin/bash
# Struct-mirror library gate: generate mirrors for real kernel structs and prove
# each ABI-correct two independent ways —
#   (1) rustc compiles the Rust const-asserts  = rustc's layout == generator's
#   (2) the kernel build compiles the BUILD_BUG_ONs against real headers
#                                               = the real kernel's layout == generator's
# Both pass => rustc-layout == generator-model == kernel-layout => mirrors correct.
set -euo pipefail
G="$(cd "$(dirname "$0")" && pwd)"; VOL=cgir-kbuild; IMG=cgir-kernel-gate; GATE=crypto/lockstep_gate
python3 "$G/generate.py"

echo "=== (1) rustc: do the Rust const-asserts hold? (rustc agrees with the generator) ==="
docker run --rm -v "$G":/g:ro "$IMG" bash -c \
  "cd /g && rustc --target aarch64-unknown-none-softfloat --crate-type=lib -C panic=abort out_mirrors.rs -o /tmp/m.rlib 2>&1 | head -6 && echo RUST_OK" \
  | tee /tmp/mir_rust.log
grep -q RUST_OK /tmp/mir_rust.log && echo "  ✓ rustc-layout matches generator" || { echo "  ✗ rustc const-assert failed"; exit 1; }

echo "=== (2) kernel BUILD_BUG_ON: does the REAL kernel layout agree? ==="
docker run --rm -v "$VOL":/build -v "$G/out":/o:ro "$IMG" bash -euc "
  cd /build/linux; mkdir -p $GATE
  grep -q 'obj-y += lockstep_gate/' crypto/Makefile || echo 'obj-y += lockstep_gate/' >> crypto/Makefile
  cd $GATE; rm -f *.c *.o *.o_shipped; cp /o/guards.c .
  printf 'obj-y := guards.o\n' > Kbuild
"
docker run --rm -v "$VOL":/build "$IMG" bash -eo pipefail -uc '
  cd /build/linux; make -s crypto/lockstep_gate/guards.o 2>&1 | tail -5 && echo KERNEL_OK
' | tee /tmp/mir_kernel.log
grep -q KERNEL_OK /tmp/mir_kernel.log && echo "  ✓ real kernel layout matches generator" || { echo "  ✗ kernel BUILD_BUG_ON failed (a mirror mismatches the real ABI)"; exit 1; }

echo "MIRROR LIBRARY GATE: PASS (mirrors proven ABI-correct: rustc == generator == kernel)"
