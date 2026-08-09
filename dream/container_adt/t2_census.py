#!/usr/bin/env python3
"""Refusal census over the whole T2 container population.

Measure-first: run the realizer's front gate over every T2 candidate and tally
what it accepts and why it refuses the rest. The refusal distribution is the
ROI ranking for the next feature — it is what says whether conditional bodies
(or splice, or rcu, or ...) are worth building.

With --gate the run also persists dream/realize/container_census_t2.json —
the machine-readable per-fn dispositions the refusal ledger (ratchet/ledger.py)
aggregates. Measure-once: the ledger reads this file, never re-runs gates.

  t2_census.py            # tally + top refusal reasons
  t2_census.py --gate     # also RUN the chain-walking gate on every acceptee
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

_spec = importlib.util.spec_from_file_location(
    "container_realize_c", os.path.join(HERE, "container_realize.py"))
CR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CR)


def targets():
    d = json.load(open(os.path.join(REPO, "dream", "realize",
                                    "container_feasibility.json")))
    out = []
    for f in d["T2"]:
        stem = f[len("container_"):-3]
        rel, fn = stem.rsplit(".c_", 1)
        out.append((rel.replace("__", "/") + ".c", fn))
    return out


def main():
    gate = "--gate" in sys.argv
    tg = targets()
    reasons, accepted = Counter(), []
    front_named = {}               # refusal class -> [rel:fn] (ledger feed)
    for rel, fn in tg:
        try:
            cops, _text, it = CR.c_ops(rel, fn)
            CR.adt_ops(rel, fn)
            accepted.append((rel, fn, cops, it))
        except CR.Refused as e:
            cls = str(e).split(":")[0]
            reasons[cls] += 1
            front_named.setdefault(cls, []).append(f"{rel}:{fn}")
        except Exception as e:
            cls = "error/" + type(e).__name__
            reasons[cls] += 1
            front_named.setdefault(cls, []).append(f"{rel}:{fn}")
    n = len(tg)
    print(f"T2 population: {n}")
    print(f"  ACCEPTED by the v1 front gate : {len(accepted)} ({100*len(accepted)/n:.0f}%)")
    print(f"  refused                       : {n - len(accepted)}")
    print("\nrefusal reasons (the ROI ranking for the next feature):")
    for r, c in reasons.most_common():
        print(f"  {c:4d}  {r}")
    it_n = sum(1 for *_x, it in accepted if it)
    print(f"\naccepted shape: {len(accepted)-it_n} straight-line, {it_n} iteration")
    print("accepted ops:", Counter(o["c_op"] for _r, _f, cs, _i in accepted for o in cs).most_common())

    if gate and accepted:
        L = CR.LM.probe_layout()
        match, gate_named = [], {}
        bad = []
        for rel, fn, _c, _i in accepted:
            try:
                v, out, d = CR.run_gate(rel, fn, L)
            except CR.Refused as e:
                # run_gate refusals (coverage:* included) are REFUSALS with a
                # name, tallied like the front gate's — never "ERROR"
                v = f"REFUSED:{e}"
            except Exception as e:
                v = f"ERROR:{str(e)[:40]}"
            if v == "MATCH":
                match.append(f"{rel}:{fn}")
            else:
                cls = v.split(":")[1] if v.startswith("REFUSED:") else v.split(":")[0]
                gate_named.setdefault(cls, []).append(f"{rel}:{fn}")
                bad.append((fn, v))
        print(f"\nCHAIN-WALKING GATE over all acceptees: {len(match)} MATCH, {len(bad)} not")
        for fn, v in bad[:15]:
            print(f"  ✗ {fn}: {v}")
        outp = os.path.join(REPO, "dream", "realize", "container_census_t2.json")
        json.dump({"population": n, "front_accepted": len(accepted),
                   "front_refusals": front_named,
                   "gate_match": len(match), "gate_refusals": gate_named,
                   "provenance": f"t2_census --gate {datetime.date.today()}"},
                  open(outp, "w"), indent=1)
        print(f"persisted -> {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
