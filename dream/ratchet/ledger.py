#!/usr/bin/env python3
"""The refusal ledger — every stage's named refusals in ONE machine-readable
table, ranked by functions-unlocked. The campaign scheduler.

Every gate in the pipeline already refuses BY NAME (the fail-closed
discipline); until now the tallies lived in per-stage artifacts and picking
the next lever was a human reading them. This module aggregates the PERSISTED
artifacts — measure-once, it never re-runs a gate — into ledger.json and a
ranked lever view:

  ledger.py            # rebuild ledger.json from artifacts + print the table
  ledger.py levers     # same (alias)

Sources (all persisted by their own campaigns):
  * dream/realize/container_census_t{2,3}.json  — containers front+gate
    refusals, per-fn named (t2_census/t3_census --gate)
  * dream/realize/census.jsonl                  — efftrace realize census
    ("result" key; gitignored — skipped gracefully when absent)
  * dream/ratchet/cweave_denominator.json       — weave residual leaders +
    the measured weave-eligibility fraction

unlock_estimate is deliberately simple and labeled: realize-stage classes
unlock `count` fns toward REALIZED; weave-stage classes unlock
`count x eligibility_fraction` toward PRESENT (presence is config-bounded —
the fraction is measured, not assumed). The two currencies are not summed.

THE RULE (STRATEGY.md): the campaign's next slice is the ledger's top lever
unless a human overrides with a written reason.

Every row carries a `tier` for the campaign runner (dream/campaign/runner.py):
  auto     — unlocked by re-running existing code (no row is; auto cycles are
             the runner's maintenance registry, kept there)
  agent    — realizer/gate EXTENSION: an agent session writes novel code, the
             playbook harness does everything around it
  research — a new oracle TYPE (new arena shapes, new differential semantics);
             stays human-driven per STRATEGY.md §4 — the runner must refuse
             to auto-dispatch these.
"""
from __future__ import annotations

import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

# refusal classes that need a NEW ORACLE TYPE (arena shapes / differential
# semantics that don't exist yet) — human-driven research per STRATEGY §4.
TIER_RESEARCH = {
    "multi_member_ops",              # needs a multi-member arena
    "multi_head_iteration",          # needs a multi-head arena
    "plain_iteration_with_mutation", # cursor-over-mutated-list semantics
    "cross_list_move",               # needs a two-list arena move oracle
}


def classify_tier(stage: str, refusal_class: str) -> str:
    if refusal_class in TIER_RESEARCH:
        return "research"
    return "agent"    # realize/weave extensions: agent-in-the-loop slices


def _load(path):
    try:
        return json.load(open(path))
    except Exception:
        return None


def collect(repo=REPO):
    """Aggregate every persisted refusal tally into ledger rows."""
    acc = {}                      # (stage, class) -> {"count": n, "fns": [...]}

    def add(stage, cls, fns=None, count=None):
        k = (stage, cls)
        r = acc.setdefault(k, {"count": 0, "fns": []})
        r["count"] += count if count is not None else len(fns or [])
        if fns:
            r["fns"] += fns

    for tier in ("t2", "t3"):
        d = _load(os.path.join(repo, "dream", "realize",
                               f"container_census_{tier}.json"))
        if not d:
            continue
        for key in ("front_refusals", "gate_refusals"):
            for cls, fns in d.get(key, {}).items():
                add(f"realize/containers-{tier}", cls, fns=fns)

    cj = os.path.join(repo, "dream", "realize", "census.jsonl")
    if os.path.exists(cj):
        for ln in open(cj):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            res = str(r.get("result", ""))
            if res == "MATCH" or not res:
                continue
            cls = res.split(":", 1)[1] if res.startswith("REFUSED:") else res
            add("realize/efftrace", cls.split(":")[0], fns=[r.get("key", "?")])

    cw = _load(os.path.join(repo, "dream", "ratchet", "cweave_denominator.json"))
    frac, frac_src = None, "no cweave_denominator.json"
    if cw:
        tv = cw.get("total_verified") or 0
        el = len(cw.get("weave_eligible", []))
        if tv:
            frac = el / tv
            frac_src = f"cweave_denominator: {el}/{tv} weave-eligible"
        for tok, count in cw.get("residual_leaders", {}).items():
            add("weave/containers", f"residual:{tok[:40]}", count=count)

    rows = []
    for (stage, cls), r in acc.items():
        realize = stage.startswith("realize")
        est = (r["count"] if realize
               else round(r["count"] * (frac if frac is not None else 0), 1))
        rows.append({"stage": stage, "refusal_class": cls, "count": r["count"],
                     "fns": sorted(set(r["fns"]))[:200],
                     "unlock_estimate": est,
                     "metric": "realized_fns" if realize else "present_fns",
                     "tier": classify_tier(stage, cls)})
    rows.sort(key=lambda r: (-r["unlock_estimate"], r["stage"], r["refusal_class"]))
    return {"generated": str(datetime.date.today()),
            "eligibility_fraction": frac, "frac_src": frac_src,
            "rule": "next slice = top lever unless a human overrides with a reason",
            "rows": rows}


def levers(led, top=12):
    print(f"=== refusal ledger — ranked levers ({led['generated']}; "
          f"eligibility {led['frac_src']}) ===")
    print(f"{'unlock':>8}  {'metric':<13} {'count':>5}  {'tier':<9} "
          f"{'stage':<24} class")
    for r in led["rows"][:top]:
        print(f"{r['unlock_estimate']:>8}  {r['metric']:<13} {r['count']:>5}  "
              f"{r.get('tier', '?'):<9} {r['stage']:<24} {r['refusal_class']}")
    rest = led["rows"][top:]
    if rest:
        print(f"  ... tail: {len(rest)} more classes, "
              f"{sum(r['count'] for r in rest)} fns total — small by "
              f"construction (Zipf head above); refuse-by-name is the honest "
              f"disposition for most of them")
    print(f"rule: {led['rule']}")


def main():
    led = collect()
    out = os.path.join(HERE, "ledger.json")
    json.dump(led, open(out, "w"), indent=1)
    levers(led)
    print(f"\nwritten -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
