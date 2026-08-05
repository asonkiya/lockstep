"""Feasibility census for CONTAINER realization (model -> real intrusive list).

Measure BEFORE building (the discipline that has repeatedly saved this project
from a wasted build). Classifies each verified container candidate by the ADT
vocabulary its body actually uses, into realization difficulty tiers:

  T1_PURE_READ   : only reads (empty/first/last/iter-without-mutation/field/tokf)
                   -> realizes like a reader: list_empty / list_first_entry /
                      container_of + field reads. No pointer surgery.
  T2_SIMPLE_MUT  : adds del/push_back/push_front/move_* on ONE list
                   -> needs real list_del/list_add (prev/next writes) but no
                      allocation and no multi-list reasoning.
  T3_RETIRE      : uses retire() (kfree) -> allocation lifetime, needs the
                   allocator model composed in; NOT a pure list transform.
  T4_MULTI       : touches 2+ distinct lists, or token-field writes.
"""
import json
import os
import re
from collections import Counter

V = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "firstrun", "verified")

READ_OPS = {"iter", "empty", "first", "last", "field", "tokf", "tok_field"}
MUT_OPS = {"del", "push_back", "push_front", "move_tail", "move_front", "set_field"}
ALLOC_OPS = {"retire"}


def body_of(path):
    src = open(path).read()
    m = re.search(r'extern "C" fn rs_call\([^)]*\) -> i64 \{\n(.*)\n\}\s*$', src, re.DOTALL)
    return m.group(1) if m else None


def classify(body):
    used = set()
    for op in READ_OPS | MUT_OPS | ALLOC_OPS:
        if re.search(rf"(?<![\w]){op}\s*\(", body):
            used.add(op)
    lists = set(re.findall(r"\bL_[A-Z0-9_]+", body))
    if used & ALLOC_OPS:
        return "T3_RETIRE", used, lists
    if len(lists) > 1:
        return "T4_MULTI", used, lists
    if used & MUT_OPS:
        return "T2_SIMPLE_MUT", used, lists
    if used & READ_OPS:
        return "T1_PURE_READ", used, lists
    return "T0_NO_ADT", used, lists


def main():
    rows = []
    for f in sorted(os.listdir(V)):
        if not f.startswith("container_"):
            continue
        b = body_of(os.path.join(V, f))
        if b is None:
            rows.append((f, "PARSE_FAIL", set(), set()))
            continue
        t, used, lists = classify(b)
        rows.append((f, t, used, lists))
    tally = Counter(r[1] for r in rows)
    print(f"container candidates: {len(rows)}")
    for k, v in tally.most_common():
        print(f"  {k:15s} {v:4d}  ({100*v/len(rows):.0f}%)")
    print("\nop frequency:")
    ops = Counter(o for r in rows for o in r[2])
    for o, c in ops.most_common():
        print(f"  {o:12s} {c}")
    # the realizable-now tier: pure reads (reader-shaped) + single-list mutation
    now = [r for r in rows if r[1] == "T1_PURE_READ"]
    near = [r for r in rows if r[1] == "T2_SIMPLE_MUT"]
    print(f"\nREALIZABLE WITH READER MACHINERY (T1): {len(now)}")
    print(f"NEEDS list_del/list_add ONLY  (T2): {len(near)}")
    print(f"NEEDS allocator composition   (T3): {tally.get('T3_RETIRE',0)}")
    print(f"MULTI-LIST / token writes     (T4): {tally.get('T4_MULTI',0)}")
    for f, t, used, lists in now[:8]:
        print(f"   T1 e.g. {f.replace('container_','')[:70]}  ops={sorted(used)}")
    json.dump({"tally": dict(tally),
               "T1": [r[0] for r in rows if r[1] == "T1_PURE_READ"],
               "T2": [r[0] for r in rows if r[1] == "T2_SIMPLE_MUT"]},
              open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "container_feasibility.json"), "w"),
              indent=0)


if __name__ == "__main__":
    main()
