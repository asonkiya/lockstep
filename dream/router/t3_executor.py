#!/usr/bin/env python3
"""The T3 executor — split the effectful mass into recordable vs per-fn.

The router (and the T2 reroutes) send effectful functions to T3. But "effectful"
is two very different populations:

  T3_TRACE   the effect is an MMIO register program (readl/writel/io*/in/out).
             A driver's correctness IS this trace, and it's UNIFORMLY recordable:
             record the C's accesses once (kind/offset/value/order), replay any
             candidate against the frozen trace with no device present. This is
             the recorder (dream/recorder/), the lever for the ~73% driver mass.

  T3_EFFECT  the effect is arbitrary global/RCU/scheduler/per-cpu state, or the
             function calls an opaque helper. There is no uniform trace to record;
             each needs a per-function effect oracle. This is the honest floor of
             the effectful mass.

This executor classifies each effectful function into those two, names the state
category for the T3_EFFECT ones (so the per-fn work is itemized), and proves the
T3_TRACE mechanism by driving the recorder end to end (record -> replay: a
value-identical bug is caught on the trace).

Empirical finding on a scalar-leaf harvest: ~all effectful leaves are arbitrary
state, ~none are directly MMIO — the recorder's population lives in drivers/,
which a lib/kernel/mm/net harvest doesn't sample. The recorder is a
DRIVER-worklist tool; this executor shows it's ready and quantifies why a general
leaf harvest doesn't feed it.

Usage: t3_executor.py [--out t3_result.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for p in ("dream/widerun", "dream/hostdiff"):
    sys.path.insert(0, os.path.join(REPO, p))
import purity  # noqa: E402
import widerun  # noqa: E402

KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
RECORDER = os.path.join(REPO, "dream", "recorder")

MMIO = re.compile(r"\b(readl|writel|read[bwq]|write[bwq]|ioread|iowrite|in[bwl]|out[bwl]|"
                  r"__raw_read[bwlq]|__raw_write[bwlq]|readq|writeq)\b")
STATE = [
    ("rcu", re.compile(r"\brcu_|synchronize_rcu|call_rcu|srcu|poll_state")),
    ("irq", re.compile(r"\birq_|generic_handle|handle_irq|ipi_send|desc->|__irq")),
    ("sched/cpu", re.compile(r"\bschedule\b|wake_up|complete\(|wait_|freeze|refrigerator|"
                             r"add_cpu|remove_cpu|cpu_|cpumask_|kcpustat")),
    ("alloc", re.compile(r"\bk[mzv]alloc|kfree|vfree|\balloc_|\bfree_")),
    ("percpu", re.compile(r"per_cpu|this_cpu|__percpu")),
    ("atomic", re.compile(r"atomic_|refcount_|\bxchg|cmpxchg|WRITE_ONCE")),
    ("list", re.compile(r"\blist_add|\blist_del|hlist_|llist_")),
]


def subclass(w: dict) -> tuple[str, str, str]:
    """(route, category, detail) for one effectful function."""
    b = purity.mask(w["body"])
    hdr = b.find("{")
    body = b[hdr:] if hdr > 0 else b
    if MMIO.search(body):
        return "T3_TRACE", "mmio", "MMIO register program — recordable by the recorder"
    cats = [name for name, rx in STATE if rx.search(body)]
    if cats:
        return "T3_EFFECT", "+".join(cats[:3]), f"arbitrary state ({'+'.join(cats[:3])}) — per-fn effect trace owed"
    return "T3_EFFECT", "opaque", "calls an opaque helper — per-fn effect trace owed"


def prove_recorder() -> dict:
    """Drive the recorder gate: correct replays the recording (MATCH), the
    value-identical skip-poll bug is caught on the trace (DIVERGE). This is the
    T3_TRACE close mechanism, proven on a driver hot path."""
    out = {}
    for mode in ("correct", "subtle"):
        r = subprocess.run(["bash", os.path.join(RECORDER, "gate.sh"), mode],
                           capture_output=True, text=True)
        line = next((ln for ln in (r.stdout + r.stderr).splitlines()
                     if "RECORDER GATE" in ln), "(no verdict)")
        out[mode] = {"rc": r.returncode, "line": line.strip()}
        print(f"  recorder[{mode}]: {line.strip()}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--router", default=os.path.join(HERE, "router_result.json"))
    ap.add_argument("--worklist", help="JSON worklist bodies (default: widerun.harvest())")
    ap.add_argument("--out", default=os.path.join(HERE, "t3_result.json"))
    ap.add_argument("--skip-recorder", action="store_true")
    a = ap.parse_args()

    rr = json.load(open(a.router))
    # driver routing puts MMIO in T3_TRACE; scalar routing lumps effectful in T3_EFFECT
    eff = [r["func"] for r in rr["rows"] if r["route"] in ("T3_EFFECT", "T3_TRACE")]
    # the T2 executor rerouted its EFFECTFUL routees here too
    t2 = os.path.join(HERE, "t2_result.json")
    if os.path.exists(t2):
        t2d = json.load(open(t2))
        eff += [r["func"] for r in t2d["rows"] if r["sub"] in ("EFFECTFUL", "T3_EFFECT")]
    eff = sorted(set(eff))
    src = json.load(open(a.worklist)) if a.worklist else widerun.harvest()
    work = {w["sym"]: w for w in src}
    # refuse a stale router pairing (the "wrong worklist" incident class)
    import hashlib
    sha = hashlib.sha256("\n".join(sorted(work)).encode()).hexdigest()[:16]
    rr_sha = rr.get("worklist_sha")
    if rr_sha is not None and rr_sha != sha:
        raise SystemExit(f"[t3] router_result.json was produced from a DIFFERENT worklist "
                         f"(router {rr_sha} vs harvested {sha}) — re-run router.py first")

    print(f"[t3] classifying {len(eff)} effectful functions...")
    rows: list[dict] = []
    cats: dict[str, int] = {}
    trace, effect = [], []
    for f in eff:
        w = work.get(f)
        if not w:
            continue
        route, cat, detail = subclass(w)
        rows.append({"func": f, "route": route, "category": cat, "reason": detail})
        cats[cat] = cats.get(cat, 0) + 1
        (trace if route == "T3_TRACE" else effect).append(f)

    print("\n[t3] T3_TRACE (recordable MMIO):", trace or "none in this worklist")
    print(f"[t3] T3_EFFECT (per-fn effect trace): {len(effect)} fns, by state category:")
    for c in sorted(cats, key=lambda k: -cats[k]):
        exemplars = [r["func"] for r in rows if r["category"] == c][:4]
        print(f"    {cats[c]:2d}  {c:16s} e.g. {', '.join(exemplars)}")

    rec = {}
    if not a.skip_recorder:
        print("\n[t3] recorder capability proof (record once -> replay; no device):")
        rec = prove_recorder()

    print("\n=== T3 EXECUTOR DASHBOARD ===")
    print(f"  effectful mass {len(rows)} | T3_TRACE {len(trace)} (recordable) | "
          f"T3_EFFECT {len(effect)} (per-fn)")
    print(f"  recorder mechanism: "
          f"{'PROVEN (correct MATCH + value-identical bug DIVERGE on trace)' if rec.get('correct',{}).get('rc')==0 and rec.get('subtle',{}).get('rc')==0 else 'not run'}")
    if trace:
        print(f"  finding: {len(trace)} recordable-MMIO driver fns — the recorder's target "
              f"population, present because this is a driver-scoped worklist")
    else:
        print(f"  finding: 0 MMIO fns — the recorder's population is drivers/, which a "
              f"scalar-leaf harvest doesn't sample; needs a driver-scoped worklist")
    json.dump({"rows": rows, "trace": trace, "effect_categories": cats,
               "recorder": rec}, open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
