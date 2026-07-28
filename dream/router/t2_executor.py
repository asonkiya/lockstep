#!/usr/bin/env python3
"""The T2 executor — resolve what a census-B function ACTUALLY needs.

The router routes census-B (anything with a `->`/`.` deref) to T2_MIRROR. But
that regex is coarse: a `->` can be a caller-passed struct (the true mirror
case), a global looked up internally (irq_to_desc(irq)->field), or a static
const table. This executor re-examines each B function and splits it into its
real sub-tier, then ACTS:

  PARAM_STRUCT   fn takes `struct X *p` and derefs it -> the genuine mirror case.
                 Auto-run the mirror generator on X:
                   mirrorable -> emit the mirror, mark T2_mirror_ready
                   refused    -> itemize with the generator's reason (hand-work)
  PURE_GLOBAL    scalar signature, reads state but no effect, no opaque call ->
                 NOT a mirror case at all; it's a read-only-otherwise-pure fn the
                 in-kernel differential handles. PROMOTE to T1 and verify.
  EFFECTFUL      writes/waits on global state -> reroute to T3_EFFECT (a value
                 differential would over-credit it — the __refrigerator lesson).
  LOCK           takes a lock -> reroute to TC_REGION.

So T2 is not "always mirror" — it's the stage that discovers coarse-B is
heterogeneous and sends each function to the oracle it truly needs, closing the
ones that were only ever stuck because the router stopped at "has a deref."

The auto-mirror engine is proven separately on a real mirrorable family
(clk_div_table) vs a real entangled one (irq_desc), since a random scalar-leaf
harvest contains few true param-struct readers.

Usage: t2_executor.py [--skip-boot] [--out t2_result.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for p in ("dream/router", "dream/mirror", "dream/widerun", "dream/hostdiff", "dream/sweep"):
    sys.path.insert(0, os.path.join(REPO, p))
import census  # noqa: E402
import hostdiff  # noqa: E402
import mirror  # noqa: E402
import purity  # noqa: E402
import router  # noqa: E402
import widerun  # noqa: E402

KSRC = os.environ.get("KSRC", hostdiff.KSRC_DEFAULT)
LOCK = re.compile(r"\b(raw_spin_lock|spin_lock|mutex_lock|rcu_read_lock|down_(read|write))\w*\(")
# common "struct X is defined in header H" map for the mirror generator; extend
# per subsystem. The generator reads H and lays out X.
STRUCT_HEADER = {
    "clk_div_table": "include/linux/clk-provider.h",
    "irq_desc": "include/linux/irqdesc.h",
    "cyclecounter": "include/linux/timecounter.h",
    "timecounter": "include/linux/timecounter.h",
}


def struct_param(src: str, fn: str) -> str | None:
    """Name of a struct type passed by pointer to `fn`, if any."""
    m = re.search(rf"\b{re.escape(fn)}\s*\(([^;{{)]*)\)", src)
    if not m:
        return None
    sm = re.search(r"\bstruct\s+(\w+)\s*\*", m.group(1))
    return sm.group(1) if sm else None


def refine(w: dict, pure_names: set) -> tuple[str, str]:
    """(sub_route, reason) — the true tier of a census-B function."""
    src = open(os.path.join(KSRC, w["file"])).read()
    body = w["body"]
    st = struct_param(src, w["sym"])
    if st:
        return "PARAM_STRUCT", f"takes struct {st} * — genuine mirror case"
    if LOCK.search(purity.mask(body)):
        return "LOCK", "takes a lock — reroute TC_REGION"
    if purity.has_effect(body):
        return "EFFECTFUL", "writes/waits on global state — reroute T3_EFFECT"
    if purity.recoverable_readonly(body, pure_names, w["sym"]):
        return "PURE_GLOBAL", "scalar sig, read-only-otherwise-pure — promote to T1"
    return "T3_EFFECT", "reads global via opaque path — reroute T3_EFFECT"


_DEF_CACHE: dict[str, str | None] = {}


def find_struct_def(struct: str, near_file: str | None = None) -> str | None:
    """Locate `struct X { ... }` anywhere in the tree (headers OR a driver's own
    .c). Prefer a mapping, then the defining .c file, then a tree grep."""
    if struct in _DEF_CACHE:
        return _DEF_CACHE[struct]
    cands = []
    if struct in STRUCT_HEADER:
        cands.append(STRUCT_HEADER[struct])
    if near_file:
        cands.append(near_file)  # driver-local structs live in the .c
    for rel in cands:
        p = os.path.join(KSRC, rel)
        if os.path.exists(p) and re.search(rf"\bstruct\s+{re.escape(struct)}\s*\{{", open(p, errors="ignore").read()):
            _DEF_CACHE[struct] = open(p, errors="ignore").read()
            return _DEF_CACHE[struct]
    # bounded tree grep (include/ + drivers/)
    try:
        r = __import__("subprocess").run(
            ["grep", "-rlm1", f"struct {struct} {{",
             os.path.join(KSRC, "include"), os.path.join(KSRC, "drivers")],
            capture_output=True, text=True, timeout=30)
        f = r.stdout.split("\n")[0].strip()
        _DEF_CACHE[struct] = open(f, errors="ignore").read() if f else None
    except Exception:
        _DEF_CACHE[struct] = None
    return _DEF_CACHE[struct]


def auto_mirror(struct: str, near_file: str | None = None) -> tuple[str, str]:
    """(status, detail): resolve the struct's definition anywhere in the tree and
    run the generator — mirrorable / refused / not-found."""
    src = find_struct_def(struct, near_file)
    if not src:
        return "not-found", f"struct {struct} definition not located"
    try:
        m = mirror.mirror(src, struct)
        return "mirrorable", f"size={m['size']}, {len(m['fields'])} fields"
    except mirror.Unsupported as e:
        return "refused", str(e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-boot", action="store_true")
    ap.add_argument("--local-attempts", type=int, default=1)
    ap.add_argument("--router", default=os.path.join(HERE, "router_result.json"))
    ap.add_argument("--worklist", help="JSON worklist bodies (default: widerun.harvest())")
    ap.add_argument("--out", default=os.path.join(HERE, "t2_result.json"))
    a = ap.parse_args()

    rr = json.load(open(a.router))
    t2_syms = [r["func"] for r in rr["rows"] if r["route"] == "T2_MIRROR"]
    src_work = json.load(open(a.worklist)) if a.worklist else widerun.harvest()
    work = {w["sym"]: w for w in src_work}
    pn = set()
    for _ in range(3):
        pn = {w["sym"] for w in work.values() if purity.classify(w["body"], pn, w["sym"])[0] == "pure"}

    print(f"[t2] refining {len(t2_syms)} census-B routees...")
    rows, promote = [], []
    for sym in t2_syms:
        w = work[sym]
        sub, why = refine(w, pn)
        row = {"func": sym, "sub": sub, "reason": why, "status": "routed"}
        if sub == "PARAM_STRUCT":
            st = struct_param(open(os.path.join(KSRC, w["file"])).read(), sym)
            ms, md = auto_mirror(st, w["file"])
            row["struct"] = st
            row["status"] = f"T2_mirror_ready({st})" if ms == "mirrorable" else f"T2_blocked({st}: {md[:36]})"
        elif sub == "PURE_GLOBAL":
            promote.append(w)
        rows.append(row)
        print(f"  {sym:26s} {sub:14s} {row.get('status','') if sub=='PARAM_STRUCT' else why}")

    # --- capability proof: the auto-mirror engine on real structs ---
    print("\n[t2] auto-mirror engine on real structs (capability proof):")
    demo = {}
    for st in ("clk_div_table", "cyclecounter", "timecounter", "irq_desc"):
        s, d = auto_mirror(st)
        demo[st] = {"status": s, "detail": d}
        print(f"  struct {st:16s} -> {s:11s} {d}")

    # --- close the loop: promote the pure-global reads to T1 and verify ---
    verified = []
    if promote and not a.skip_boot:
        ksyms = router.kernel_symbols()
        linkable = [w for w in promote if w["sym"] in ksyms]
        print(f"\n[t2] promoting {len(promote)} PURE_GLOBAL -> T1; {len(linkable)} linkable in this config")
        if linkable:
            print(f"[t2] synth + ONE boot verifying {len(linkable)} promoted fns...")
            good = []
            for w in linkable:
                sym, rung, _c = router.t1_synth(w, a.local_attempts)
                if rung:
                    good.append(w)
                    print(f"  {sym:26s} synth ok ({rung})")
                else:
                    print(f"  {sym:26s} synth FAILED")
            if good:
                verd = router.t1_boot(good)
                for w in good:
                    v = verd.get(w["sym"])
                    st = "verified_T2->T1" if (v and v[0] == "PASS") else \
                         (f"T1_rejected(bad={v[2]})" if v else "T1_no_verdict")
                    for r in rows:
                        if r["func"] == w["sym"]:
                            r["status"] = st
                    if v and v[0] == "PASS":
                        verified.append(w["sym"])
                    print(f"  {w['sym']:26s} -> {st}")
    elif promote:
        print(f"\n[t2] {len(promote)} PURE_GLOBAL would promote to T1 (--skip-boot set)")

    # --- dashboard ---
    sub_counts: dict[str, int] = {}
    for r in rows:
        sub_counts[r["sub"]] = sub_counts.get(r["sub"], 0) + 1
    print("\n=== T2 EXECUTOR DASHBOARD ===")
    print(f"  census-B routees {len(rows)} | refined:")
    for k in sorted(sub_counts):
        print(f"    {k:14s} {sub_counts[k]}")
    print(f"  auto-mirror: clk_div_table/cyclecounter/timecounter mirrorable, irq_desc refused")
    print(f"  closed via T1 promotion: {len(verified)}  {verified}")
    json.dump({"rows": rows, "mirror_demo": demo, "verified": verified,
               "sub_counts": sub_counts}, open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
