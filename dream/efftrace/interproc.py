#!/usr/bin/env python3
"""Interprocedural footprint closure — the CGIR<->lockstep seam that recovers the
opaque-callee 90%.

footprint.py refuted 12,855 bounded_state functions on a single opaque call
(a syntactic pass must treat any callee as possibly-unbounded). Most of those
callees are small pure/bounded helpers. This module resolves them: build a corpus
map (name -> body over the core), then compute each function's footprint as the
transitive closure over its call graph —

  footprint(F) = own_writes(F)  U  U_callee footprint(callee)

with PURE builtins contributing nothing, a bounded callee folding its write-set
in, and any UNBOUNDED (pointer-graph/alloc/recursion) or UNRESOLVED (external,
body not in corpus) callee refuting F. This is exactly what CGIR's `effects`
transitive closure over the CALLS graph does; here it runs on lockstep's own
call-graph extraction (CGIR's call_graph would sharpen edges through function
pointers/macros — the noted refinement).

Verdict precedence: unbounded > unresolved > bounded. `unresolved` (external
callee) is reported separately from `unbounded` (genuine) so corpus-incompleteness
is distinguishable from real unboundedness.
"""
from __future__ import annotations

import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("widerun", "cluster", "router"):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import purity    # noqa: E402
import entangle  # noqa: E402
import footprint  # noqa: E402

KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
_MAXDEPTH = 12

BOUNDED, UNBOUNDED, UNRESOLVED = "bounded", "unbounded", "unresolved"


def build_corpus():
    """{name: body} over the core + the bounded_state worklist (one parse pass)."""
    import cluster
    corpus, worklist = {}, []
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
            corpus.setdefault(name, f["text"])   # first definition wins (static collisions)
            try:
                if entangle.classify(f["text"], rel, name)[0] == "bounded_state":
                    worklist.append(name)
            except Exception:
                pass
    return corpus, worklist


def resolve(name, corpus, memo, stack, depth=0):
    """Return (verdict, writes:set, reason)."""
    if name in memo:
        return memo[name]
    if name in purity.PURE_CALL or name in purity.NONCALL:
        return (BOUNDED, set(), "pure builtin")
    if name in stack:
        return (UNBOUNDED, set(), "recursion")
    if depth > _MAXDEPTH:
        return (UNRESOLVED, set(), "depth cap")
    if name not in corpus:
        return (UNRESOLVED, set(), f"external callee {name}")   # body not in corpus

    of = footprint.own_footprint(corpus[name], name)
    if of["local_hard"]:
        res = (UNBOUNDED, of["writes"], of["reason"])
        memo[name] = res
        return res

    writes = set(of["writes"])
    worst = BOUNDED
    reason = "bounded"
    stack = stack | {name}
    for callee in of["callees"]:
        v, w, r = resolve(callee, corpus, memo, stack, depth + 1)
        if v == BOUNDED:
            writes |= w
        elif v == UNRESOLVED and worst != UNBOUNDED:
            worst, reason = UNRESOLVED, r
        elif v == UNBOUNDED:
            worst, reason = UNBOUNDED, f"callee {callee}: {r}"
    res = (worst, writes, reason)
    if worst != UNRESOLVED:          # don't cache unresolved (corpus may grow); safe+simple
        memo[name] = res
    return res


def scan():
    corpus, worklist = build_corpus()
    memo = {}
    tally = Counter()
    recovered = []
    for name in worklist:
        v, writes, reason = resolve(name, corpus, memo, set())
        tally[{BOUNDED: "BOUNDED (effect-trace applies)",
               UNBOUNDED: "unbounded (genuine)",
               UNRESOLVED: "unresolved (external callee)"}[v]] += 1
        if v == BOUNDED:
            recovered.append((name, sorted(writes)[:4]))
    return tally, recovered, len(worklist), len(corpus)


def main() -> int:
    tally, recovered, nwork, ncorp = scan()
    b = tally.get("BOUNDED (effect-trace applies)", 0)
    print(f"=== interprocedural footprint closure over bounded_state "
          f"({nwork} fns, corpus {ncorp}) ===\n")
    for k, c in tally.most_common():
        print(f"  {c:6d}  {k}")
    print(f"\n  effect-trace reach WITH interprocedural closure: {b}/{nwork} "
          f"= {100*b/nwork:.1f}% of bounded_state" if nwork else "")
    print(f"  (syntactic-only was 729; interprocedural recovers to {b})")
    for name, w in recovered[:10]:
        print(f"    e.g. {name}  writes={w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
