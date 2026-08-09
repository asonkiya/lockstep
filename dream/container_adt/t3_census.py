#!/usr/bin/env python3
"""Shape census over the T3 (retire/kfree) container population.

Measure BEFORE building (the T2 lesson: the census said NOT to build the
condition parser — 2 fns — and to spend the effort gating 181 instead). T3 is
gated on composing the chain oracle with allocation LIFETIME; what exactly the
composed gate must support is an empirical question about the 131 real C
bodies, answered here:

  * free variant       — bare kfree(node) only? kfree_rcu/kvfree/kmem_cache_free?
                         (reach.py banked only `kfree(simple_var)`, verify that)
  * iteration form     — safe / plain / none / other-loop
  * conditional bodies — the `if (pred) { del; kfree; }` shape
  * list-op vocabulary — which concrete ops co-occur with the free
  * unsupported C      — _rcu / splice / hlist_ / multi-list

  t3_census.py          # tally
  t3_census.py --list   # per-candidate rows
  t3_census.py --gate   # front gate over all 131 + the COMPOSED differential
                        # (chain + free-event log) over every acceptee
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
VERIFIED = os.path.join(REPO, "dream", "firstrun", "verified")

_spec = importlib.util.spec_from_file_location(
    "container_feas_t3", os.path.join(REPO, "dream", "realize", "container_feas.py"))
CF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CF)

sys.path.insert(0, os.path.join(REPO, "dream", "cluster"))
import cluster  # noqa: E402

_FREE_VARIANTS = ("kfree_rcu", "kfree_sensitive", "kvfree", "kmem_cache_free",
                  "devm_kfree", "kfree")           # order: specific before bare
_UNSUPPORTED_C = ("_rcu", "list_splice", "list_cut", "list_bulk",
                  "list_replace", "list_swap", "list_rotate", "hlist_")
_LIST_OPS = ("list_del_init", "list_del", "list_add_tail", "list_add",
             "list_move_tail", "list_move", "INIT_LIST_HEAD")


def t3_targets():
    out = []
    for f in sorted(os.listdir(VERIFIED)):
        if not f.startswith("container_"):
            continue
        b = CF.body_of(os.path.join(VERIFIED, f))
        if b is None:
            continue
        tier, _used, _lists = CF.classify(b)
        if tier != "T3_RETIRE":
            continue
        stem = f[len("container_"):-3]
        rel, fn = stem.rsplit(".c_", 1)
        out.append((rel.replace("__", "/") + ".c", fn, b))
    return out


def shape_of(rel, fn):
    src = open(os.path.join(KSRC, rel), errors="ignore").read()
    try:
        text = cluster.functions(src)[fn]["text"]
    except KeyError:
        return {"verdict": "fn_not_found"}
    body = text[text.index("{"):]
    s = {"verdict": "ok"}

    frees = []
    masked = body
    for v in _FREE_VARIANTS:
        n = len(re.findall(rf"\b{v}\s*\(", masked))
        if n:
            frees.append((v, n))
            masked = re.sub(rf"\b{v}\s*\(", "MASKED(", masked)
    s["free"] = "+".join(f"{v}x{n}" if n > 1 else v for v, n in frees) or "none"
    # is every bare kfree arg a simple identifier?
    bare_args = re.findall(r"\bkfree\s*\(\s*([^)]*?)\s*\)", body)
    s["free_arg_simple"] = all(re.fullmatch(r"[A-Za-z_]\w*", a) for a in bare_args)

    if re.search(r"\b(kmalloc|kzalloc|kcalloc|kmem_cache_alloc)\b", body):
        s["alloc_too"] = True

    bad = [b for b in _UNSUPPORTED_C if b in body]
    if bad:
        s["unsupported"] = bad[0]

    if "list_for_each_entry_safe" in body:
        s["iter"] = "safe"
    elif "list_for_each_entry" in body:
        s["iter"] = "plain"
    elif re.search(r"\b(while|for)\b", body) or "list_for_each" in body:
        s["iter"] = "other_loop"
    else:
        s["iter"] = "none"

    s["conditional"] = bool(re.search(r"\bif\b|\?", body))
    s["ops"] = [m.group(1) for m in
                re.finditer(r"\b(" + "|".join(_LIST_OPS) + r")\s*\(", body)]
    heads = set()
    for m in re.finditer(r"\blist_for_each_entry(?:_safe)?\s*\(([^;{]*)\)", body):
        parts = [p.strip() for p in m.group(1).split(",")]
        if len(parts) >= 3:
            heads.add(re.sub(r"[\s&]", "", parts[-2]))
    s["iter_heads"] = len(heads)
    return s


def adt_shape(b):
    """The model side: is it the canonical flush (`for id in iter(L) { del(id);
    retire(id) }`), a conditional variant, or straight-line del+retire?"""
    has_iter = re.search(r"(?<![\w])iter\s*\(", b) is not None
    has_if = re.search(r"(?<![\w])if\b", b) is not None
    return ("iter_cond" if has_iter and has_if else
            "iter" if has_iter else
            "cond" if has_if else "straight")


def main():
    listing = "--list" in sys.argv
    tg = t3_targets()
    print(f"T3 population: {len(tg)}")
    shapes, rows = Counter(), []
    free_tally, adt_tally = Counter(), Counter()
    for rel, fn, mbody in tg:
        s = shape_of(rel, fn)
        a = adt_shape(mbody)
        adt_tally[a] += 1
        if s["verdict"] != "ok":
            shapes[s["verdict"]] += 1
            rows.append((rel, fn, s["verdict"], a))
            continue
        free_tally[s["free"]] += 1
        key = []
        if s.get("unsupported"):
            key.append(f"unsupported:{s['unsupported']}")
        if s.get("alloc_too"):
            key.append("alloc_too")
        if not s["free_arg_simple"]:
            key.append("free_arg_complex")
        key.append(f"iter={s['iter']}")
        if s["conditional"]:
            key.append("cond")
        if s["iter_heads"] > 1:
            key.append("multi_head")
        k = ",".join(key)
        shapes[k] += 1
        rows.append((rel, fn, k + " | " + "+".join(s["ops"]) + f" | {s['free']}", a))

    print("\nC shape distribution (the ROI ranking for the composed gate):")
    for k, c in shapes.most_common():
        print(f"  {c:4d}  {k}")
    print("\nfree variant:")
    for k, c in free_tally.most_common():
        print(f"  {c:4d}  {k}")
    print("\nADT model shape:")
    for k, c in adt_tally.most_common():
        print(f"  {c:4d}  {k}")
    if listing:
        print()
        for rel, fn, k, a in rows:
            print(f"  {rel}:{fn}\n      C  : {k}\n      ADT: {a}")

    if "--gate" in sys.argv:
        import datetime
        import json
        _spec2 = importlib.util.spec_from_file_location(
            "container_realize_t3c", os.path.join(HERE, "container_realize.py"))
        CR = importlib.util.module_from_spec(_spec2)
        _spec2.loader.exec_module(CR)
        reasons, accepted, front_named = Counter(), [], {}
        for rel, fn, _b in tg:
            try:
                cops, _t, it = CR.c_ops(rel, fn)
                aops, _ = CR.adt_ops(rel, fn)
                if it is None:
                    CR.correspond(cops, aops)
                accepted.append((rel, fn, it))
            except CR.Refused as e:
                cls = str(e).split(":")[0]
                reasons[cls] += 1
                front_named.setdefault(cls, []).append(f"{rel}:{fn}")
            except Exception as e:
                cls = "error/" + type(e).__name__
                reasons[cls] += 1
                front_named.setdefault(cls, []).append(f"{rel}:{fn}")
        print(f"\nfront gate: {len(accepted)}/{len(tg)} accepted")
        for r, c in reasons.most_common():
            print(f"  {c:4d}  {r}")
        L = CR.LM.probe_layout()
        v_ok, bad, gate_named = 0, [], {}
        for rel, fn, _it in accepted:
            try:
                v, out, _d = CR.run_gate(rel, fn, L)
            except CR.Refused as e:
                v = f"REFUSED:{e}"     # coverage:* and friends — named, not ERROR
            except Exception as e:
                v = f"ERROR:{str(e)[:40]}"
            if v == "MATCH":
                v_ok += 1
            else:
                cls = v.split(":")[1] if v.startswith("REFUSED:") else v.split(":")[0]
                gate_named.setdefault(cls, []).append(f"{rel}:{fn}")
                bad.append((rel, fn, v))
        print(f"\nCOMPOSED GATE (chain + free log) over acceptees: "
              f"{v_ok} MATCH, {len(bad)} not")
        for rel, fn, v in bad[:20]:
            print(f"  ✗ {rel}:{fn}: {v}")
        outp = os.path.join(REPO, "dream", "realize", "container_census_t3.json")
        json.dump({"population": len(tg), "front_accepted": len(accepted),
                   "front_refusals": front_named,
                   "gate_match": v_ok, "gate_refusals": gate_named,
                   "provenance": f"t3_census --gate {datetime.date.today()}"},
                  open(outp, "w"), indent=1)
        print(f"persisted -> {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
