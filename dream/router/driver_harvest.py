#!/usr/bin/env python3
"""Driver-scoped harvester — feed the router the population it was missing.

The scalar-leaf harvester (widerun) keeps only EXPORT_SYMBOL functions with
scalar signatures — which is exactly why the router's mirror (T2) and recorder
(T3) buckets came up empty: struct-reading and MMIO functions have struct-pointer
params and aren't exported. This harvester is the opposite: it walks driver dirs,
takes EVERY column-0 function definition (exported or static), and KEEPS the
struct-param and MMIO ones — the mirror and recorder populations.

It emits a router-compatible worklist [{sym,file,ret,args,body}] and a tier
distribution. A driver census confirmed the payoff: drivers/{gpio,ptp,clk} hold
~3,580 tier-B struct readers and ~596 MMIO functions — vs 0 of each in a
scalar-leaf sweep.

To keep a router run bounded (T0 ladder-synth is the only expensive step, and it
only fires on the pure subset), the worklist is stratified-capped: all tiers are
represented, the synth-triggering pure subset is capped small.

Usage: driver_harvest.py [--dirs d1,d2] [--cap-pure N] [--out driver_worklist.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "dream/sweep"))
sys.path.insert(0, os.path.join(REPO, "dream/widerun"))
import census  # noqa: E402
import purity  # noqa: E402

KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
MMIO = re.compile(r"\b(readl|writel|read[bwq]|write[bwq]|ioread|iowrite|__raw_read|__raw_write)\b")
# scalar C types the T0/T1 probe path understands (others still route by census)
SCALAR = {"int", "unsigned int", "unsigned", "long", "unsigned long", "long long",
          "unsigned long long", "u8", "u16", "u32", "u64", "s8", "s16", "s32", "s64",
          "bool", "size_t"}
FNDEF = re.compile(r"^([A-Za-z_][\w \t\*]*?)\b([A-Za-z_]\w*)\s*\(([^;]*)\)\s*$")


def norm(t):
    return re.sub(r"\s+", " ", t.replace("const", "").replace("__init", "").replace("__exit", "").strip())


def sig_of(src: str, name: str) -> tuple[str, list[str]] | None:
    """(ret_ctype, [arg_ctypes]) best-effort for a driver fn (any signature)."""
    m = re.search(rf"\n([A-Za-z_][\w \t\*]*?)\b{re.escape(name)}\s*\(([^;{{)]*)\)\s*\n?\{{", src)
    if not m:
        return None
    ret = norm(m.group(1))
    args = []
    ap = m.group(2).strip()
    if ap and ap != "void":
        for p in ap.split(","):
            p = p.strip()
            sm = re.match(r"(.+?)\b[A-Za-z_]\w*\s*$", p) or re.match(r"(.+?\*)\s*$", p)
            args.append(norm(sm.group(1)) if sm else norm(p))
    return ret, args


def harvest(dirs: list[str], cap_pure: int) -> tuple[list[dict], dict]:
    dist: dict[str, int] = {}
    buckets: dict[str, list[dict]] = {}
    seen = set()
    pure_names: set[str] = set()
    scratch = []
    for d in dirs:
        for dp, _, fns in os.walk(os.path.join(KSRC, d)):
            for fn in fns:
                if not fn.endswith(".c"):
                    continue
                p = os.path.join(dp, fn)
                for name, body in census.funcs_in(p):
                    if name in seen:
                        continue
                    seen.add(name)
                    sig = sig_of(open(p, errors="ignore").read(), name)
                    if not sig:
                        continue
                    ret, args = sig
                    tier = census.classify(body)
                    mmio = bool(MMIO.search(purity.mask(body)))
                    scalar_ok = ret in SCALAR and all(a in SCALAR for a in args)
                    w = {"sym": name, "file": os.path.relpath(p, KSRC), "ret": ret,
                         "args": args, "body": body, "_tier": tier, "_mmio": mmio,
                         "_scalar": scalar_ok}
                    scratch.append(w)
    # fixpoint purity over the scalar subset (for the router's read-only routing)
    scal = [w for w in scratch if w["_scalar"]]
    for _ in range(3):
        pure_names = {w["sym"] for w in scal if purity.classify(w["body"], pure_names, w["sym"])[0] == "pure"}

    # stratify: keep all MMIO + a bounded struct-B + the pure scalar subset
    for w in scratch:
        if w["_mmio"]:
            key = "mmio"
        elif w["_tier"] == "B" and w["_scalar"] is False:
            key = "struct"
        elif w["_scalar"] and purity.recoverable_readonly(w["body"], pure_names, w["sym"]):
            key = "pure"
        else:
            key = "other"
        buckets.setdefault(key, []).append(w)
        dist[f"{w['_tier']}{'+MMIO' if w['_mmio'] else ''}"] = dist.get(f"{w['_tier']}{'+MMIO' if w['_mmio'] else ''}", 0) + 1

    work = (buckets.get("pure", [])[:cap_pure]
            + buckets.get("mmio", [])[:40]
            + buckets.get("struct", [])[:40]
            + buckets.get("other", [])[:20])
    for w in work:
        for k in ("_tier", "_mmio", "_scalar"):
            w.pop(k, None)
    return work, {"scanned": len(scratch), "distribution": dict(sorted(dist.items(), key=lambda x: -x[1])),
                  "worklist_size": len(work),
                  "bucket_sizes": {k: len(v) for k, v in buckets.items()}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", default="drivers/gpio,drivers/ptp,drivers/clk")
    ap.add_argument("--cap-pure", type=int, default=25)
    ap.add_argument("--out", default=os.path.join(HERE, "driver_worklist.json"))
    a = ap.parse_args()
    dirs = a.dirs.split(",")
    work, summary = harvest(dirs, a.cap_pure)
    print(f"driver harvest over {dirs}:")
    print(f"  scanned {summary['scanned']} fns")
    print("  tier distribution:")
    for k, v in summary["distribution"].items():
        print(f"    {v:5d}  {k}")
    print(f"  buckets: {summary['bucket_sizes']}")
    print(f"  -> worklist {summary['worklist_size']} (stratified): "
          f"routes to fill the mirror (struct) + recorder (mmio) buckets the scalar sweep couldn't")
    json.dump(work, open(a.out, "w"), indent=1)
    json.dump(summary, open(a.out.replace(".json", "_summary.json"), "w"), indent=1)
    print(f"  wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
