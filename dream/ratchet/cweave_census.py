#!/usr/bin/env python3
"""Containers-WEAVE front gate census — the frozen denominator, measured
BEFORE any weave (pre-registration discipline).

The gate-time realizer (container_realize.py) verified the OP-SEQUENCE
translation: both differential sides model list ops + free events only, so
lock brackets and any other statements were soundly ignorable THERE. A WOVEN
function is different: it replaces the whole C body in the kernel, so the
emitted Rust must reproduce EVERYTHING the body does. This census classifies
every chain/composed-verified container fn (T2 139 + T3 95) by what full-body
weaving needs:

  * coverage  — body must be ONLY: list ops, kfree, lock brackets, lockdep
                asserts, iteration scaffolding, cursor decls, bare return.
                Anything else -> residual:<top tokens> (fail-closed).
  * locks     — none | mutex (mutex_lock/unlock: real symbols, extern-callable)
                | spin (spin_lock/unlock -> _raw_spin_lock/unlock: real symbols
                on SMP) | spin_irq (irqsave/irqrestore: symbol returns flags)
                | other (refuse).
  * storage   — every head/lock object: param-derived (&p->field) or
                file-static (seam passes the address; C seam has file scope).
  * built     — file compiled in the defconfig volume (config coverage).

  cweave_census.py           # tally + freeze dream/ratchet/cweave_denominator.json
  cweave_census.py --list    # per-fn rows
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
VOL = os.environ.get("WEAVE_VOL", "cgir-kbuild-defconfig")

_spec = importlib.util.spec_from_file_location(
    "container_realize_cw",
    os.path.join(REPO, "dream", "container_adt", "container_realize.py"))
CR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CR)
_spec2 = importlib.util.spec_from_file_location(
    "t3_census_cw", os.path.join(REPO, "dream", "container_adt", "t3_census.py"))
T3 = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(T3)

sys.path.insert(0, os.path.join(REPO, "dream", "cluster"))
import cluster  # noqa: E402

_MUTEX = ("mutex_lock", "mutex_unlock")
_SPIN = ("spin_lock", "spin_unlock")
_SPIN_IRQ = ("spin_lock_irqsave", "spin_unlock_irqrestore",
             "spin_lock_irq", "spin_unlock_irq", "spin_lock_bh",
             "spin_unlock_bh", "raw_spin_lock", "raw_spin_unlock",
             "raw_spin_lock_irqsave", "raw_spin_unlock_irqrestore")
_ASSERTS = ("lockdep_assert_held", "assert_spin_locked")

_COVERED_CALLS = (list(CR._C_OPS) + list(_MUTEX) + list(_SPIN)
                  + list(_SPIN_IRQ) + list(_ASSERTS)
                  + ["list_for_each_entry_safe", "list_for_each_entry",
                     "list_entry", "container_of", "list_empty"])


def verified_pairs():
    """(rel, fn, tier) for every chain/composed-verified container fn —
    re-derived by the same front gates that produced 139 + 95."""
    out = []
    d = json.load(open(os.path.join(REPO, "dream", "realize",
                                    "container_feasibility.json")))
    t2 = []
    for f in d["T2"]:
        stem = f[len("container_"):-3]
        rel, fn = stem.rsplit(".c_", 1)
        t2.append((rel.replace("__", "/") + ".c", fn))
    t3 = [(rel, fn) for rel, fn, _b in T3.t3_targets()]
    for tier, pairs in (("T2", t2), ("T3", t3)):
        for rel, fn in pairs:
            try:
                cops, _t, it = CR.c_ops(rel, fn)
                aops, _ = CR.adt_ops(rel, fn)
                if it is None:
                    CR.correspond(cops, aops)
            except Exception:
                continue
            out.append((rel, fn, tier))
    return out


def _mask_covered(body):
    """Remove everything weaving can reproduce; what remains is residual."""
    s = body
    # comments and strings first
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    s = re.sub(r"//[^\n]*", " ", s)
    s = re.sub(r'"(?:[^"\\]|\\.)*"', " ", s)
    # covered calls (balanced-paren removal via the realizer's splitter)
    for name in sorted(_COVERED_CALLS, key=len, reverse=True):
        while True:
            m = re.search(rf"\b{name}\s*\(", s)
            if not m:
                break
            try:
                _args, end = CR._split_call(s, m.end() - 1)
            except Exception:
                break
            s = s[:m.start()] + " " + s[end:]
    s = re.sub(r"\bkfree\s*\(\s*[A-Za-z_]\w*\s*\)", " ", s)
    # iteration cursor declarations: `struct foo *pos, *n;`
    s = re.sub(r"\bstruct\s+\w+\s*\*\s*\w+(\s*,\s*\*\s*\w+)*\s*;", " ", s)
    # irqsave flag declarations: `unsigned long flags;` — reproducible, the
    # _raw_spin_lock_irqsave symbol RETURNS the flags value
    s = re.sub(r"\bunsigned\s+long\s+flags\w*\s*;", " ", s)
    # list_empty guard shells (the call itself was masked above): the guarded
    # class is emitted by weave_containers via the gate-proven parser
    s = re.sub(r"\bif\s*\(\s*!?\s*\)\s*return\s*;", " ", s)
    s = re.sub(r"\bif\s*\(\s*!?\s*\)", " ", s)
    # bare control tokens weaving reproduces
    s = re.sub(r"\breturn\s*;", " ", s)
    s = re.sub(r"[{};]", " ", s)
    return " ".join(s.split())


def classify(rel, fn):
    src = open(os.path.join(KSRC, rel), errors="ignore").read()
    text = cluster.functions(src)[fn]["text"]
    body = text[text.index("{"):]
    row = {}

    if any(re.search(rf"\b{a}\s*\(", body) for a in _SPIN_IRQ):
        row["locks"] = "spin_irq"
    elif any(re.search(rf"\b{a}\s*\(", body) for a in _SPIN):
        row["locks"] = "spin"
    elif any(re.search(rf"\b{a}\s*\(", body) for a in _MUTEX):
        row["locks"] = "mutex"
    else:
        row["locks"] = "none"

    resid = _mask_covered(body)
    row["residual"] = resid[:80] if resid else ""

    # storage of every head/lock argument: file-static vs param-derived
    statics = set()
    for name in _MUTEX + _SPIN + _SPIN_IRQ + tuple(CR._C_OPS):
        for m in re.finditer(rf"\b{name}\s*\(", body):
            try:
                args, _ = CR._split_call(body, m.end() - 1)
            except Exception:
                continue
            for a in args:
                a = a.strip()
                mm = re.match(r"&?\s*([A-Za-z_]\w*)$", a)
                if mm:                       # bare identifier: param or static
                    statics.add(mm.group(1))
    row["bare_objs"] = sorted(statics)
    return row


def built_map(rels):
    """ONE docker call: which files produced .o in the defconfig volume."""
    script = "\n".join(
        f'test -f /build/linux/{rel[:-2]}.o && echo "BUILT {rel}" || echo "NOT {rel}"'
        for rel in sorted(set(rels)))
    r = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{VOL}:/build", "cgir-kernel-gate",
         "bash", "-c", script], capture_output=True, text=True, timeout=300)
    out = {}
    for ln in r.stdout.splitlines():
        parts = ln.split(None, 1)
        if len(parts) == 2 and parts[0] in ("BUILT", "NOT"):
            out[parts[1]] = parts[0] == "BUILT"
    return out


def main():
    listing = "--list" in sys.argv
    pairs = verified_pairs()
    print(f"verified container fns (re-derived): {len(pairs)}")
    rows = []
    for rel, fn, tier in pairs:
        row = classify(rel, fn)
        row.update(rel=rel, fn=fn, tier=tier)
        rows.append(row)
    built = built_map([r["rel"] for r in rows])
    for r in rows:
        r["built"] = built.get(r["rel"], False)

    lock_t = Counter(r["locks"] for r in rows)
    resid_n = sum(1 for r in rows if r["residual"])
    built_n = sum(1 for r in rows if r["built"])
    full = [r for r in rows if not r["residual"]
            and r["locks"] in ("none", "mutex", "spin", "spin_irq")]
    weaveable = [r for r in full if r["built"]]
    print(f"\nlocks: {dict(lock_t)}")
    print(f"full-coverage bodies (no residual): {len(rows)-resid_n}/{len(rows)}")
    print(f"built in {VOL}: {built_n}/{len(rows)}")
    print(f"\nWEAVE-ELIGIBLE (full-coverage AND built): {len(weaveable)}")
    print("  by tier:", Counter(r["tier"] for r in weaveable))
    print("  by locks:", Counter(r["locks"] for r in weaveable))
    resid_top = Counter()
    for r in rows:
        if r["residual"]:
            resid_top[r["residual"].split()[0]] += 1
    print("\ntop residual leaders (the honest refusal tally):")
    for k, c in resid_top.most_common(12):
        print(f"  {c:4d}  {k}")
    if listing:
        for r in rows:
            flag = "OK " if (not r["residual"] and r["built"]) else "-- "
            print(f"  {flag}{r['tier']} {r['rel']}:{r['fn']} locks={r['locks']}"
                  f" built={r['built']} resid={r['residual'][:40]}")
    out = os.path.join(HERE, "cweave_denominator.json")
    json.dump({"total_verified": len(pairs),
               "weave_eligible": [
                   {k: r[k] for k in ("rel", "fn", "tier", "locks", "bare_objs")}
                   for r in weaveable],
               "locks_tally": dict(lock_t),
               "residual_leaders": dict(resid_top.most_common(20))},
              open(out, "w"), indent=1)
    print(f"\nfrozen -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
