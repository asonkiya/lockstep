#!/bin/bash
# hostdiff gate — the T0 boot-free oracle, proven on the real fleet battery.
#   * 7 real kernel functions (incl. a cross-TU dep: lcm -> gcd), verified
#     against their boot-verified Rust winners: all must MATCH.
#   * negative control: Ring 5's ACTUAL bad synth (int_sqrt wrong algorithm,
#     y + m <= x/m) must DIVERGE — the same bug the boot gate caught, now
#     caught in under a second with no kernel, no QEMU, no boot.
set -euo pipefail
G="$(cd "$(dirname "$0")" && pwd)"; R="$G/../ratchet"
total_t0=$(python3 -c 'import time; print(time.time())')

run() { # file func cand [deps...]
  local f="$1" fn="$2" cand="$3"; shift 3
  local deps=(); [ $# -gt 0 ] && deps=(--deps "$@")
  python3 "$G/hostdiff.py" "$f" "$fn" --cand "$cand" "${deps[@]}"
}

echo "== battery: 7 real kernel fns vs their boot-verified Rust winners =="
run lib/math/gcd.c      gcd            "$R/ring5/gcd.rs"
run lib/math/int_sqrt.c int_sqrt       "$R/ring5/int_sqrt5.rs"
run lib/math/int_pow.c  int_pow        "$R/ring5/int_pow.rs"
run lib/hweight.c       __sw_hweight32 "$R/ring5/hw32.rs"
run lib/hweight.c       __sw_hweight64 "$R/ring2/hweight64.rs"
run lib/math/lcm.c      lcm            "$R/ring5/lcm.rs"    lib/math/gcd.c
run lib/math/lcm.c      lcm_not_zero   "$R/ring5/lcm_nz.rs" lib/math/gcd.c

echo
echo "== negative control: Ring 5's real bad synth (wrong int_sqrt algorithm) =="
cat > /tmp/hostdiff_badsqrt.rs <<'EOF'
// Ring 5's actual first-attempt failure, reconstructed: chooses the
// y + m <= x / m Newton-ish test instead of shift-and-subtract.
#[no_mangle]
pub extern "C" fn cgir_int_sqrt(x: u64) -> u64 {
    if x <= 1 { return x; }
    let mut y: u64 = 0;
    let mut m: u64 = 1u64 << 62;
    while m != 0 {
        if m != 0 && y + m <= x / m { y += m; }
        m >>= 2;
    }
    y
}
EOF
if python3 "$G/hostdiff.py" lib/math/int_sqrt.c int_sqrt --cand /tmp/hostdiff_badsqrt.rs; then
  echo "  ✗ bad synth slipped through"; exit 1
else
  echo "  ✓ DIVERGE — the bug the boot gate caught, caught boot-free"
fi

total_t1=$(python3 -c 'import time; print(time.time())')
secs=$(python3 -c "print(round($total_t1-$total_t0,1))")
echo
echo "HOSTDIFF GATE: PASS (7 MATCH + 1 control DIVERGE, ~16M differential cases, ${secs}s total, 0 boots)"
