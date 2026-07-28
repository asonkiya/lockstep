#!/bin/bash
# Per-driver MMIO-record harness generator gate. For each real in-tree GPIO
# function: auto-extract its register program, generate the record/replay
# harness, and prove it non-vacuously — the correct candidate replays the C's
# recorded trace (MATCH), a wrong-register control is rejected on the trace
# (DIVERGE). Real driver source, no hand-scaffolding.
set -euo pipefail
export PATH="$HOME/.cargo/bin:$PATH"
G="$(cd "$(dirname "$0")" && pwd)"
DRV="drivers/gpio/gpio-ftgpio010.c"
FNS="ftgpio_gpio_ack_irq ftgpio_gpio_mask_irq ftgpio_gpio_unmask_irq"
pass=0; n=0
for f in $FNS; do
  n=$((n+1))
  echo "== $f =="
  if python3 "$G/mmio_harness.py" "$DRV" "$f" | sed 's/^/  /'; then pass=$((pass+1)); fi
done
echo
[ "$pass" -eq "$n" ] && echo "MMIOGEN GATE: PASS ($pass/$n real GPIO fns recorder-verified + control-rejected)" \
  || { echo "MMIOGEN GATE: FAIL ($pass/$n)"; exit 1; }
