#!/usr/bin/env python3
"""Container-family ADT oracle — Build-Order Step 1: prove the MECHANISM (boot-free).

The top tail lever (dream/efftrace/UNBOUNDED_RESEARCH.md): container ops are a finite
closed vocabulary backed by shared verified cores (lib/list.h, lib/rbtree.c) with
Rust-for-Linux abstractions (List, RBTree) as the rewrite targets. A function mutating
an intrusive list is "unbounded" only to a byte-level oracle; at the ADT level (a list
= an ordered sequence of node ids) each op is O(1) and the abstract state is bounded
AND comparable across representations.

So the oracle is REPRESENTATION-INDEPENDENT: the C reference runs the REAL list.h ops
on real `list_head` nodes; the Rust candidate runs the same logic against a Vec-modeled
`List` ADT (the RfL surface). The oracle extracts each side's abstract per-list
id-sequence and compares — certifying the Rust holds the same ADT CONTENTS as the C,
not the same pointers.

Subject: move(id, cond) = del(node) from wherever it is; cond -> front of list 1 else
tail of list 2. Driven by a workload that migrates 8 nodes across 3 lists.

Scenarios (host cc + rustc, no boot):
  correct      -> MATCH
  wrong_list   -> DIVERGE (adds to list 2 where it should add to list 1)
  wrong_order  -> DIVERGE (front where it should be tail -> ADT sequence order differs)
  dropped_del  -> DIVERGE (node ends in two lists)
  wrong_node   -> DIVERGE (operates on the wrong id)
  shallow_ok   -> DIVERGE *** and an op-count check says MATCH ***  (right op shape,
                 right lists/order, WRONG nodes: the ADT oracle is strictly stronger
                 than a "did it call the right APIs the right number of times" check)
"""
from __future__ import annotations

import os
import subprocess
import tempfile

CADT_H = r"""
#ifndef CADT_H
#define CADT_H
struct list_head { struct list_head *next, *prev; };
static inline void INIT_LIST_HEAD(struct list_head *h){ h->next=h; h->prev=h; }
static inline void __list_add(struct list_head *n, struct list_head *p, struct list_head *x){
    x->prev=n; n->next=x; n->prev=p; p->next=n; }
static inline void list_add(struct list_head *n, struct list_head *h){ __list_add(n,h,h->next); }
static inline void list_add_tail(struct list_head *n, struct list_head *h){ __list_add(n,h->prev,h); }
static inline void list_del(struct list_head *e){ e->prev->next=e->next; e->next->prev=e->prev; e->next=e->prev=0; }
#endif
"""

# C reference: the REAL list.h ops on real list_head nodes (link is the first member,
# so a list_head* IS the node* — container_of at offset 0). c_seq walks the chain to
# extract the ADT sequence.
REF_C = r"""
#include "cadt.h"
#define NN 8
#define NL 3
struct node { struct list_head link; int id; };
static struct node nodes[NN];
static struct list_head lists[NL];
long c_ndel, c_nadd;
void c_init(void){
    c_ndel=c_nadd=0;
    for (int i=0;i<NL;i++) INIT_LIST_HEAD(&lists[i]);
    for (int i=0;i<NN;i++){ nodes[i].id=i; list_add_tail(&nodes[i].link,&lists[0]); }
}
void c_move(int id, int cond){
    list_del(&nodes[id].link); c_ndel++;
    if (cond) list_add(&nodes[id].link,&lists[1]);   /* front of list 1 */
    else      list_add_tail(&nodes[id].link,&lists[2]); /* tail of list 2 */
    c_nadd++;
}
int c_seq(int L, int *buf, int cap){
    int n=0; struct list_head *p;
    for (p=lists[L].next; p!=&lists[L] && n<cap; p=p->next) buf[n++]=((struct node*)p)->id;
    return n;
}
"""

# Rust candidate: the same logic against a Vec-modeled List ADT (the RfL List surface:
# push_back == push, push_front == insert(0), remove). rs_seq exposes the ADT sequence.
_CAND_HEAD = """
static mut LISTS: [Vec<u32>; 3] = [Vec::new(), Vec::new(), Vec::new()];
static mut ND: i64 = 0;
static mut NA: i64 = 0;
#[no_mangle] pub extern "C" fn rs_init() { unsafe {
    ND = 0; NA = 0;
    for l in LISTS.iter_mut() { l.clear(); }
    for i in 0..8u32 { LISTS[0].push(i); }
}}
#[no_mangle] pub extern "C" fn rs_seq(l: i32, buf: *mut i32, cap: i32) -> i32 { unsafe {
    let v = &LISTS[l as usize];
    let n = core::cmp::min(v.len(), cap as usize);
    for i in 0..n { *buf.add(i) = v[i] as i32; }
    n as i32
}}
#[no_mangle] pub extern "C" fn rs_ndel() -> i64 { unsafe { ND } }
#[no_mangle] pub extern "C" fn rs_nadd() -> i64 { unsafe { NA } }
fn del(id: u32) { unsafe {
    for l in LISTS.iter_mut() { if let Some(p) = l.iter().position(|&x| x == id) { l.remove(p); } }
    ND += 1;
}}
#[no_mangle] pub extern "C" fn rs_move(id: i32, cond: i32) { unsafe {
"""

# each scenario supplies the body of rs_move (between the unsafe { and the closing }})
CANDS = {
    "correct": """
    let id = id as u32; del(id);
    if cond != 0 { LISTS[1].insert(0, id); } else { LISTS[2].push(id); }
    NA += 1;
""",
    "wrong_list": """
    let id = id as u32; del(id);
    if cond != 0 { LISTS[2].insert(0, id); } else { LISTS[2].push(id); }
    NA += 1;
""",
    "wrong_order": """
    let id = id as u32; del(id);
    if cond != 0 { LISTS[1].insert(0, id); } else { LISTS[2].insert(0, id); }
    NA += 1;
""",
    "dropped_del": """
    let id = id as u32;
    if cond != 0 { LISTS[1].insert(0, id); } else { LISTS[2].push(id); }
    NA += 1;
""",
    "wrong_node": """
    let id = ((id + 1) % 8) as u32; del(id);
    if cond != 0 { LISTS[1].insert(0, id); } else { LISTS[2].push(id); }
    NA += 1;
""",
    # right op SHAPE (1 del + 1 add), right list + order, WRONG nodes (rotated by 3):
    # op-count check matches, ADT contents diverge.
    "shallow_ok": """
    let id = ((id + 3) % 8) as u32; del(id);
    if cond != 0 { LISTS[1].insert(0, id); } else { LISTS[2].push(id); }
    NA += 1;
""",
}

PROBE_C = r"""
#include <stdio.h>
#include "cadt.h"
extern void c_init(void); extern void c_move(int,int); extern int c_seq(int,int*,int);
extern long c_ndel, c_nadd;
extern void rs_init(void); extern void rs_move(int,int); extern int rs_seq(int,int*,int);
extern long rs_ndel(void); extern long rs_nadd(void);

int main(void){
    static const int MID[] = {0,1,2,3,0,2,5,1,7,3,4,6,0,5};
    static const int MC [] = {1,0,1,0,0,0,1,1,0,1,1,0,1,0};
    int n = sizeof(MID)/sizeof(MID[0]);
    c_init(); rs_init();
    for (int i=0;i<n;i++){ c_move(MID[i],MC[i]); rs_move(MID[i],MC[i]); }
    int cb[16], rb[16], adt_ok=1, first=-1;
    for (int L=0; L<3; L++){
        int cn=c_seq(L,cb,16), rn=rs_seq(L,rb,16);
        int eq = (cn==rn);
        for (int j=0; eq && j<cn; j++) if (cb[j]!=rb[j]) eq=0;
        if (!eq){ adt_ok=0; if (first<0) first=L; }
    }
    int shape_ok = (c_ndel==rs_ndel() && c_nadd==rs_nadd());
    printf("CONTAINER_ADT shape_ok=%d adt_ok=%d firstdifflist=%d verdict=%s\n",
           shape_ok, adt_ok, first, adt_ok ? "MATCH" : "DIVERGE");
    return adt_ok ? 0 : 1;
}
"""


def run_scenario(tmp, name):
    d = os.path.join(tmp, name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "cadt.h"), "w").write(CADT_H)
    open(os.path.join(d, "ref.c"), "w").write(REF_C)
    open(os.path.join(d, "probe.c"), "w").write(PROBE_C)
    open(os.path.join(d, "cand.rs"), "w").write(_CAND_HEAD + CANDS[name] + "}}\n")
    r = subprocess.run(["rustc", "--edition", "2021", "-O", "--crate-type=staticlib",
                        os.path.join(d, "cand.rs"), "-o", os.path.join(d, "libcand.a")],
                       capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL", r.stderr, False
    r = subprocess.run(["cc", "-O2", "-I", d, os.path.join(d, "probe.c"), os.path.join(d, "ref.c"),
                        os.path.join(d, "libcand.a"), "-o", os.path.join(d, "run")],
                       capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL", r.stderr, False
    r = subprocess.run([os.path.join(d, "run")], capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    v = "MATCH" if "verdict=MATCH" in out else "DIVERGE" if "verdict=DIVERGE" in out else "UNKNOWN"
    shape_ok = "shape_ok=1" in out
    return v, out, shape_ok


def run_all(tmp):
    expect = {"correct": "MATCH", "wrong_list": "DIVERGE", "wrong_order": "DIVERGE",
              "dropped_del": "DIVERGE", "wrong_node": "DIVERGE", "shallow_ok": "DIVERGE"}
    results = {}
    for name in CANDS:
        v, out, shape_ok = run_scenario(tmp, name)
        results[name] = {"verdict": v, "expect": expect[name], "shape_ok": shape_ok,
                         "ok": v == expect[name], "out": out}
    return results


def main():
    with tempfile.TemporaryDirectory() as tmp:
        results = run_all(tmp)
    print("=== container-family ADT oracle (LIST idiom) MECHANISM proof ===")
    allok = True
    for name, r in results.items():
        allok &= r["ok"]
        mark = "✓" if r["ok"] else "✗ UNEXPECTED"
        extra = f"  [op-count check would say: {'MATCH' if r['shape_ok'] else 'DIVERGE'}]" if name == "shallow_ok" else ""
        print(f"  {mark}  {name:12s} ADT={r['verdict']:8s} (expect {r['expect']}){extra}")
    sh = results["shallow_ok"]
    stronger = sh["verdict"] == "DIVERGE" and sh["shape_ok"]
    print(f"\n  ADT oracle strictly stronger than op-count check: {stronger}")
    print("MECHANISM PROOF:", "PASS — representation-independent ADT comparison catches "
          "wrong contents even when the op shape matches"
          if allok and stronger else "FAIL")
    return 0 if allok and stronger else 1


if __name__ == "__main__":
    raise SystemExit(main())
