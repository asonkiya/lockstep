#!/usr/bin/env python3
"""Effect-trace oracle — Build-Order Step 1: prove the MECHANISM (boot-free).

The +36pp linchpin for the entangled core (dream/router/ENTANGLE_RESULTS.md):
bounded_state functions have no pure differential, but a driver's-register-program
argument generalizes — a function's correctness is the ordered sequence of
STATE effects it performs (reads/writes to its footprint), not just its return.
Generalize the Ring-3 MMIO recorder from readl/writel to an arbitrary state
footprint {id -> cell}: intercept every read/write to the footprint, RECORD the
ordered (kind, id, value) trace under a workload, then REPLAY a Rust candidate
against the frozen trace (reads return recorded values; writes must match id AND
value AND order). Accept iff the candidate consumes the whole trace + returns match.

The footprint (which locations to intercept) is exactly what CGIR's `effects`
layer computes — this proof hard-codes a footprint; the productized oracle takes
it from CGIR. This module proves the mechanism on a synthetic bounded-state
accumulator before that wiring, the same way structdiff/proof.py did for mirrors.

Scenarios (all host cc+rustc, no boot):
  correct        -> MATCH
  drop_count     -> DIVERGE  *** and return-only oracle says MATCH ***  (the
                   over-credit case: identical RETURN, wrong STATE — the whole
                   point; a value differential passes it, the effect-trace does not)
  wrong_loc      -> DIVERGE  (writes total to the wrong cell)
  wrong_val      -> DIVERGE  (max uses >= : an extra write on ties)
  reorder        -> DIVERGE  (reads count before total)
"""
from __future__ import annotations

import os
import subprocess
import tempfile

# The subject: a bounded-state accumulator over footprint cells
#   0=total, 1=count, 2=max. Reads/writes only those; returns the new total.
# Written to the eff_r/eff_w seam — this is "the real function, effect-instrumented"
# (the productized oracle rewrites real C accesses into this seam using CGIR's
# effect set; here it is written directly).
REF_C = r"""
#include <stdint.h>
typedef long long i64;
extern i64  eff_r(int id);
extern void eff_w(int id, i64 val);
i64 acct_add_ref(i64 v) {
    i64 t = eff_r(0); eff_w(0, t + v);      /* total += v   */
    i64 c = eff_r(1); eff_w(1, c + 1);      /* count += 1   */
    i64 m = eff_r(2); if (v > m) eff_w(2, v);/* max = max(max,v) */
    return t + v;                            /* return new total */
}
"""

_CAND_HEAD = """
extern "C" { fn eff_r(id: i32) -> i64; fn eff_w(id: i32, val: i64); }
#[no_mangle] pub extern "C" fn acct_add_rs(v: i64) -> i64 { unsafe {
"""

CANDS = {
    # correct — same effect program + return
    "correct": """
    let t = eff_r(0); eff_w(0, t.wrapping_add(v));
    let c = eff_r(1); eff_w(1, c.wrapping_add(1));
    let m = eff_r(2); if v > m { eff_w(2, v); }
    t.wrapping_add(v)
}""",
    # wrong_count_write: SAME read/write program (trace stays aligned), but count
    # += 2 instead of += 1. The return is total-based and UNAFFECTED, so a
    # return-only oracle passes it; the effect-trace rejects it on the W(1) value.
    # This is the over-credit case (identical RETURN, wrong STATE).
    "wrong_count_write": """
    let t = eff_r(0); eff_w(0, t.wrapping_add(v));
    let c = eff_r(1); eff_w(1, c.wrapping_add(2));
    let m = eff_r(2); if v > m { eff_w(2, v); }
    t.wrapping_add(v)
}""",
    # wrong_loc: writes total to cell 3 instead of 0
    "wrong_loc": """
    let t = eff_r(0); eff_w(3, t.wrapping_add(v));
    let c = eff_r(1); eff_w(1, c.wrapping_add(1));
    let m = eff_r(2); if v > m { eff_w(2, v); }
    t.wrapping_add(v)
}""",
    # wrong_val: max uses >= -> an EXTRA write on ties (workload has ties)
    "wrong_val": """
    let t = eff_r(0); eff_w(0, t.wrapping_add(v));
    let c = eff_r(1); eff_w(1, c.wrapping_add(1));
    let m = eff_r(2); if v >= m { eff_w(2, v); }
    t.wrapping_add(v)
}""",
    # reorder: reads count before total -> read-order divergence
    "reorder": """
    let c = eff_r(1); eff_w(1, c.wrapping_add(1));
    let t = eff_r(0); eff_w(0, t.wrapping_add(v));
    let m = eff_r(2); if v > m { eff_w(2, v); }
    t.wrapping_add(v)
}""",
}

# workload: a sequence of inputs that evolves the state machine; includes TIES
# (10,10 and 2,2) so the > vs >= distinction is exercised.
PROBE_C = r"""
#include <stdio.h>
#include <stdint.h>
typedef long long i64;
#define NCELL 8
static i64 cell[NCELL];
struct acc { char kind; int id; i64 val; };
static struct acc trace[8192];
static int tn, tpos, diverged, divpos;
enum { RECORD, REPLAY } mode;

i64 eff_r(int id) {
    if (mode == RECORD) { i64 v = cell[id]; trace[tn].kind='R'; trace[tn].id=id; trace[tn].val=v; tn++; return v; }
    if (tpos >= tn || trace[tpos].kind!='R' || trace[tpos].id!=id) { if(!diverged){diverged=1;divpos=tpos;} return 0; }
    return trace[tpos++].val;
}
void eff_w(int id, i64 val) {
    if (mode == RECORD) { trace[tn].kind='W'; trace[tn].id=id; trace[tn].val=val; tn++; cell[id]=val; return; }
    /* structural mismatch (wrong kind/location/extra-or-missing op) desyncs -> do
       NOT advance. value-only mismatch stays ALIGNED (advance) but flags divergence
       -- so a wrong WRITE VALUE is caught without corrupting the return, which is
       exactly the over-credit case a return-only oracle misses. */
    if (tpos >= tn || trace[tpos].kind!='W' || trace[tpos].id!=id) { if(!diverged){diverged=1;divpos=tpos;} return; }
    if (trace[tpos].val != val) { if(!diverged){diverged=1;divpos=tpos;} }
    tpos++;
}
extern i64 acct_add_ref(i64 v);
extern i64 acct_add_rs(i64 v);

int main(void) {
    static const i64 WL[] = { 3, -1, 10, 10, 0, 7, -5, 100, 2, 2 };
    int n = sizeof(WL)/sizeof(WL[0]);
    i64 cret[64], gret[64];

    mode = RECORD; tn = 0; for (int i=0;i<NCELL;i++) cell[i]=0;
    for (int i=0;i<n;i++) cret[i] = acct_add_ref(WL[i]);
    int reclen = tn;

    mode = REPLAY; tpos = 0; diverged = 0; divpos = -1; for (int i=0;i<NCELL;i++) cell[i]=0;
    int retmismatch = 0;
    for (int i=0;i<n;i++) { gret[i] = acct_add_rs(WL[i]); if (gret[i] != cret[i]) retmismatch = 1; }
    int consumed = (tpos == reclen);
    int trace_ok = !diverged && consumed;

    printf("EFFTRACE reclen=%d divpos=%d consumed=%d retmismatch=%d verdict=%s\n",
           reclen, divpos, consumed, retmismatch,
           (trace_ok && !retmismatch) ? "MATCH" : "DIVERGE");
    printf("  return-only-oracle: %s\n", retmismatch ? "DIVERGE" : "MATCH");
    return (trace_ok && !retmismatch) ? 0 : 1;
}
"""


def run_scenario(tmp, name):
    d = os.path.join(tmp, name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "ref.c"), "w").write(REF_C)
    open(os.path.join(d, "probe.c"), "w").write(PROBE_C)
    open(os.path.join(d, "cand.rs"), "w").write(_CAND_HEAD + CANDS[name] + "}\n")
    r = subprocess.run(["rustc", "--edition", "2021", "-O", "-C", "overflow-checks=off",
                        "--crate-type=staticlib", os.path.join(d, "cand.rs"),
                        "-o", os.path.join(d, "libcand.a")], capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL", r.stderr, "n/a"
    r = subprocess.run(["cc", "-O2", os.path.join(d, "probe.c"), os.path.join(d, "ref.c"),
                        os.path.join(d, "libcand.a"), "-o", os.path.join(d, "run")],
                       capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL", r.stderr, "n/a"
    r = subprocess.run([os.path.join(d, "run")], capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    v = "MATCH" if "verdict=MATCH" in out else "DIVERGE" if "verdict=DIVERGE" in out else "UNKNOWN"
    ret_only = "MATCH" if "return-only-oracle: MATCH" in out else "DIVERGE"
    return v, out, ret_only


def run_all(tmp):
    expect = {"correct": "MATCH", "wrong_count_write": "DIVERGE", "wrong_loc": "DIVERGE",
              "wrong_val": "DIVERGE", "reorder": "DIVERGE"}
    results = {}
    for name in CANDS:
        v, out, ret_only = run_scenario(tmp, name)
        results[name] = {"verdict": v, "expect": expect[name], "ret_only": ret_only,
                         "ok": v == expect[name], "out": out}
    return results


def main():
    with tempfile.TemporaryDirectory() as tmp:
        results = run_all(tmp)
    print("=== effect-trace oracle MECHANISM proof ===")
    allok = True
    for name, r in results.items():
        allok &= r["ok"]
        mark = "✓" if r["ok"] else "✗ UNEXPECTED"
        extra = ""
        if name == "wrong_count_write":
            extra = f"  [return-only oracle would say: {r['ret_only']}]"
        print(f"  {mark}  {name:17s} effect-trace={r['verdict']:8s} (expect {r['expect']}){extra}")
    dc = results["wrong_count_write"]
    strictly_stronger = dc["verdict"] == "DIVERGE" and dc["ret_only"] == "MATCH"
    print(f"\n  effect-trace strictly stronger than return-only oracle: {strictly_stronger}")
    print("MECHANISM PROOF:", "PASS — catches wrong-state even when the RETURN matches"
          if allok and strictly_stronger else "FAIL")
    return 0 if allok and strictly_stronger else 1


if __name__ == "__main__":
    raise SystemExit(main())
