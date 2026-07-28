#!/bin/bash
# Driver-MMIO effect recorder gate (userspace — the mechanism; drops into the
# in-kernel readl/writel seam that Ring 3/4 booted).
#   correct : the Rust transplant replays the recorded trace exactly   -> MATCH
#   subtle  : "skip the status poll" — same return value, wrong register program;
#             the recording rejects it on trace divergence               -> DIVERGE
# Usage: gate.sh <correct|subtle>
set -euo pipefail
G="$(cd "$(dirname "$0")" && pwd)"; mode="${1:-correct}"
CAND="$G/cand.rs"
if [ "$mode" = subtle ]; then
  # drop the poll: same value (device model sets result synchronously), wrong trace
  python3 - "$G/cand.rs" "/tmp/cand_subtle.rs" <<'PY'
import sys, re
s = open(sys.argv[1]).read()
s = re.sub(r"\n\s*// POLL-BEGIN\n\s*while reg_read\(REG_STATUS\)[^\n]*\{\}\n\s*// POLL-END\n",
           "\n        // [subtle: status poll dropped]\n", s, count=1)
assert "poll dropped" in s, "poll block not found"
open(sys.argv[2], "w").write(s)
PY
  CAND="/tmp/cand_subtle.rs"
fi
echo "[recorder:$mode] compiling Rust candidate + C harness..."
rustc --edition 2021 -O --crate-type=staticlib "$CAND" -o /tmp/libcand.a 2>/tmp/rustc.log || { cat /tmp/rustc.log; exit 1; }
cc -O2 "$G/recorder.c" /tmp/libcand.a -o /tmp/recorder
echo "[recorder:$mode] record C trace -> replay to Rust -> compare:"
# subtle mode DELIBERATELY diverges (recorder exits non-zero) — capture the code
# without letting `set -e` abort before the verdict is printed
if /tmp/recorder; then rc=0; else rc=$?; fi
if [ "$mode" = correct ]; then
  [ $rc -eq 0 ] && echo "RECORDER GATE (correct): PASS (transplant replays the recording exactly)" || { echo "RECORDER GATE (correct): FAIL"; exit 1; }
else
  [ $rc -ne 0 ] && echo "RECORDER GATE (subtle): PASS (recording rejected the wrong register program)" || { echo "RECORDER GATE (subtle): FAIL — a value-only check would have missed it"; exit 1; }
fi
