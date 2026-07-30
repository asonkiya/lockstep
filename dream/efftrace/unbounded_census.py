#!/usr/bin/env python3
"""Sub-census of the "unbounded" tail — WHAT makes it unbounded, so each research
technique can be sized against real numbers before we propose it.

The interprocedural closure found ~52% of the core is a hard tail. But "unbounded"
is not one thing. This buckets every core function carrying an unbounded signal by
its DOMINANT sub-shape, because different sub-shapes yield to different techniques:

  container_op   list/rbtree/xarray/idr/radix ops   -> ADT-level oracle + shared
                                                        verified core (a FAMILY lever)
  alloc          kmalloc/slab/pages                 -> modelable effect (allocator
                                                        model in the recorder)
  iter_loop      loop over a list/array of unknown n-> concrete under a recorded
                                                        workload (record/replay)
  reactive       jiffies/ktime/random/cycles        -> record-the-environment
                                                        (the genuine frontier)
  external_only  opaque callee, no local unbounded  -> corpus/annotation completeness
  other          none of the above matched
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("widerun", "cluster", "router"):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import purity    # noqa: E402
import entangle  # noqa: E402

KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")

_CONTAINER = re.compile(r"\b(list_add\w*|list_del\w*|list_move\w*|list_splice\w*|list_for_each\w*|"
                        r"hlist_\w+|rb_insert\w*|rb_erase\w*|rb_link_node|rb_next|rb_first|rb_entry|"
                        r"xa_\w+|xas_\w+|idr_\w+|ida_\w+|radix_tree_\w+|llist_\w+)\b")
_ALLOC = re.compile(r"\bk[mzv]alloc\w*|\bkfree\b|\bvfree\b|kmem_cache_\w+|mempool_\w+|"
                    r"__get_free_\w+|alloc_pages?\b|free_pages?\b|__free_\w+")
_ITER = re.compile(r"\b(list_for_each\w*|hlist_for_each\w*)\b|\bwhile\s*\([^)]*->\s*next|"
                   r"\bfor\s*\([^)]*->\s*next")
_REACTIVE = re.compile(r"\bjiffies\b|\bktime\w*|get_random\w*|prandom\w*|get_cycles|"
                       r"random_\w+|\brdtsc|sched_clock|local_clock|ktime_get\w*")


def sub_classify(body: str, owned) -> str:
    scan = purity.mask(body)
    if _CONTAINER.search(scan):
        return "container_op"
    if _ALLOC.search(scan):
        return "alloc"
    if _ITER.search(scan):
        return "iter_loop"
    if _REACTIVE.search(scan):
        return "reactive"
    # opaque callee with no local unbounded signal -> recoverable by annotation
    for m in purity.CALL.finditer(scan):
        nm = m.group(1)
        if nm not in purity.NONCALL and nm not in purity.PURE_CALL and nm not in owned:
            return "external_only"
    return "other"


def _is_unbounded_signal(scan):
    return bool(entangle._GRAPH.search(scan) or _ALLOC.search(scan)
                or _ITER.search(scan) or _REACTIVE.search(scan))


def scan():
    import cluster
    tally = Counter()
    examples = {}
    limit = int(os.environ.get("LIMIT", "0"))
    files = entangle._core_files()
    if limit:
        files = files[:limit]
    for pth in files:
        rel = os.path.relpath(pth, KSRC)
        try:
            funcs = cluster.functions(open(pth, errors="ignore").read())
        except Exception:
            continue
        for name, f in funcs.items():
            try:
                klass, _ = entangle.classify(f["text"], rel, name)
            except Exception:
                continue
            # the "hard tail": unbounded_state, or bounded_state that carries an
            # unbounded signal in-body (the genuinely-unbounded interproc class).
            scan_body = purity.mask(f["text"])
            if klass == "unbounded_state" or (klass == "bounded_state" and _is_unbounded_signal(scan_body)):
                owned = purity.owned_names(f["text"]) | purity.KEYWORDS
                sub = sub_classify(f["text"], owned)
                tally[sub] += 1
                examples.setdefault(sub, []).append(f"{name} ({rel})")
    return tally, examples


def main() -> int:
    tally, examples = scan()
    total = sum(tally.values())
    print(f"=== sub-census of the unbounded core tail ({total} fns) ===\n")
    TECH = {
        "container_op": "ADT-level oracle + shared verified container core (FAMILY lever)",
        "alloc": "allocator model in the effect-trace recorder",
        "iter_loop": "concrete under a recorded workload (record/replay)",
        "external_only": "corpus/annotation completeness (engineering)",
        "reactive": "record-the-environment (the genuine frontier)",
        "other": "unclassified",
    }
    for sub, _ in sorted(TECH.items(), key=lambda kv: -tally.get(kv[0], 0)):
        n = tally.get(sub, 0)
        pct = 100 * n / total if total else 0
        print(f"  {n:6d}  {pct:5.1f}%  {sub:13s} -> {TECH[sub]}")
        for ex in examples.get(sub, [])[:2]:
            print(f"                        e.g. {ex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
