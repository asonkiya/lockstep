#!/bin/bash
# Static-function cluster weaving gate. Operates on the REAL lib/math/gcd.c
# (includes swapped for a userspace shim; the function bodies are the actual
# kernel source). Four legs, all must hold:
#   (1) ORPHAN   — the naive single-fn weave (excise gcd only) leaves `static
#                  binary_gcd` unused; -Werror=unused-function MUST fail the
#                  build. This is the problem the library exists to solve;
#                  if it compiled clean the demo would be vacuous.
#   (2) CLUSTER  — the whole-cluster weave (excise gcd AND binary_gcd) compiles
#                  clean under the same -Werror.
#   (3) DIFF     — link the cluster-woven C (gcd -> Rust cgir_gcd) against the
#                  stock C (gcd_ref, its own static binary_gcd) and drive the
#                  EXPORTED entry over a wide domain: bit-identical -> MATCH.
#                  binary_gcd is never called by name (private in Rust, gone from
#                  C) yet is exercised transitively — boundary verification.
#   (4) CONTROL  — a bug planted INSIDE the private helper (dropped swap) is
#                  caught by the same entry-only differential -> DIVERGE, proving
#                  the boundary oracle reaches the helper.
set -euo pipefail
G="$(cd "$(dirname "$0")" && pwd)"
K="${KSRC:-/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux}"
W=/tmp/cluster_gate; rm -rf "$W"; mkdir -p "$W"; cp "$G/kdefs.h" "$W/"
CFLAGS="-O2 -fcommon -I$W"

# Real source -> userspace-buildable (swap kernel includes for the shim).
python3 - "$K/lib/math/gcd.c" "$W/gcd_stock.c" <<'PY'
import re, sys
s = open(sys.argv[1]).read()
s = re.sub(r'#include <linux/[^>]+>\n', '', s, count=1)          # first -> replace
s = '#include "kdefs.h"\n' + re.sub(r'#include <linux/[^>]+>\n', '', s)
open(sys.argv[2], "w").write(s)
PY

echo "== cluster analysis (from real gcd.c) =="
python3 "$G/cluster.py" "$W/gcd_stock.c" gcd report | sed 's/^/  /'

# generate the two weaves
python3 "$G/cluster.py" "$W/gcd_stock.c" gcd naive   > "$W/gcd_naive.c"
python3 "$G/cluster.py" "$W/gcd_stock.c" gcd cluster > "$W/gcd_cluster.c"

echo
echo "== (1) ORPHAN: naive single-fn weave must FAIL -Werror=unused-function =="
if cc $CFLAGS -Werror -Wunused-function -c "$W/gcd_naive.c" -o "$W/naive.o" 2>"$W/naive.err"; then
  echo "  ✗ naive weave compiled clean — orphan not demonstrated (vacuous)"; exit 1
else
  grep -q "binary_gcd" "$W/naive.err" && echo "  ✓ orphaned static binary_gcd rejected the build:" \
    && sed -n '1,2p' "$W/naive.err" | sed 's/^/      /' || { echo "  ✗ failed for the wrong reason"; cat "$W/naive.err"; exit 1; }
fi

echo
echo "== (2) CLUSTER: whole-cluster weave compiles clean under the same -Werror =="
cc $CFLAGS -Werror -Wunused-function -c "$W/gcd_cluster.c" -o "$W/cluster.o" \
  && echo "  ✓ gcd forwards to Rust, binary_gcd removed — no orphan, no warning"

echo
echo "== (3) DIFF: cluster-woven gcd (-> Rust) vs stock gcd_ref, entry-only =="
cc $CFLAGS -Dgcd=gcd_ref -c "$W/gcd_stock.c" -o "$W/gcd_ref.o"        # golden C
rustc --edition 2021 -O --crate-type=staticlib "$G/cluster.rs" -o "$W/libcluster.a" 2>"$W/rustc.log" || { cat "$W/rustc.log"; exit 1; }
cc $CFLAGS "$W/gcd_cluster.c" "$W/gcd_ref.o" "$G/probe/probe.c" "$W/libcluster.a" -o "$W/diff"
if "$W/diff" > "$W/diff.out"; then rc=0; else rc=1; fi; sed 's/^/  /' "$W/diff.out"
[ $rc -eq 0 ] && echo "  ✓ MATCH — the transplanted cluster equals its C original" || { echo "  ✗ unexpected DIVERGE on correct cluster"; exit 1; }

echo
echo "== (4) CONTROL: bug inside the PRIVATE helper must still be caught =="
rustc --edition 2021 -O --crate-type=staticlib "$G/cluster_bad.rs" -o "$W/libbad.a" 2>"$W/rustc.log" || { cat "$W/rustc.log"; exit 1; }
cc $CFLAGS "$W/gcd_cluster.c" "$W/gcd_ref.o" "$G/probe/probe.c" "$W/libbad.a" -o "$W/diffbad"
if "$W/diffbad" > "$W/diffbad.out"; then rc=0; else rc=1; fi; sed 's/^/  /' "$W/diffbad.out"
[ $rc -ne 0 ] && echo "  ✓ DIVERGE — entry-only differential reaches the private helper" || { echo "  ✗ sabotaged helper slipped through — boundary oracle blind"; exit 1; }

echo
echo "CLUSTER WEAVING GATE: PASS (orphan avoided; cluster differential-verified at the exported boundary; helper bug caught)"
