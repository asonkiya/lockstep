#!/usr/bin/env python3
"""Container-family ADT oracle — idiom #2: RBTREE (ordered-map ADT), boot-free.

Second idiom after LIST (proof.py), the way gpio_family added SET/CLR after RMW.
An rbtree's ABSTRACT semantics is an ordered map {key -> value}; its correctness as
a container is the sorted (key, value) contents, which is invariant under the tree's
balancing. So the ADT oracle compares the in-order (key, id) sequence extracted from
each side's structure — and the representation-independence is even starker than for
LIST: the C reference is an ordered-map structure and the Rust candidate is a
std BTreeMap, two totally different representations holding the same ADT.

Honest scope: the C reference here is a faithful C ordered-map (a sorted array
supporting insert-or-replace / erase / in-order walk) — the rbtree's ADT, NOT
lib/rbtree.c's balancing machinery. Because the oracle compares ADT CONTENTS (which
are balancing-invariant), the real lib/rbtree.c (rb_link_node/rb_insert_color/
rb_erase, walked by rb_first/rb_next) slots in unchanged at the oracle level in-kernel
— the abstract sequence it yields is identical. The rewrite target is RfL
`RBTree<K,V>` (insert/get/remove/iter).

Subject: rb_move(key, id, op) = op ? insert-or-replace(key,id) : erase(key), over a
workload with duplicate-key replaces and erases.

Scenarios (host cc + rustc, no boot):
  correct        -> MATCH
  wrong_key      -> DIVERGE (inserts under key+1)
  wrong_id       -> DIVERGE (right keyset, WRONG value -> a keyset-only check passes)
  dropped_erase  -> DIVERGE (stale entries)
  shallow_ok     -> DIVERGE *** op-count check says MATCH *** (right #ins/#del, shifted
                   keys: strictly stronger than an op-count check, like the LIST proof)
"""
from __future__ import annotations

import os
import subprocess
import tempfile

REF_C = r"""
#include <stdio.h>
#define CAP 64
static int keys[CAP], ids[CAP], cnt;
long c_nins, c_ndel;
void c_init(void){ cnt=0; c_nins=c_ndel=0; }
static int find(int k){ for(int i=0;i<cnt;i++) if(keys[i]==k) return i; return -1; }
static void ins(int k,int id){
    int p=find(k); if(p>=0){ ids[p]=id; return; }       /* insert-or-replace */
    int i=cnt++; while(i>0 && keys[i-1]>k){ keys[i]=keys[i-1]; ids[i]=ids[i-1]; i--; }
    keys[i]=k; ids[i]=id;
}
static void del(int k){ int p=find(k); if(p<0) return; for(int i=p;i<cnt-1;i++){ keys[i]=keys[i+1]; ids[i]=ids[i+1]; } cnt--; }
void c_move(int k,int id,int op){ if(op){ ins(k,id); c_nins++; } else { del(k); c_ndel++; } }
int c_seq(int *kb,int *ib,int cap){ int n=cnt<cap?cnt:cap; for(int i=0;i<n;i++){ kb[i]=keys[i]; ib[i]=ids[i]; } return n; }
"""

_CAND_HEAD = """
use std::collections::BTreeMap;
static mut M: Option<BTreeMap<i32, i32>> = None;
static mut NI: i64 = 0;
static mut NDL: i64 = 0;
#[no_mangle] pub extern "C" fn rs_init() { unsafe { M = Some(BTreeMap::new()); NI = 0; NDL = 0; } }
#[no_mangle] pub extern "C" fn rs_seq(kb: *mut i32, ib: *mut i32, cap: i32) -> i32 { unsafe {
    let m = M.as_ref().unwrap(); let mut n = 0usize;
    for (k, v) in m.iter() { if n >= cap as usize { break; } *kb.add(n)=*k; *ib.add(n)=*v; n+=1; }
    n as i32
}}
#[no_mangle] pub extern "C" fn rs_nins() -> i64 { unsafe { NI } }
#[no_mangle] pub extern "C" fn rs_ndel() -> i64 { unsafe { NDL } }
#[no_mangle] pub extern "C" fn rs_move(k: i32, id: i32, op: i32) { unsafe { let m = M.as_mut().unwrap();
"""

CANDS = {
    "correct":       "    if op != 0 { m.insert(k, id); NI += 1; } else { m.remove(&k); NDL += 1; }\n",
    "wrong_key":     "    if op != 0 { m.insert(k + 1, id); NI += 1; } else { m.remove(&k); NDL += 1; }\n",
    "wrong_id":      "    if op != 0 { m.insert(k, id + 1); NI += 1; } else { m.remove(&k); NDL += 1; }\n",
    "dropped_erase": "    if op != 0 { m.insert(k, id); NI += 1; } else { NDL += 1; }\n",
    # right op shape (#ins/#del), keys shifted by 100 -> op-count check matches, ADT diverges
    "shallow_ok":    "    if op != 0 { m.insert(k + 100, id); NI += 1; } else { m.remove(&(k + 100)); NDL += 1; }\n",
}

PROBE_C = r"""
#include <stdio.h>
#define CAP 64
extern void c_init(void); extern void c_move(int,int,int); extern int c_seq(int*,int*,int);
extern long c_nins, c_ndel;
extern void rs_init(void); extern void rs_move(int,int,int); extern int rs_seq(int*,int*,int);
extern long rs_nins(void); extern long rs_ndel(void);

int main(void){
    /* (key, id, op)  op=1 insert-or-replace, op=0 erase; includes dup-key replaces */
    static const int K[]  = {5,3,8,3,1,8,5,9,2,3,7,1,6,9};
    static const int I[]  = {50,30,80,31,10,0,0,90,20,0,70,11,60,91};
    static const int OP[] = {1,1,1,1,1,0,0,1,1,0,1,1,1,1};
    int n = sizeof(K)/sizeof(K[0]);
    c_init(); rs_init();
    for (int i=0;i<n;i++){ c_move(K[i],I[i],OP[i]); rs_move(K[i],I[i],OP[i]); }
    int ck[CAP], ci[CAP], rk[CAP], ri[CAP];
    int cn = c_seq(ck,ci,CAP), rn = rs_seq(rk,ri,CAP), adt_ok = (cn==rn);
    for (int j=0; adt_ok && j<cn; j++) if (ck[j]!=rk[j] || ci[j]!=ri[j]) adt_ok=0;
    int shape_ok = (c_nins==rs_nins() && c_ndel==rs_ndel());
    printf("CONTAINER_RBTREE shape_ok=%d adt_ok=%d clen=%d rlen=%d verdict=%s\n",
           shape_ok, adt_ok, cn, rn, adt_ok ? "MATCH" : "DIVERGE");
    return adt_ok ? 0 : 1;
}
"""


def run_scenario(tmp, name):
    d = os.path.join(tmp, name)
    os.makedirs(d, exist_ok=True)
    # PROBE_C references CAP; define it before use by prepending the macro.
    open(os.path.join(d, "probe.c"), "w").write(PROBE_C)
    open(os.path.join(d, "ref.c"), "w").write(REF_C)
    open(os.path.join(d, "cand.rs"), "w").write(_CAND_HEAD + CANDS[name] + "}}\n")
    r = subprocess.run(["rustc", "--edition", "2021", "-O", "--crate-type=staticlib",
                        os.path.join(d, "cand.rs"), "-o", os.path.join(d, "libcand.a")],
                       capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL", r.stderr, False
    r = subprocess.run(["cc", "-O2", os.path.join(d, "probe.c"), os.path.join(d, "ref.c"),
                        os.path.join(d, "libcand.a"), "-o", os.path.join(d, "run")],
                       capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL", r.stderr, False
    r = subprocess.run([os.path.join(d, "run")], capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    v = "MATCH" if "verdict=MATCH" in out else "DIVERGE" if "verdict=DIVERGE" in out else "UNKNOWN"
    return v, out, "shape_ok=1" in out


def run_all(tmp):
    expect = {"correct": "MATCH", "wrong_key": "DIVERGE", "wrong_id": "DIVERGE",
              "dropped_erase": "DIVERGE", "shallow_ok": "DIVERGE"}
    out = {}
    for name in CANDS:
        v, o, shape = run_scenario(tmp, name)
        out[name] = {"verdict": v, "out": o, "shape_ok": shape,
                     "expect": expect[name], "ok": v == expect[name]}
    return out


def main():
    with tempfile.TemporaryDirectory() as tmp:
        results = run_all(tmp)
    print("=== container-family ADT oracle (RBTREE idiom) MECHANISM proof ===")
    allok = True
    for name, r in results.items():
        allok &= r["ok"]
        mark = "✓" if r["ok"] else "✗ UNEXPECTED"
        extra = f"  [op-count check would say: {'MATCH' if r['shape_ok'] else 'DIVERGE'}]" if name == "shallow_ok" else ""
        print(f"  {mark}  {name:13s} ADT={r['verdict']:8s} (expect {r['expect']}){extra}")
    sh = results["shallow_ok"]
    stronger = sh["verdict"] == "DIVERGE" and sh["shape_ok"]
    print(f"\n  ADT oracle strictly stronger than op-count check: {stronger}")
    print("MECHANISM PROOF:", "PASS — ordered-map ADT comparison (C ordered-map vs Rust "
          "BTreeMap) catches wrong contents even when the op shape matches"
          if allok and stronger else "FAIL")
    return 0 if allok and stronger else 1


if __name__ == "__main__":
    raise SystemExit(main())
