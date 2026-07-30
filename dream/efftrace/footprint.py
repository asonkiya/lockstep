#!/usr/bin/env python3
"""Effect-trace oracle — footprint extraction (the CGIR<->lockstep seam, honestly).

The oracle needs a function's STATE footprint: the set of locations it reads and
writes (globals/statics, and field/deref targets that escape the locals). This is
a data-flow question. CGIR's `effects` layer classifies effect KINDS
(io/fs/nondeterm/alloc/purity) — necessary as a boundedness GATE, but it does NOT
emit the location read/write set; that lives on CGIR's PDG / reaching_defs
substrate. So this module computes the footprint directly (write-set precisely,
read-set approximately), reusing purity.owned_names to separate transient locals
from escaping state, and marks a function's footprint BOUNDED (finite named scalar
locations -> effect-trace oracle applies) or UNBOUNDED (pointer-graph / alloc /
opaque-callee effects -> the unbounded_state fallback). CGIR's interprocedural
PDG is the refinement that would RECOVER functions this conservatively refutes on
a single opaque call — quantified in the report as the CGIR upside.

Run over the router's bounded_state class, it refines the 35.5% upper bound into
the effect-trace oracle's real reach.
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

# a field/deref write target, e.g.  p->count = / g.state |=  (one or more steps)
_FIELD_WRITE = re.compile(
    r"([A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)+)\s*(?:=[^=]|\+\+|--|[-+*/|&^]=|<<=|>>=)")
_FIELD_ANY = re.compile(r"[A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)+")
# a global array write with a NON-constant index -> unbounded external storage
_DYN_IDX_WRITE = re.compile(r"([A-Za-z_]\w*)\s*\[\s*([^\]]+?)\s*\]\s*(?:=[^=]|[-+|&^]=)")


def _opaque_calls(scan, owned, fn):
    out = set()
    for m in purity.CALL.finditer(scan):
        name = m.group(1)
        if name in purity.NONCALL or name in purity.PURE_CALL or name in owned or name == fn:
            continue
        out.add(name)
    return out


def own_footprint(func_text: str, fn: str = "") -> dict:
    """Direct (intraprocedural) footprint + the callee list, WITHOUT deciding on
    opaque calls — that decision is deferred to the interprocedural closure
    (interproc.py). local_hard=True means unbounded regardless of callees
    (pointer-graph / alloc / dynamic-index write)."""
    scan = purity.mask(func_text)
    hdr = scan.find("{")
    body = scan[hdr:] if hdr > 0 else scan
    owned = purity.owned_names(func_text) | purity.KEYWORDS

    writes: set[str] = set()
    for rx in (purity._ASSIGN, purity._PREINC):
        for m in rx.finditer(body):
            if m.group(1) not in owned:
                writes.add(m.group(1))
    for m in _FIELD_WRITE.finditer(body):
        writes.add(re.sub(r"\s+", "", m.group(1)))

    if entangle._GRAPH.search(body):
        return {"local_hard": True, "reason": "pointer-graph/alloc", "writes": writes, "callees": set()}
    dyn = [m for m in _DYN_IDX_WRITE.finditer(body)
           if m.group(1) not in owned and not re.fullmatch(r"\d+|0x[0-9a-fA-F]+", m.group(2).strip())]
    if dyn:
        return {"local_hard": True, "reason": "dynamic-index write", "writes": writes, "callees": set()}
    reads = {re.sub(r"\s+", "", m.group(0)) for m in _FIELD_ANY.finditer(body)}
    return {"local_hard": False, "reason": "", "writes": writes, "reads": reads,
            "callees": _opaque_calls(body, owned, fn)}


def extract(func_text: str, fn: str = "") -> dict:
    """Return {bounded, reads, writes, reason}. bounded => effect-trace applies."""
    scan = purity.mask(func_text)
    hdr = scan.find("{")
    body = scan[hdr:] if hdr > 0 else scan
    owned = purity.owned_names(func_text) | purity.KEYWORDS

    # WRITE set: bare-identifier writes that escape locals + field/deref writes.
    writes: set[str] = set()
    for rx in (purity._ASSIGN, purity._PREINC):
        for m in rx.finditer(body):
            if m.group(1) not in owned:
                writes.add(m.group(1))
    for m in _FIELD_WRITE.finditer(body):
        writes.add(re.sub(r"\s+", "", m.group(1)))

    # boundedness refutations (in priority order, each SOUND-conservative)
    if entangle._GRAPH.search(body):
        return {"bounded": False, "reason": "pointer-graph/alloc", "writes": writes, "reads": set()}
    dyn = [m for m in _DYN_IDX_WRITE.finditer(body)
           if m.group(1) not in owned and not re.fullmatch(r"\d+|0x[0-9a-fA-F]+", m.group(2).strip())]
    if dyn:
        return {"bounded": False, "reason": f"dynamic-index write ({dyn[0].group(1)}[{dyn[0].group(2)[:12]}])",
                "writes": writes, "reads": set()}
    opaque = _opaque_calls(body, owned, fn)
    if opaque:
        return {"bounded": False, "reason": f"opaque call(s) {sorted(opaque)[:3]} — CGIR interprocedural effects would refine",
                "writes": writes, "reads": set(), "opaque": sorted(opaque)}
    if not writes:
        return {"bounded": False, "reason": "no escaping write (read-only -> in-kernel diff, not effect-trace)",
                "writes": writes, "reads": set()}

    # READ set (approximate): escaping fields + non-owned bare idents referenced.
    reads = {re.sub(r"\s+", "", m.group(0)) for m in _FIELD_ANY.finditer(body)}
    for m in re.finditer(r"(?<![\w.>])([A-Za-z_]\w*)\b", body):
        n = m.group(1)
        if n not in owned and not n.isupper() and n not in writes:
            reads.add(n)
    return {"bounded": True, "reason": f"bounded footprint: {len(writes)} write loc(s)",
            "writes": writes, "reads": reads}


# ---------------------------------------------------------------------------

def scan_bounded_state():
    """Over the core, take the router's bounded_state class and refine each with
    the footprint extractor. Reports the effect-trace oracle's REAL reach."""
    import cluster
    tally = Counter()
    lost_to_opaque = 0
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
            if klass != "bounded_state":
                continue
            tally["bounded_state(router)"] += 1
            fp = extract(f["text"], name)
            if fp["bounded"]:
                tally["footprint_BOUNDED (effect-trace applies)"] += 1
                examples.setdefault("bounded", []).append(f"{name} ({rel}) w={sorted(fp['writes'])[:3]}")
            else:
                tally[f"refute: {fp['reason'].split(' — ')[0].split('(')[0].strip()}"] += 1
                if "opaque call" in fp["reason"]:
                    lost_to_opaque += 1
    return tally, examples, lost_to_opaque


def main() -> int:
    tally, examples, lost = scan_bounded_state()
    bs = tally.get("bounded_state(router)", 0)
    ok = tally.get("footprint_BOUNDED (effect-trace applies)", 0)
    print(f"=== effect-trace footprint refinement of the bounded_state class ({bs} fns) ===\n")
    for k, c in tally.most_common():
        print(f"  {c:6d}  {k}")
    print(f"\n  footprint-BOUNDED (effect-trace oracle real reach): {ok}/{bs} "
          f"= {100*ok/bs:.1f}% of bounded_state" if bs else "  (no bounded_state fns found)")
    print(f"  refuted on a single opaque call (CGIR interprocedural effects would "
          f"RECOVER much of this): {lost}")
    for ex in examples.get("bounded", [])[:8]:
        print(f"    e.g. {ex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
