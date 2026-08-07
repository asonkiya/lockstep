#!/usr/bin/env python3
"""Refusal census over the whole T2 container population.

Measure-first: run the realizer's front gate over every T2 candidate and tally
what it accepts and why it refuses the rest. The refusal distribution is the
ROI ranking for the next feature — it is what says whether conditional bodies
(or splice, or rcu, or ...) are worth building.

  t2_census.py            # tally + top refusal reasons
  t2_census.py --gate     # also RUN the chain-walking gate on every acceptee
"""
from __future__ import annotations

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
    for rel, fn in tg:
        try:
            cops, _text, it = CR.c_ops(rel, fn)
            CR.adt_ops(rel, fn)
            accepted.append((rel, fn, cops, it))
        except CR.Refused as e:
            reasons[str(e).split(":")[0]] += 1
        except Exception as e:
            reasons["error/" + type(e).__name__] += 1
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
        v_ok = v_bad = 0
        bad = []
        for rel, fn, _c, _i in accepted:
            try:
                v, out, d = CR.run_gate(rel, fn, L)
            except Exception as e:
                v = f"ERROR:{str(e)[:40]}"
            if v == "MATCH":
                v_ok += 1
            else:
                v_bad += 1
                bad.append((fn, v))
        print(f"\nCHAIN-WALKING GATE over all acceptees: {v_ok} MATCH, {v_bad} not")
        for fn, v in bad[:15]:
            print(f"  ✗ {fn}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
