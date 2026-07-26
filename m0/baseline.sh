#!/bin/bash
# Lockstep M0 — the sanitizer baseline.
#
# Boot a kernel with the concurrency sanitizers that will be Lockstep's oracle
# (KCSAN = data races, lockdep = lock-order/deadlock) under a real in-kernel
# workload (crypto self-tests + KUnit), and capture what they report on STOCK
# code. That report is the baseline every region transplant is judged against:
# a transplant is accepted only if it adds NO new KCSAN/lockdep finding here.
#
# Reuses CGIR rung 4's container (cgir-kernel-gate) + kernel tree volume
# (cgir-kbuild); Lockstep will grow its own once M0 is proven.
#
# Usage: baseline.sh /path/to/linux-src /path/to/out-dir
set -euo pipefail
SRC="$1"; OUT="$2"
VOL=cgir-kbuild; IMG=cgir-kernel-gate
mkdir -p "$OUT"
docker volume create "$VOL" >/dev/null

if ! docker run --rm -v "$VOL":/build "$IMG" test -f /build/linux/Makefile; then
  echo "[m0] copying kernel tree into volume..."
  docker run --rm -v "$SRC":/src:ro -v "$VOL":/build "$IMG" \
    bash -c "mkdir -p /build/linux && tar -C /src --exclude=.git -cf - . | tar -C /build/linux -xf -"
fi

echo "[m0] configuring: SMP + KCSAN + lockdep + KUnit + crypto self-tests..."
docker run --rm -v "$VOL":/build "$IMG" bash -c '
  cd /build/linux && make -s defconfig &&
  ./scripts/config \
    -e SMP \
    -e EXPERT -e DEBUG_KERNEL \
    -e KCSAN -e KCSAN_EARLY_ENABLE -e KCSAN_ASSUME_PLAIN_WRITES_ATOMIC \
    -e PROVE_LOCKING -e DEBUG_LOCK_ALLOC -e LOCKDEP -e DEBUG_SPINLOCK \
    -e DEBUG_ATOMIC_SLEEP \
    -e KUNIT -e KUNIT_ALL_TESTS \
    -e CRYPTO_MANAGER -d CRYPTO_MANAGER_DISABLE_TESTS -e CRYPTO_SELFTESTS \
    -e CRYPTO_AES -e CRYPTO_SHA256 -e CRYPTO_ECB -e CRYPTO_CBC \
    -e DYNAMIC_DEBUG &&
  make -s olddefconfig &&
  echo "--- sanitizer config sanity ---" &&
  grep -E "CONFIG_KCSAN=|CONFIG_PROVE_LOCKING=|CONFIG_KUNIT=|CONFIG_SMP=" .config
'

echo "[m0] building Image (KCSAN instruments everything — this is slow)..."
docker run --rm -v "$VOL":/build "$IMG" bash -c '
  cd /build/linux && make -s -j$(nproc) Image 2>&1 | tail -6
'

echo "[m0] booting SMP under QEMU, capturing console (900s cap; KCSAN boots are slow)..."
docker run --rm -v "$VOL":/build "$IMG" bash -c '
  timeout 900 qemu-system-aarch64 -M virt -cpu max -smp 4 -m 2048 -nographic -net none \
    -kernel /build/linux/arch/arm64/boot/Image \
    -append "console=ttyAMA0 panic=-1 kunit.enable=1 kcsan.early_enable=1 kunit.filter_glob=prb* hung_task_panic=1" -no-reboot 2>&1 || true
' > "$OUT/baseline-console.txt"

# Extract the sanitizer surface: KCSAN races + lockdep splats.
grep -nE "BUG: KCSAN|WARNING:.*lock|possible .* deadlock|INFO: .* lock|inconsistent lock state|BUG: (spinlock|sleeping)" \
  "$OUT/baseline-console.txt" > "$OUT/baseline-findings.txt" || true
KCSAN=$(grep -c "BUG: KCSAN" "$OUT/baseline-console.txt" || true)
LOCKDEP=$(grep -cE "possible .* deadlock|inconsistent lock state|WARNING:.*lock" "$OUT/baseline-console.txt" || true)
BOOTED=$(grep -c "Freeing unused kernel|Run /init|Kernel panic - not syncing: VFS" "$OUT/baseline-console.txt" || true)

echo
echo "=== M0 BASELINE ==="
echo "console lines : $(wc -l < "$OUT/baseline-console.txt")"
echo "KCSAN reports : $KCSAN"
echo "lockdep splats: $LOCKDEP"
echo "reached init  : $([ "$BOOTED" -gt 0 ] && echo yes || echo 'no (check console)')"
echo "findings -> $OUT/baseline-findings.txt"
if [ "$KCSAN" -eq 0 ] && [ "$LOCKDEP" -eq 0 ]; then
  echo "M0 BASELINE CLEAN: sanitizers report nothing on stock boot+selftests."
else
  echo "M0 BASELINE has findings (the known-baseline set a transplant must not add to):"
  head -8 "$OUT/baseline-findings.txt"
fi
