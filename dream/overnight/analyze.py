"""Analyze — turn the recorder census into a prioritized next-increment backlog,
and take a tree-wide purity/tier census. Fast, deterministic, no toolchain.

Two reports:
  1. refusal taxonomy: the raw refusal strings clustered into actionable buckets
     (control-flow, non-clean access, guards, iomem-locals, ...), ranked by count,
     each = a concrete extractor increment with its addressable-function count and
     an example. Plus the harness-anomaly (emit-gap) breakdown — the cheap win.
  2. tree-wide census: harvest exported scalar leaves across many dirs, classify
     purity + census tier, report the distribution (the T0/T1/T2/T3 denominators).

Usage: analyze.py [--census PATH] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for p in ("dream/widerun", "dream/sweep"):
    sys.path.insert(0, os.path.join(REPO, p))

BUCKETS = [
    ("control flow (if/for/while/switch/goto)", lambda r: r.startswith("control flow")),
    ("nested-brace scope (guard/scoped_guard/block)", lambda r: "nested brace" in r or r.startswith("unmodellable statement: 'guard")),
    ("non-clean access (computed/opaque offset or value)", lambda r: r.startswith("non-clean")),
    ("iomem local / base-alias (unresolvable base)", lambda r: "void __iomem" in r or "base-alias" in r),
    ("struct-typed local / plumbing", lambda r: "struct " in r),
    ("unresolvable identifier (macro/global offset)", lambda r: "unresolvable identifier" in r or "unresolved" in r),
    ("comment / preprocessor noise", lambda r: r.startswith("unmodellable statement: '/*") or "#" in r),
]


def refusal_taxonomy(census: str, out: str) -> dict:
    recs = [json.loads(l) for l in open(census)] if os.path.exists(census) else []
    ref = [r for r in recs if r.get("verdict") == "REFUSED"]
    anom = [r for r in recs if r.get("verdict") == "HARNESS_ANOMALY"]
    closed = [r for r in recs if r.get("verdict") == "CLOSED"]
    counts, examples, matched = {}, {}, set()
    for r in ref:
        reason = r.get("reason", "?")
        for name, pred in BUCKETS:
            if pred(reason):
                counts[name] = counts.get(name, 0) + 1
                examples.setdefault(name, f"{r['file']}:{r['fn']}")
                matched.add(id(r))
                break
    other = sum(1 for r in ref if id(r) not in matched)
    if other:
        counts["other / uncategorized"] = other
    # emitter-gap: extracted but the correct candidate didn't compile
    emit_gap = {}
    for r in anom:
        emit_gap[r.get("detail", "?")] = emit_gap.get(r.get("detail", "?"), 0) + 1
    report = {
        "register_fns": len(recs), "closed": len(closed), "refused": len(ref),
        "harness_anomaly_emit_gap": len(anom),
        "refusal_buckets": dict(sorted(counts.items(), key=lambda x: -x[1])),
        "bucket_examples": examples,
        "emit_gap_breakdown": emit_gap,
        "next_increment_ranked": [
            {"increment": name, "addressable_fns": c,
             "pct_of_register_mass": round(100 * c / max(1, len(recs)), 1)}
            for name, c in sorted(counts.items(), key=lambda x: -x[1])[:6]
        ],
    }
    json.dump(report, open(os.path.join(out, "refusal_taxonomy.json"), "w"), indent=1)
    return report


def tree_census(out: str) -> dict:
    import census as cen
    import purity
    import widerun
    widerun.DIRS = ["lib", "kernel", "mm", "crypto", "block", "fs", "net/core",
                    "net/ipv4", "sound/core", "security", "ipc", "drivers/gpio",
                    "drivers/clk", "drivers/pci", "drivers/rtc", "drivers/base"]
    work = widerun.harvest()
    tiers = {"A": 0, "B": 0, "C": 0, "D": 0}
    for w in work:
        tiers[cen.classify(w["body"])] += 1
    pn = set()
    for _ in range(3):
        pn = {w["sym"] for w in work if purity.classify(w["body"], pn, w["sym"])[0] == "pure"}
    rep = {"harvested_scalar_leaves": len(work), "census_tiers": tiers,
           "pure_fraction": round(100 * len(pn) / max(1, len(work)), 1),
           "pure_count": len(pn)}
    json.dump(rep, open(os.path.join(out, "tree_census.json"), "w"), indent=1)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--census", default=os.path.join(REPO, "dream/overnight/reports/recorder_census/sweep.jsonl"))
    ap.add_argument("--out", default=os.path.join(HERE, "reports", "analysis"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    print("[analyze] refusal taxonomy...", flush=True)
    tax = refusal_taxonomy(a.census, a.out)
    print(f"  buckets: {tax['refusal_buckets']}", flush=True)
    print("[analyze] tree-wide purity/tier census...", flush=True)
    tc = tree_census(a.out)
    print(f"  {tc}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
