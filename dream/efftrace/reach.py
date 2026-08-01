#!/usr/bin/env python3
"""Effect-trace front gate — the honest reach measurement for PRODUCTIZING the
effect-trace oracle over the bounded_state class (proof.py proved the ordered
record/replay mechanism; footprint.py measured boundedness; this gate measures
which real functions the productized HARNESS can host-lift and gate).

The productized oracle is a per-call FULL-FOOTPRINT state differential: run the
real C verbatim, compare every footprint cell + the return value against the
Rust state-model candidate after every call. That claim is sound only if the
footprint is COMPLETE and every cell is HOST-DECLARABLE, so the gate requires:

  1. Effectful, non-graph, non-mmio, non-asm (the router's bounded_state shape;
     container/alloc signals are the container/allocator oracles' territory).
  2. Every call is a known-pure helper or a strippable lock bracket (stripped
     AND flagged — single-threaded host; the verdict is the state-transition
     half, the locking half is concgate's composition claim).
  3. Every parameter is an integer scalar or a resolvable struct-of-scalars
     pointer (its fields become model cells).
  4. Every field path in the body is `param->scalar_field`.
  5. Every other escaping identifier resolves to a file-scope scalar global
     (type + integer initializer parsed — the harness owns reset) or an
     object-like integer #define (inlined as a constant).
  6. No fn-scope statics (hidden unreadable state), no arrays, no pointer
     locals, no volatile/atomic/ONCE.
  Control flow is UNRESTRICTED (loops/switch fine): the C compiles verbatim;
  only translation difficulty varies, and the gate arbitrates that.

Everything refused is tallied by reason — worklist + measured v2 backlog.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("cluster", "mirror", "widerun", "router"):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import cluster   # noqa: E402
import mirror    # noqa: E402
import purity    # noqa: E402
import entangle  # noqa: E402

KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")

LOCK_STRIP = {
    "spin_lock", "spin_unlock", "spin_lock_irqsave", "spin_unlock_irqrestore",
    "spin_lock_irq", "spin_unlock_irq", "spin_lock_bh", "spin_unlock_bh",
    "raw_spin_lock", "raw_spin_unlock", "raw_spin_lock_irqsave",
    "raw_spin_unlock_irqrestore", "mutex_lock", "mutex_unlock",
    "lockdep_assert_held", "assert_spin_locked",
}
_ASM = re.compile(r"\basm\s+goto\b|\basm\s+volatile\b|\b__asm__\b|(?<![A-Za-z_])asm\s*\(")
_MMIO = re.compile(r"\b(readl|writel|read[bwq]|write[bwq]|ioread\d*|iowrite\d*)\b")
_FORBID = re.compile(
    r"\bWRITE_ONCE\b|\bREAD_ONCE\b|\batomic_|\brefcount_|\bkref_|\bxchg\b|cmpxchg"
    r"|\bvolatile\b|\bjiffies\b|\bktime|random|\bcurrent\b|this_cpu|per_cpu"
    r"|\bpr_\w+\s*\(|\bprintk\b|\bWARN|\bBUG\b|container_of|\bgoto\b")

_SCALAR_TYPES = (
    r"(?:unsigned\s+|signed\s+)?"
    r"(?:int|long|short|char|bool|size_t|ssize_t|u8|u16|u32|u64|s8|s16|s32|s64|"
    r"uint8_t|uint16_t|uint32_t|uint64_t|int8_t|int16_t|int32_t|int64_t)"
    r"(?:\s+long)?(?:\s+int)?")
_PARAM_STRUCT = re.compile(
    r"(?:const\s+)?struct\s+([A-Za-z_]\w*)\s*(\*+)\s*(?:const\s+)?([A-Za-z_]\w*)$")
_SCALAR_PARAM = re.compile(rf"(?:const\s+)?{_SCALAR_TYPES}\s+([A-Za-z_]\w*)$")
_FIELD_PATH = re.compile(r"([A-Za-z_]\w*)\s*(->|\.)\s*([A-Za-z_]\w*)"
                         r"(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)*")
_FN_STATIC = re.compile(r"\bstatic\s+")
_PTR_LOCAL = re.compile(rf"\b{_SCALAR_TYPES}\s*\*")

INT_RETURNS = {"void", "int", "bool", "unsigned", "unsigned int", "long",
               "unsigned long", "u8", "u16", "u32", "u64", "s8", "s16", "s32",
               "s64", "size_t", "ssize_t", "short", "char"}


class Refused(Exception):
    pass


def _sig_split(text):
    op = text.find("(")
    depth, i = 0, op
    while i < len(text):
        depth += (text[i] == "(") - (text[i] == ")")
        if depth == 0:
            break
        i += 1
    head = text[:op].strip()
    m = re.match(r"(.*?)([A-Za-z_]\w*)$", head, re.DOTALL)
    ret = re.sub(r"\b(static|inline|__always_inline|noinline|__init|__exit|"
                 r"__maybe_unused|__must_check)\b", " ", m.group(1) if m else "")
    return " ".join(ret.split()), text[op + 1:i], text[text.find("{", i):]


def _struct_scalar_fields(struct, near):
    """{field: ctype} for the struct's SCALAR fields (non-scalar fields are
    fine as long as the fn never touches them — the host struct is emitted
    self-consistently from just the touched subset, no kernel layout needed).
    Refused only if the struct itself is unresolvable."""
    try:
        src = mirror.resolve_struct_source(struct, near_file=near) or open(near, errors="ignore").read()
        fields = mirror.parse_struct(src, struct)
    except mirror.Unsupported as e:
        raise Refused(f"param-struct {struct}: {str(e)[:36]}")
    except Exception as e:
        raise Refused(f"param-struct {struct}: {type(e).__name__}")
    out = {}
    for ctype, fname, extra in fields:
        if ctype in ("__nested__", "__ptr__") or extra is not None:
            continue
        if ctype in mirror.SCALAR:
            out[fname] = ctype
    if not out:
        raise Refused(f"param-struct {struct}: no scalar fields")
    return out


def _resolve_callee(name, src, src_masked):
    """INTERPROCEDURAL LADDER (v1): admit a same-file callee whose footprint is
    pure or global-only and depth-1 (calls only pure builtins). Returns
    (text, globals_dict) or None (not admissible -> caller refuses as opaque).

    Soundness: the reference TU inlines the callee's REAL C, F's cell vector is
    extended with the callee's global footprint (reads+writes), and the
    differential compares the COMPLETE composite footprint. So even an untrusted
    callee is safe — a wrong composite translation diverges; we only require the
    footprint be BOUNDED (completeness) and the source includable (fidelity)."""
    try:
        text = cluster.functions(src)[name]
    except Exception:
        return None
    text = text["text"] if isinstance(text, dict) else text
    cret, _cparams, cbody = _sig_split(text)
    cscan = purity.mask(cbody)
    if _ASM.search(cscan) or _MMIO.search(cscan) or entangle._GRAPH.search(cscan):
        return None
    if _FORBID.search(cscan) or _FN_STATIC.search(cscan[cscan.find("{") + 1:]):
        return None
    if _PTR_LOCAL.search(cscan) or "->" in cscan or re.search(r"\w+\s*\.\s*\w+", cscan):
        return None                      # v1: no struct params / field access
    for m in purity.CALL.finditer(cscan):
        nm = m.group(1)
        if nm not in purity.NONCALL and nm not in purity.PURE_CALL and nm != name:
            return None                  # v1: depth-1 (pure builtins only)
    owned = purity.owned_names(text) | purity.KEYWORDS
    cglobals, cdefines = {}, {}
    for m in re.finditer(r"(?<![\w.>])([A-Za-z_]\w*)\b", cscan[cscan.find("{"):]):
        n = m.group(1)
        if (n in owned or n in purity.PURE_CALL or n in cglobals
                or n in cdefines or n == name):
            continue
        if re.search(rf"\b{n}\s*\(", cscan):     # a call, not a var
            continue
        g = _global_decl(n, src_masked)
        if g is not None and g["init"] is not None:
            cglobals[n] = g
            continue
        v = mirror._resolve_define(n, src)       # a #define the callee uses
        if v is not None:
            cdefines[n] = v
            continue
        return None                              # unresolvable -> not admissible
    return text, cglobals, cdefines


# file-scope (column-0) scalar global with an optional integer initializer
def _global_decl(name, src_masked):
    m = re.search(
        rf"^(?:static\s+)?({_SCALAR_TYPES})\s+{re.escape(name)}\s*(?:=\s*([^;,]+))?;",
        src_masked, re.M)
    if not m:
        return None
    init = 0
    if m.group(2):
        try:
            init = int(m.group(2).strip().rstrip("uUlL") or "0", 0)
        except ValueError:
            init = None
    return {"ctype": " ".join(m.group(1).split()), "init": init}


def gate(rel, fn, _cache={}):
    if rel not in _cache:
        _cache.clear()
        src = open(os.path.join(KSRC, rel), errors="ignore").read()
        _cache[rel] = (src, purity.mask(src))
    src, src_masked = _cache[rel]
    try:
        text = cluster.functions(src)[fn]["text"]
    except Exception:
        raise Refused("no-source")
    ret, params_str, body = _sig_split(text)
    scan = purity.mask(body)

    if _ASM.search(scan) or _MMIO.search(scan):
        raise Refused("asm/mmio")
    if entangle._GRAPH.search(scan):
        raise Refused("graph/alloc (container/allocator territory)")
    if _FORBID.search(scan):
        raise Refused(f"forbidden: {_FORBID.search(scan).group(0)[:14]}")
    if ret not in INT_RETURNS:
        raise Refused(f"ret: {ret[:24]!r}")
    inner = scan[scan.find("{") + 1:]
    if _FN_STATIC.search(inner):
        raise Refused("fn-static (hidden state)")
    if _PTR_LOCAL.search(inner):
        raise Refused("pointer local")

    # calls
    flags = {"locks_stripped": False}
    inlined = {}                 # callee name -> real C text (ladder)
    inlined_globals = {}         # folded callee global footprint
    inlined_defines = {}         # folded callee #define usage
    interproc = os.environ.get("INTERPROC", "1") != "0"
    for m in purity.CALL.finditer(scan):
        name = m.group(1)
        if name in purity.NONCALL or name in purity.PURE_CALL or name == fn:
            continue
        if name in LOCK_STRIP:
            flags["locks_stripped"] = True
            continue
        if name in inlined:
            continue
        res = _resolve_callee(name, src, src_masked) if interproc else None
        if res is not None:
            inlined[name] = res[0]
            inlined_globals.update(res[1])
            inlined_defines.update(res[2])
            continue
        raise Refused(f"opaque: {name}")

    # params: scalar | struct-of-(touched-)scalars ptr | scalar OUT-param ptr
    params, nodes, outp = {}, {}, set()
    near = os.path.join(KSRC, rel)
    for piece in [p.strip() for p in params_str.split(",") if p.strip()]:
        if piece == "void":
            continue
        sm = _PARAM_STRUCT.match(piece)
        if sm:
            if sm.group(2) != "*":
                raise Refused(f"param: multi-star {piece!r}")
            nodes[sm.group(3)] = (sm.group(1),
                                  _struct_scalar_fields(sm.group(1), near))
            params[sm.group(3)] = {"kind": "node", "struct": sm.group(1)}
            continue
        om = re.match(rf"({_SCALAR_TYPES})\s*\*\s*([A-Za-z_]\w*)$", piece)
        if om:
            outp.add(om.group(2))
            params[om.group(2)] = {"kind": "outp",
                                   "ctype": " ".join(om.group(1).split())}
            continue
        cm = _SCALAR_PARAM.match(piece)
        if cm:
            params[cm.group(1)] = {"kind": "scalar", "struct": None}
            continue
        raise Refused(f"param: {piece!r}")

    # strip lock brackets, mask out-param derefs, then resolve every field
    # path + escaping identifier
    masked = scan
    for lk in LOCK_STRIP:
        masked = re.sub(rf"\b{lk}\s*\([^()]*\)", " LOCKCALL ", masked)
    op_tok = {}
    for i, name in enumerate(sorted(outp)):
        tok = f"OPCELL{i}"
        op_tok[tok] = name
        # `* name` in expression context can only be the deref (a pointer can't
        # be a multiplication operand); param decls are outside the body scan.
        masked = re.sub(rf"\*\s*{re.escape(name)}\b", f" {tok} ", masked)
    for tok, name in op_tok.items():
        if re.search(rf"\b{name}\b(?!\s*(==|!=|\)|,|;|&&|\|\|))", masked):
            raise Refused(f"out-param arithmetic: {name}")
    for m in _FIELD_PATH.finditer(masked):
        var, arrow, fld = m.group(1), m.group(2), m.group(3)
        full = re.sub(r"\s+", "", m.group(0))
        if full.count("->") + full.count(".") > 1:
            raise Refused(f"field chain: {full[:24]}")
        if arrow != "->" or var not in nodes or fld not in nodes[var][1]:
            raise Refused(f"field path: {full[:24]}")
    fp_masked = _FIELD_PATH.sub(" NFLD ", masked)
    if re.search(r"\b[A-Za-z_]\w*\s*\[", fp_masked):
        raise Refused("array access")
    if re.search(r"\*\s*[A-Za-z_]\w*\s*=[^=]", fp_masked):
        raise Refused("deref write")
    # address-of: `&x` with no left operand (binary AND has one; `&&` excluded)
    if re.search(r"(?<![\w)\]&])&(?!&)\s*[A-Za-z_]", fp_masked):
        raise Refused("address-of")

    owned = (purity.owned_names(text) | purity.KEYWORDS
             | {"NFLD", "LOCKCALL"} | set(op_tok))
    call_names = {m.group(1) for m in purity.CALL.finditer(fp_masked)}
    globals_, defines = {}, {}
    for m in re.finditer(r"(?<![\w.>])([A-Za-z_]\w*)\b", fp_masked):
        n = m.group(1)
        if (n in owned or n in call_names or n in globals_ or n in defines
                or n in purity.PURE_CALL):
            continue
        g = _global_decl(n, src_masked)
        if g:
            if g["init"] is None:
                raise Refused(f"global {n}: non-literal init")
            globals_[n] = g
            continue
        v = mirror._resolve_define(n, src)
        if v is not None:
            defines[n] = v
            continue
        raise Refused(f"unresolved: {n}")

    # fold each inlined callee's global footprint into the caller's cells, so
    # the differential compares the COMPLETE composite state (an effect the
    # callee has on a global the caller's own body never mentions is now a
    # seeded, compared cell — closes the over-credit hole).
    for gn, gd in inlined_globals.items():
        globals_.setdefault(gn, gd)
    for dn, dv in inlined_defines.items():
        defines.setdefault(dn, dv)

    # effectful? at least one escaping write (global, param field, out-param).
    # a global a callee WRITES counts as an effect of the composite.
    writes = set()
    for rx in (purity._ASSIGN, purity._PREINC, purity._IDX_ASSIGN):
        for m in rx.finditer(masked):
            if m.group(1) in globals_:
                writes.add(m.group(1))
    for cname, ctext in inlined.items():
        cmask = purity.mask(ctext)
        for rx in (purity._ASSIGN, purity._PREINC, purity._IDX_ASSIGN):
            for m in rx.finditer(cmask):
                if m.group(1) in globals_:
                    writes.add(m.group(1))
    wfields = set()
    for m in re.finditer(
            r"([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)\s*(?:=[^=]|\+\+|--|[-+*/|&^]=|<<=|>>=)",
            masked):
        wfields.add((m.group(1), m.group(2)))
    opw = {name for tok, name in op_tok.items()
           if re.search(rf"\b{tok}\s*(=[^=]|\+\+|--|[-+*/|&^]=|<<=|>>=)", masked)}
    if not writes and not wfields and not opw:
        raise Refused("no escaping write (pure/reader)")

    return {
        "file": rel, "fn": fn, "ret": ret or "void", "flags": flags,
        "globals": {n: g for n, g in sorted(globals_.items())},
        "defines": dict(sorted(defines.items())),
        "write_globals": sorted(writes),
        "write_outp": sorted(opw),
        "write_fields": sorted(f"{v}->{f}" for v, f in wfields),
        "inlined_callees": inlined,      # name -> real C text (ladder rungs)
        "branches": len(re.findall(r"\bif\s*\(|\bwhile\s*\(|\bfor\s*\(", masked)),
        "params": [{"name": p, "kind": params[p]["kind"],
                    "struct": params[p]["struct"],
                    **({"scalar_fields": nodes[p][1]} if p in nodes else {})}
                   for p in params],
    }


_BROAD_SUBS = ("lib", "kernel", "mm", "fs", "block", "crypto", "security",
               "net/core", "net/ipv4")


def _broad_corpus():
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


def main():
    pairs = _broad_corpus()
    limit = int(os.environ.get("LIMIT", "0"))
    if limit:
        pairs = pairs[:limit]
    tally = Counter()
    accepted = []
    for rel, fn in pairs:
        try:
            r = gate(rel, fn)
            accepted.append(r)
            tally["ACCEPTED"] += 1
        except Refused as e:
            key = re.sub(r"['\"].*", "", str(e)).strip()[:30]
            tally[f"refuse:{key}"] += 1
        except Exception as e:
            tally[f"ERROR:{type(e).__name__}"] += 1
    print(f"=== effect-trace front gate over {len(pairs)} fns ===")
    for k, c in tally.most_common(35):
        print(f"  {c:6d}  {k}")
    print(f"\nACCEPTED (host-liftable bounded-state fns): {len(accepted)}")
    for a in accepted[:25]:
        print(f"  {a['fn']}  ({a['file']})  wg={a['write_globals']} "
              f"wf={a['write_fields']} locks={a['flags']['locks_stripped']}")
    out = os.path.join(HERE, "reach_accepted.json")
    json.dump(accepted, open(out, "w"), indent=1)
    print(f"\n-> {os.path.relpath(out)} ({len(accepted)} functions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
