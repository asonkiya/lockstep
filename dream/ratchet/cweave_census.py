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


def built_map(pairs):
    """ONE docker call: file built + is the fn's own symbol in the .o.
    ELIGIBILITY is file-level (BUILT or NOSYM): a static fn's symbol is
    routinely inlined away while its woven seam still calls _rs (21 of the
    boot-verified 40 are NOSYM statics). But NOSYM also covers the vacuous
    case — a fn #ifdef'd out of the object (net_unlink_todo under
    CONFIG_LOCKDEP) whose _rs would link with no caller. The two are
    distinguished at BATCH time by the seam-reference check (the woven .o
    must reference <fn>_rs); sym_in_obj is recorded here so that check has
    its prediction list.

    .o existence is NOT proof the file is in this config's vmlinux: our own
    probe/census passes force-build orphan objects (`make path/x.o` succeeds
    for files the config never links — cxgb4_mps.o existed with
    CONFIG_CHELSIO_T4 entirely unset, measured 2026-08-09, and its woven _rs
    was correctly caught absent by the batch nm gate). Linkage test: the .o's
    first defined global symbol must be defined in vmlinux -> else ORPHAN."""
    lines = ["nm /build/linux/vmlinux 2>/dev/null | awk '{print $NF}' "
             "| sort -u > /tmp/vml.txt"]
    for rel, fn in sorted(set(pairs)):
        obj = f"/build/linux/{rel[:-2]}.o"
        lines.append(
            f'if test -f {obj}; then '
            f'g=$(nm -g --defined-only {obj} 2>/dev/null | awk \'NR==1{{print $NF}}\'); '
            f'if [ -n "$g" ] && ! grep -qx "$g" /tmp/vml.txt; '
            f'then echo "ORPHAN {rel} {fn}"; '
            f'elif nm {obj} 2>/dev/null | grep -q " [tT] {fn}$"; '
            f'then echo "BUILT {rel} {fn}"; else echo "NOSYM {rel} {fn}"; fi; '
            f'else echo "NOT {rel} {fn}"; fi')
    # script via STDIN, not -c: 289 generated lines exceed the kernel's
    # 128 KiB single-argument cap and execve silently truncates the run
    r = subprocess.run(
        ["docker", "run", "--rm", "-i", "-v", f"{VOL}:/build",
         "cgir-kernel-gate", "bash", "-s"], input="\n".join(lines),
        capture_output=True, text=True, timeout=600)
    out = {}
    for ln in r.stdout.splitlines():
        parts = ln.split()
        if len(parts) == 3 and parts[0] in ("BUILT", "NOSYM", "NOT", "ORPHAN"):
            out[(parts[1], parts[2])] = parts[0]
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
    built = built_map([(r["rel"], r["fn"]) for r in rows])
    for r in rows:
        r["built_state"] = built.get((r["rel"], r["fn"]), "NOT")
        r["built"] = r["built_state"] in ("BUILT", "NOSYM")
        r["sym_in_obj"] = r["built_state"] == "BUILT"
    orphan_n = sum(1 for r in rows if r["built_state"] == "ORPHAN")

    lock_t = Counter(r["locks"] for r in rows)
    resid_n = sum(1 for r in rows if r["residual"])
    built_n = sum(1 for r in rows if r["built"])
    nosym_n = sum(1 for r in rows if r["built_state"] == "NOSYM")
    full = [r for r in rows if not r["residual"]
            and r["locks"] in ("none", "mutex", "spin", "spin_irq")]
    weaveable = [r for r in full if r["built"]]
    print(f"\nlocks: {dict(lock_t)}")
    print(f"full-coverage bodies (no residual): {len(rows)-resid_n}/{len(rows)}")
    print(f"file linked in {VOL} vmlinux: {built_n}/{len(rows)}"
          f"  (fn symbol inlined/absent in obj: {nosym_n} — "
          f"batch seam-reference check adjudicates; "
          f"orphan .o never linked: {orphan_n})")
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
                   {k: r[k] for k in ("rel", "fn", "tier", "locks",
                                      "bare_objs", "sym_in_obj")}
                   for r in weaveable],
               "locks_tally": dict(lock_t),
               "residual_leaders": dict(resid_top.most_common(20))},
              open(out, "w"), indent=1)
    print(f"\nfrozen -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
