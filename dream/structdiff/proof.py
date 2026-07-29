#!/usr/bin/env python3
"""Struct-driven branch harness — Build-Order Step 1: prove the MECHANISM.

Goal: verify a C function that BRANCHES on struct fields by translating it to
Rust that reads the struct through a generated #[repr(C)] MIRROR, then running a
boot-free differential. This module proves the mechanism end-to-end on a
synthetic function before any real extraction is built on it (the same
discipline cfg.py applied to branch parsing).

The soundness chain:
  * ONE struct instance is constructed in C (real layout) and its pointer is
    passed to BOTH the C reference (reads via the real struct) AND the Rust
    candidate (reads via the mirror). They observe the SAME bytes, so they
    compute the same result IFF the mirror layout == the real layout — which is
    exactly what the mirror gate proves (here, on the host, re-checked with a
    static_assert(sizeof) and the generator's own offsets).
  * The field sweep drives every struct-field condition to BOTH outcomes; a
    PATH-COVERAGE gate REFUSES the close if any branch of the C reference went
    unexercised (a shared blind spot is not a sound close).
  * A wrong translation (swapped branch arms, inverted condition, wrong field)
    reads the same bytes but computes a different result -> DIVERGE.

Scenarios run by main():
  A correct candidate      -> MATCH  + full path coverage       => PASS
  B swapped-arm candidate   -> DIVERGE (caught on an exercised branch)
  C inverted-condition cand -> DIVERGE
  D under-coverage sweep     -> REFUSE (path coverage incomplete, NOT a vacuous MATCH)
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mirror"))
import mirror  # noqa: E402

# --- the synthetic subject: a struct-field-conditioned function -------------
# Branches on w->flags (bit test) and w->mode (comparison); returns a field-
# derived value. Two nested branches -> four leaf paths to cover.
STRUCT_SRC = """
struct widget {
\tunsigned int flags;
\tint lo;
\tint hi;
\tunsigned int mode;
};
"""
STRUCT_NAME = "widget"

# C reference, INSTRUMENTED with a per-branch taken bitmap (br[]) so the probe
# can enforce path coverage. This is what the cfg.py emitter will generate
# automatically for real functions; here it is written by hand as the oracle.
CREF = r"""
#include "widget.h"
unsigned char br[4];
int widget_pick(const struct widget *w) {
    if (w->flags & 1u) { br[0] = 1; return w->hi; }
    else {
        br[1] = 1;
        if (w->mode > 2u) { br[2] = 1; return w->lo * 2; }
        else              { br[3] = 1; return w->lo; }
    }
}
"""

# Rust candidates read the struct through the generated mirror (injected at {MIRROR}).
CAND_CORRECT = """
{MIRROR}
#[no_mangle]
pub extern "C" fn widget_pick_rs(w: *const Widget) -> i32 {{
    let w = unsafe {{ &*w }};
    if w.flags & 1 != 0 {{ w.hi }}
    else if w.mode > 2 {{ w.lo * 2 }}
    else {{ w.lo }}
}}
"""
# NEGATIVE CONTROL B: swaps the arms of the flags branch (returns lo where it
# should return hi). Reads the same bytes -> must DIVERGE on the exercised branch.
CAND_SWAP = """
{MIRROR}
#[no_mangle]
pub extern "C" fn widget_pick_rs(w: *const Widget) -> i32 {{
    let w = unsafe {{ &*w }};
    if w.flags & 1 != 0 {{ w.lo }}
    else if w.mode > 2 {{ w.lo * 2 }}
    else {{ w.lo }}
}}
"""
# NEGATIVE CONTROL C: inverts the mode comparison (> becomes <=).
CAND_INVERT = """
{MIRROR}
#[no_mangle]
pub extern "C" fn widget_pick_rs(w: *const Widget) -> i32 {{
    let w = unsafe {{ &*w }};
    if w.flags & 1 != 0 {{ w.hi }}
    else if w.mode <= 2 {{ w.lo * 2 }}
    else {{ w.lo }}
}}
"""


def _probe(nbranch: int, cover_flags: bool) -> str:
    """Differential probe: construct a widget, sweep its fields, compare returns,
    and enforce path coverage over the C reference's br[] bitmap.

    cover_flags=False models an UNDER-COVERAGE sweep (never sets the flags bit),
    so branch 0 stays untaken and the path-coverage gate must REFUSE."""
    flags_vals = "{0u, 1u, 3u}" if cover_flags else "{0u, 2u}"  # bit0 never set when False
    return r"""
#include <stdio.h>
#include "widget.h"
extern int widget_pick(const struct widget *);
extern int widget_pick_rs(const struct widget *);
extern unsigned char br[%d];

/* host gate leg: the mirror's size must equal the real struct's (offsets are
   re-checked by the generator; a wrong mirror would also diverge on reads). */
_Static_assert(sizeof(struct widget) == %d, "mirror size != real struct size");

int main(void) {
    unsigned long long cases = 0, bad = 0;
    int fe = 0, fg = 0; unsigned ff = 0, fm = 0;
    static const unsigned flags[] = %s;
    static const unsigned modes[] = {0u, 1u, 2u, 3u, 7u};
    static const int los[] = {-2, 0, 5, 1000};
    static const int his[] = {-9, 0, 42, 77};
    for (unsigned fi = 0; fi < sizeof(flags)/sizeof(flags[0]); fi++)
    for (unsigned mi = 0; mi < sizeof(modes)/sizeof(modes[0]); mi++)
    for (unsigned li = 0; li < sizeof(los)/sizeof(los[0]); li++)
    for (unsigned hi = 0; hi < sizeof(his)/sizeof(his[0]); hi++) {
        struct widget w;
        w.flags = flags[fi]; w.mode = modes[mi]; w.lo = los[li]; w.hi = his[hi];
        int e = widget_pick(&w), g = widget_pick_rs(&w);
        cases++;
        if (e != g && bad++ == 0) { fe = e; fg = g; ff = w.flags; fm = w.mode; }
    }
    /* path-coverage gate: every branch of the C reference must be exercised */
    int uncov = 0;
    for (int i = 0; i < %d; i++) if (!br[i]) { printf("  branch %%d UNCOVERED\n", i); uncov++; }
    if (uncov) { printf("STRUCTDIFF verdict=REFUSE (path coverage incomplete)\n"); return 2; }
    printf("STRUCTDIFF cases=%%llu bad=%%llu verdict=%%s\n",
           cases, bad, bad ? "DIVERGE" : "MATCH");
    if (bad) printf("  first mismatch: flags=%%u mode=%%u exp=%%d got=%%d\n", ff, fm, fe, fg);
    return bad ? 1 : 0;
}
""" % (nbranch, _real_size, flags_vals, nbranch)


_real_size = 16  # sizeof(struct widget): 4 scalars, computed & asserted below


def _run_scenario(tmp: str, name: str, cand_tmpl: str, cover_flags: bool,
                  mirror_rs: str, real_size: int) -> tuple[str, str]:
    """Compile C-ref + candidate + probe on the host, run, return (verdict, out)."""
    global _real_size
    _real_size = real_size
    d = os.path.join(tmp, name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "widget.h"), "w").write("#pragma once\n" + STRUCT_SRC)
    open(os.path.join(d, "cref.c"), "w").write(CREF)
    open(os.path.join(d, "cand.rs"), "w").write(cand_tmpl.format(MIRROR=mirror_rs))
    open(os.path.join(d, "probe.c"), "w").write(_probe(4, cover_flags))
    # rustc staticlib (no_std not needed on host; keep it a plain lib)
    r = subprocess.run(["rustc", "--crate-type=staticlib", "-O",
                        os.path.join(d, "cand.rs"), "-o", os.path.join(d, "libcand.a")],
                       capture_output=True, text=True, cwd=d)
    if r.returncode:
        return "BUILD_FAIL", r.stderr
    r = subprocess.run(["cc", "-O2", "-I", d, os.path.join(d, "probe.c"),
                        os.path.join(d, "cref.c"), os.path.join(d, "libcand.a"),
                        "-o", os.path.join(d, "run")], capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL", r.stderr
    r = subprocess.run([os.path.join(d, "run")], capture_output=True, text=True)
    out = r.stdout + r.stderr
    verdict = ("MATCH" if "verdict=MATCH" in out else
               "DIVERGE" if "verdict=DIVERGE" in out else
               "REFUSE" if "verdict=REFUSE" in out else "UNKNOWN")
    return verdict, out.strip()


def run_all(tmp: str) -> dict:
    # generate the mirror from the C struct (the real generator output)
    m = mirror.mirror(STRUCT_SRC, STRUCT_NAME)
    mirror_rs = m["rust"]
    real_size = m["size"]
    scenarios = [
        ("A_correct", CAND_CORRECT, True, "PASS(MATCH+covered)", "MATCH"),
        ("B_swap_arm", CAND_SWAP, True, "DIVERGE", "DIVERGE"),
        ("C_invert", CAND_INVERT, True, "DIVERGE", "DIVERGE"),
        ("D_undercover", CAND_CORRECT, False, "REFUSE", "REFUSE"),
    ]
    results = {}
    for name, tmpl, cover, _label, expect in scenarios:
        verdict, out = _run_scenario(tmp, name, tmpl, cover, mirror_rs, real_size)
        results[name] = {"verdict": verdict, "expect": expect,
                         "ok": verdict == expect, "out": out}
    return results, real_size


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        results, real_size = run_all(tmp)
    print(f"=== struct-driven branch MECHANISM proof (mirror size={real_size}) ===")
    allok = True
    for name, r in results.items():
        mark = "✓" if r["ok"] else "✗ UNEXPECTED"
        allok &= r["ok"]
        print(f"  {mark}  {name:14s} verdict={r['verdict']:8s} (expected {r['expect']})")
        for ln in r["out"].splitlines():
            print(f"        {ln}")
    print("\nMECHANISM PROOF:", "PASS — differential catches wrong translations; "
          "path-coverage gate refuses under-exercised sweeps" if allok
          else "FAIL — a scenario did not behave as expected")
    return 0 if allok else 1


if __name__ == "__main__":
    raise SystemExit(main())
