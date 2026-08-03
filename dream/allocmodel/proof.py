#!/usr/bin/env python3
"""Allocator-init oracle — MECHANISM proof (boot-free), the discipline every
oracle in this project started with (structdiff/efftrace/container all shipped a
proof.py before the harness).

The graph/alloc decomposition (dream/efftrace census) found the 5,989-fn
"graph/alloc" mass is 51% raw pointer-chain (the C-forever floor), 26% alloc-only,
20% list. The alloc-only slice — `p = kzalloc(sizeof(*p)); if (!p) return NULL;
p->f = ...; return p;` — is unreachable by the container oracle (no list op) and
by effect-trace (allocation is a forbidden token). 63 of them are gate-clean in
kernel+mm+lib. This oracle targets exactly that pattern.

The model: allocation is a FRESH ARENA SLOT. kzalloc/kcalloc -> a zeroed slot;
kmalloc -> a slot (zeroed on both sides so init-then-read agrees); kfree ->
retire. The function's field initializations land in that slot's cells; the
returned pointer maps to the slot id (NULL -> -1). The differential runs the
REAL C (kzalloc #defined to a bump-allocator over a host arena) against a Rust
model that produces the same fresh-slot sequence, and compares — after every
call — the allocated object's fields AND the returned id.

Soundness scope (flagged, like the container oracle's alloc_stripped): kzalloc
ALWAYS SUCCEEDS in the model, so the verdict covers the SUCCESS path's state
transition; the allocation-FAILURE path (`if (!p) return -ENOMEM`) is the
allocator-fault half, not modeled here. The init-transition claim is exact.

Scenarios (host cc + rustc, no boot):
  correct      -> MATCH
  drop_flag    -> DIVERGE:state (forgets p->flag = 1; the fresh slot's cell
                  stays 0 -> a return-only oracle that only checks the pointer
                  MISSES this; the field differential catches it)
  wrong_y      -> DIVERGE:state (y = b instead of b + 1)
  no_init      -> DIVERGE:state (returns the fresh object uninitialized — the
                  over-credit case: a valid non-null pointer returned, but the
                  object's contents are wrong)
  wrong_count  -> DIVERGE:ret (allocates twice -> the returned slot id differs;
                  the fresh-slot sequence is observable)
"""
from __future__ import annotations

import os
import subprocess
import tempfile

# Host arena + bump allocator standing in for the slab. kzalloc zeroes; kmalloc
# is modeled zeroed too (both sides) so init-then-use agrees. kfree retires.
ALLOC_H = r"""
#include <stddef.h>
#include <string.h>
#define GFP_KERNEL 0
struct obj { int x; int y; int flag; };
extern void *am_alloc(unsigned long sz, int zero);
extern void am_retire(void *p);
extern void am_reset(void);
extern int am_slot(void *p);
extern int am_field(int id, int f);
#define kzalloc(sz, fl) am_alloc((sz), 1)
#define kcalloc(n, sz, fl) am_alloc((n)*(sz), 1)
#define kmalloc(sz, fl) am_alloc((sz), 1)   /* zeroed on both sides (see proof) */
#define kfree(p) am_retire((void*)(p))
"""

# ref.c OWNS the arena + allocator definitions (one TU); probe.c only externs.
REF_C = r"""
#include "alloc.h"
static struct obj AM_ARENA[64];
static int AM_NEXT;
static int AM_RETLOG[256]; static int AM_NRET;
void *am_alloc(unsigned long sz, int zero){
    (void)sz;
    if (AM_NEXT >= 64) return 0;            /* arena exhausted */
    struct obj *p = &AM_ARENA[AM_NEXT++];
    if (zero) memset(p, 0, sizeof(*p));
    return p;
}
void am_retire(void *p){
    if (!p) return;
    int id = (int)((struct obj*)p - AM_ARENA);
    if (AM_NRET < 256) AM_RETLOG[AM_NRET++] = id;
}
void am_reset(void){ AM_NEXT = 0; AM_NRET = 0; memset(AM_ARENA, 0, sizeof(AM_ARENA)); }
int am_slot(void *p){ return p ? (int)((struct obj*)p - AM_ARENA) : -1; }
int am_field(int id, int f){
    struct obj *p = &AM_ARENA[id];
    return f==0 ? p->x : f==1 ? p->y : p->flag;
}
struct obj *mk(int a, int b){
    struct obj *p = kzalloc(sizeof(*p), GFP_KERNEL);
    if (!p)
        return 0;
    p->x = a;
    p->y = b + 1;
    p->flag = 1;
    return p;
}
"""

PROBE_C = r"""
#include <stdio.h>
#include "alloc.h"
extern void am_reset(void); extern struct obj *mk(int,int);
extern int am_slot(void*); extern int am_field(int,int);
extern void rs_reset(void); extern long rs_mk(long,long);
extern long rs_field(long,long);

int main(void){
    static const int A[] = {0,1,2,7,-1,3,5,100,0,-8,255,4};
    static const int B[] = {0,-1,1,2,7,-8,100,3,255,-2,5,0};
    int n = sizeof(A)/sizeof(A[0]);
    am_reset(); rs_reset();
    for (int k=0;k<n;k++){
        struct obj *cp = mk(A[k], B[k]);
        long cid = am_slot(cp);
        long rid = rs_mk((long)A[k], (long)B[k]);
        if (cid != rid){ printf("ALLOCMODEL verdict=DIVERGE:ret call=%d c=%ld r=%ld\n", k, cid, rid); return 1; }
        if (cid >= 0){
            for (int f=0; f<3; f++){
                long cv = am_field(cid, f), rv = rs_field(rid, (long)f);
                if (cv != rv){ printf("ALLOCMODEL verdict=DIVERGE:state call=%d field=%d c=%ld r=%ld\n", k, f, cv, rv); return 1; }
            }
        }
    }
    printf("ALLOCMODEL verdict=MATCH calls=%d\n", n);
    return 0;
}
"""

# Rust model: fresh-slot allocator mirroring am_alloc; field cells per slot.
_CAND_HEAD = """#![allow(non_snake_case, dead_code, static_mut_refs, unused_unsafe, unused_variables)]
const NF: usize = 3;
static mut SLOTS: Vec<[i64; NF]> = Vec::new();   // one [x,y,flag] per allocated obj
#[no_mangle] pub extern "C" fn rs_reset() { unsafe { SLOTS = Vec::new(); } }
#[no_mangle] pub extern "C" fn rs_field(id: i64, f: i64) -> i64 { unsafe { SLOTS[id as usize][f as usize] } }
fn alloc() -> i64 { unsafe {   // fresh zeroed slot; -1 if arena exhausted (>=64)
    if SLOTS.len() >= 64 { return -1; }
    SLOTS.push([0; NF]); (SLOTS.len() - 1) as i64
}}
fn set(id: i64, f: usize, v: i64) { unsafe { SLOTS[id as usize][f] = v; } }
#[no_mangle] pub extern "C" fn rs_mk(a: i64, b: i64) -> i64 {
"""

CANDS = {
    "correct": """
    let p = alloc();
    if p < 0 { return -1; }
    set(p, 0, a);
    set(p, 1, b + 1);
    set(p, 2, 1);
    p
""",
    "drop_flag": """
    let p = alloc();
    if p < 0 { return -1; }
    set(p, 0, a);
    set(p, 1, b + 1);
    p
""",
    "wrong_y": """
    let p = alloc();
    if p < 0 { return -1; }
    set(p, 0, a);
    set(p, 1, b);
    set(p, 2, 1);
    p
""",
    "no_init": """
    let p = alloc();
    if p < 0 { return -1; }
    p
""",
    # allocates twice -> the returned slot id is off-by-one vs the C sequence
    "wrong_count": """
    let _ = alloc();
    let p = alloc();
    if p < 0 { return -1; }
    set(p, 0, a);
    set(p, 1, b + 1);
    set(p, 2, 1);
    p
""",
}


def run_scenario(tmp, name):
    d = os.path.join(tmp, name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "alloc.h"), "w").write(ALLOC_H)
    open(os.path.join(d, "ref.c"), "w").write(REF_C)
    open(os.path.join(d, "probe.c"), "w").write(PROBE_C)
    open(os.path.join(d, "cand.rs"), "w").write(_CAND_HEAD + CANDS[name] + "}\n")
    r = subprocess.run(["rustc", "--edition", "2021", "-O", "--crate-type=staticlib",
                        os.path.join(d, "cand.rs"), "-o", os.path.join(d, "libcand.a")],
                       capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL_RS", r.stderr[-800:]
    r = subprocess.run(["cc", "-O2", "-w", "-I", d, os.path.join(d, "probe.c"),
                        os.path.join(d, "ref.c"), os.path.join(d, "libcand.a"),
                        "-o", os.path.join(d, "run")], capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL_C", r.stderr[-800:]
    r = subprocess.run([os.path.join(d, "run")], capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    import re
    m = re.search(r"verdict=([A-Z_]+(?::[a-z]+)?)", out)
    return (m.group(1) if m else "UNKNOWN"), out


def run_all(tmp):
    expect = {"correct": "MATCH", "drop_flag": "DIVERGE:state",
              "wrong_y": "DIVERGE:state", "no_init": "DIVERGE:state",
              "wrong_count": "DIVERGE:ret"}
    return {name: {"verdict": (v := run_scenario(tmp, name))[0], "out": v[1],
                   "expect": expect[name], "ok": v[0] == expect[name]}
            for name in CANDS}


def main():
    with tempfile.TemporaryDirectory() as tmp:
        results = run_all(tmp)
    print("=== allocator-init oracle MECHANISM proof ===")
    allok = True
    for name, r in results.items():
        allok &= r["ok"]
        mark = "✓" if r["ok"] else "✗ UNEXPECTED"
        print(f"  {mark}  {name:12s} -> {r['verdict']:14s} (expect {r['expect']})  [{r['out'][:60]}]")
    print("MECHANISM PROOF:", "PASS — kzalloc modeled as fresh arena slot; init "
          "field-writes + returned id differential catches wrong contents even "
          "when a valid pointer is returned (over-credit)" if allok else "FAIL")
    return 0 if allok else 1


if __name__ == "__main__":
    raise SystemExit(main())
