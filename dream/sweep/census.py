#!/usr/bin/env python3
"""Massive eligibility census — classify thousands of real kernel functions into
transplant tiers, to measure the reachable denominator with tight error bars
(tightening the research's n=360 sample to n=thousands).

Tiers (research A/B/C/D):
  A leaf-scalar : scalar/void args, no field deref, no lock, no entangling call
  B struct      : reads struct fields (->/.) but no lock/entanglement (Tier-B middle)
  C locked      : takes a lock (spin/mutex/rcu_read/seq)
  D entangled   : container_of / per_cpu / rcu_deref / list_ / ops-table / setjmp

Boot-free, pure static heuristics over the real tree. Prints a JSON summary.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys

KSRC = "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux"
SUBSYS = ["drivers", "lib", "fs", "net", "kernel", "mm", "crypto", "block", "sound"]
PER_SUBSYS_FILES = 220  # stratified cap per subsystem for speed + balance

LOCK = re.compile(r"\b(spin_lock|mutex_lock|down_(read|write)|rcu_read_lock|read_lock|write_lock|seq)\w*\s*\(")
ENTANGLE = re.compile(r"\b(container_of|per_cpu|this_cpu|__percpu|rcu_dereference|list_(add|del|for_each|entry)|hlist_|llist_|kref_|refcount_)\w*")
FIELD = re.compile(r"[A-Za-z_]\w*\s*(->|\.)\s*[A-Za-z_]")
FNDEF = re.compile(r"^[A-Za-z_][\w \t\*]*?\b([A-Za-z_]\w*)\s*\([^;]*\)\s*$")


def funcs_in(path):
    """(name, body) for column-0 function definitions."""
    try:
        src = open(path, errors="ignore").read()
    except OSError:
        return
    lines = src.split("\n")
    i, n = 0, len(lines)
    while i < n:
        m = FNDEF.match(lines[i])
        if m and i + 1 < n and lines[i + 1].startswith("{"):
            # brace-match the body
            depth, j, body = 0, i + 1, []
            started = False
            while j < n:
                body.append(lines[j])
                depth += lines[j].count("{") - lines[j].count("}")
                if "{" in lines[j]:
                    started = True
                if started and depth <= 0:
                    break
                j += 1
            yield m.group(1), "\n".join(body)
            i = j + 1
        else:
            i += 1


def mask(s):
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    s = re.sub(r"//[^\n]*", " ", s)
    s = re.sub(r'"(\\.|[^"])*"', '""', s)
    return s


def classify(body):
    b = mask(body)
    if ENTANGLE.search(b):
        return "D"
    if LOCK.search(b):
        return "C"
    if FIELD.search(b):
        return "B"
    return "A"


def wilson(k, n):
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    z = 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * p, 100 * max(0, c - h), 100 * min(1, c + h))


def main():
    tally = {t: 0 for t in "ABCD"}
    per = {}
    total = 0
    for sub in SUBSYS:
        root = os.path.join(KSRC, sub)
        cfiles = []
        for dp, _, fns in os.walk(root):
            for fn in fns:
                if fn.endswith(".c"):
                    cfiles.append(os.path.join(dp, fn))
        cfiles.sort()
        # stratified: evenly spaced sample of files across the subsystem
        step = max(1, len(cfiles) // PER_SUBSYS_FILES)
        sample = cfiles[::step][:PER_SUBSYS_FILES]
        st = {t: 0 for t in "ABCD"}
        for f in sample:
            for _, body in funcs_in(f):
                if body.count("\n") > 400:
                    continue
                t = classify(body)
                st[t] += 1
                tally[t] += 1
                total += 1
        per[sub] = st
        print(f"  {sub}: {sum(st.values())} fns  A{st['A']} B{st['B']} C{st['C']} D{st['D']}", file=sys.stderr)

    out = {
        "total_classified": total,
        "tiers": {t: {"count": tally[t], **dict(zip(("pct", "lo", "hi"), wilson(tally[t], total)))} for t in "ABCD"},
        "reachable_pct": round(100 * (tally["A"] + tally["B"] + tally["C"]) / total, 1),
        "per_subsystem": {s: {t: round(100 * st[t] / max(1, sum(st.values())), 1) for t in "ABCD"} for s, st in per.items()},
    }
    print(json.dumps(out, indent=1))
    os.makedirs(os.path.dirname(os.path.abspath(__file__)), exist_ok=True)
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "census.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
