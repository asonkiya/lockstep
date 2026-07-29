#!/usr/bin/env python3
"""Struct-driven branch harness — the SOUND front gate (and the honest reach
measurement, since the count falls out of the real gate).

A function is verifiable by the pure-struct-reader differential (proof.py's
mechanism) iff ALL of:
  1. v1-parseable by cfg.py (if/else + guard returns; no loop/switch/goto).
  2. STRUCT-DRIVEN: at least one branch condition reads a struct field
     (`p->f` / `p.f`) — else it's a scalar-input branch, a different harness.
  3. PURE-STRUCT-READER: after masking legitimate reads through the function's
     own struct-pointer PARAMETERS, the body has no residual `->` (a pointer
     CHAIN or a non-param/global deref — memory we did not construct), no MMIO
     (readl/writel/ioread/...), no locks, and no opaque calls. Crucially, a body
     with no register read and no opaque call CANNOT have a device-state-
     dependent condition, so this single gate SUBSUMES the transitive-read
     obligation (a condition can only depend on struct fields + params + consts).
  4. CONSTRUCTIBLE: every struct-pointer parameter that is dereferenced has a
     mirrorable struct (mirror.mirror succeeds) — we must build an instance.

This corrects the earlier over-count (437: ignored purity) AND the scalar-purity
under-count (~35: purity.py flags EVERY `->` as impure, but reads through our
OWN struct parameters are exactly the input we construct, not impurity).
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "cluster"))
sys.path.insert(0, os.path.join(HERE, "..", "mmiogen"))
sys.path.insert(0, os.path.join(HERE, "..", "mirror"))
sys.path.insert(0, os.path.join(HERE, "..", "widerun"))
import cluster   # noqa: E402
import cfg       # noqa: E402
import mirror    # noqa: E402
import purity    # noqa: E402

KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")

# `struct X *p` / `struct X **p` params -> {p: X}. Also const-qualified.
_PARAM_STRUCT = re.compile(
    r"(?:const\s+)?struct\s+([A-Za-z_]\w*)\s*\*+\s*(?:const\s+)?([A-Za-z_]\w*)")


class Refused(Exception):
    pass


def param_struct_ptrs(sig_params: str) -> dict[str, str]:
    return {m.group(2): m.group(1) for m in _PARAM_STRUCT.finditer(sig_params)}


def _mask_param_fields(body: str, params: dict[str, str]) -> str:
    """Replace legitimate `p->field` / `p.field` reads (p a struct-ptr param)
    with a neutral token, so residual `->` marks a chain / non-param deref."""
    masked = body
    for p in params:
        masked = re.sub(rf"\b{re.escape(p)}\s*->\s*[A-Za-z_]\w*", " SRF ", masked)
        masked = re.sub(rf"\b{re.escape(p)}\s*\.\s*[A-Za-z_]\w*", " SRF ", masked)
    return masked


def sr_purity(body: str, params: dict[str, str], fn: str) -> None:
    """Raise Refused unless the body is a pure struct-reader over `params`."""
    masked = purity.mask(_mask_param_fields(body, params))
    m = purity.IMPURE.search(masked)          # catches residual `->`, MMIO, locks, ...
    if m:
        raise Refused(f"impure: {m.group(0)!r}")
    for c in purity.CALL.finditer(masked):    # opaque (non-pure) calls
        name = c.group(1)
        if name in purity.NONCALL or name in purity.PURE_CALL or name == fn:
            continue
        raise Refused(f"opaque call {name}()")


def _conds(blk: "cfg.Block") -> list[str]:
    out = []

    def rec(b):
        for it in b.items:
            if isinstance(it, cfg.Branch):
                out.append(it.cond)
                rec(it.then_blk)
                if it.else_blk:
                    rec(it.else_blk)
    rec(blk)
    return out


def _struct_field_cond(conds: list[str], params: dict[str, str]) -> bool:
    for c in conds:
        for p in params:
            if re.search(rf"\b{re.escape(p)}\s*(->|\.)\s*[A-Za-z_]\w*", c):
                return True
    return False


def _deref_params(body: str, params: dict[str, str]) -> set[str]:
    return {p for p in params if re.search(rf"\b{re.escape(p)}\s*(->|\.)", body)}


def gate(rel: str, fn: str) -> dict:
    """Run the full front gate on one function. Returns an accepted record or
    raises Refused with the first failing reason."""
    src = open(os.path.join(KSRC, rel), errors="ignore").read()
    try:
        text = cluster.functions(src)[fn]["text"]
    except Exception:
        raise Refused("no-source")
    # signature params (between the first '(' and its matching ')')
    op = text.find("(")
    depth, i = 0, op
    while i < len(text):
        depth += (text[i] == "(") - (text[i] == ")")
        if depth == 0:
            break
        i += 1
    sig_params = text[op + 1:i]
    body = text[text.find("{", i):]
    params = param_struct_ptrs(sig_params)
    if not params:
        raise Refused("no struct-ptr param")
    blk = cfg.build(text)                       # (1) v1-parseable (raises Unsupported)
    conds = _conds(blk)
    if not conds:
        raise Refused("no branches")
    if not _struct_field_cond(conds, params):  # (2) struct-driven
        raise Refused("no struct-field condition")
    sr_purity(body, params, fn)                # (3) pure struct-reader (subsumes transitive-read)
    # (4) constructible: mirror every dereferenced struct-ptr param
    deref = _deref_params(body, params)
    mirrored = {}
    for p in deref:
        st = params[p]
        try:
            near = os.path.join(KSRC, rel)
            ssrc = mirror.resolve_struct_source(st, near_file=near) or open(near, errors="ignore").read()
            mirror.mirror(ssrc, st, near_file=near)
            mirrored[p] = st
        except mirror.Unsupported as e:
            raise Refused(f"unmirrorable struct {st}: {str(e)[:40]}")
        except Exception as e:
            raise Refused(f"resolve-fail {st}: {type(e).__name__}")
    return {"file": rel, "fn": fn, "structs": sorted(set(mirrored.values())),
            "branches": len(conds)}


def _census_cf() -> list[tuple[str, str]]:
    """The MMIO recorder census (register functions). NOTE: these are impure by
    construction (they touch registers), so the pure-struct-reader gate accepts
    ZERO of them — the pure-differential class lives in general kernel helpers,
    NOT here. Retained for the record / the branch-aware-trace path."""
    path = os.path.join(HERE, "..", "overnight", "reports", "recorder_census", "sweep.jsonl")
    cf = []
    if os.path.exists(path):
        for line in open(path):
            r = json.loads(line)
            if r.get("verdict") == "REFUSED" and str(r.get("reason", "")).startswith("control flow"):
                cf.append((r["file"], r["fn"]))
    return cf


# Pure helpers cluster in these subsystems; drivers are sampled (mostly impure).
_BROAD_SUBS = ("lib", "kernel", "mm", "fs", "block", "crypto", "security",
               "net/core", "net/ipv4")


def _broad_corpus() -> list[tuple[str, str]]:
    import glob
    pairs = []
    files = []
    for sub in _BROAD_SUBS:
        files += glob.glob(os.path.join(KSRC, sub, "**", "*.c"), recursive=True)
    files += sorted(glob.glob(os.path.join(KSRC, "drivers", "**", "*.c"), recursive=True))[::12]
    for p in files:
        rel = os.path.relpath(p, KSRC)
        try:
            for fn in cluster.functions(open(p, errors="ignore").read()):
                pairs.append((rel, fn))
        except Exception:
            continue
    return pairs


def main() -> int:
    mode = os.environ.get("CORPUS", "broad")
    cf = _census_cf() if mode == "census" else _broad_corpus()
    limit = int(os.environ.get("LIMIT", "0"))
    if limit:
        cf = cf[:limit]
    tally = Counter()
    accepted = []
    for rel, fn in cf:
        try:
            accepted.append(gate(rel, fn))
            tally["ACCEPTED"] += 1
        except cfg.Unsupported:
            tally["refuse:not-v1-parseable"] += 1
        except Refused as e:
            key = re.sub(r"['\"].*", "", str(e)).strip()[:28]
            tally[f"refuse:{key}"] += 1
        except Exception as e:
            tally[f"ERROR:{type(e).__name__}"] += 1
    print(f"=== struct-driven pure-reader front gate over {len(cf)} fns (corpus={mode}) ===")
    for k, c in tally.most_common():
        print(f"  {c:5d}  {k}")
    print(f"\nACCEPTED (true pure-struct-reader-branch reach): {len(accepted)}")
    for a in accepted[:15]:
        print(f"  {a['fn']}  ({a['file']})  structs={a['structs']} branches={a['branches']}")
    out = os.path.join(HERE, "reach_accepted.json")
    json.dump(accepted, open(out, "w"), indent=1)
    print(f"\n-> {os.path.relpath(out)} ({len(accepted)} functions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
