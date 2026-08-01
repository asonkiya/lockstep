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


_SSF_CACHE = {}


def _struct_scalar_fields(struct, near):
    """{field: ctype} for the struct's SCALAR fields (non-scalar fields are
    fine as long as the fn never touches them — the host struct is emitted
    self-consistently from just the touched subset, no kernel layout needed).
    Refused only if the struct itself is unresolvable. Cached by (struct, dir)
    — resolve_struct_source globs include/ per miss, the interproc hot path."""
    key = (struct, os.path.dirname(near))
    hit = _SSF_CACHE.get(key)
    if hit is not None:
        if isinstance(hit, Refused):
            raise hit
        return hit
    try:
        src = mirror.resolve_struct_source(struct, near_file=near) or open(near, errors="ignore").read()
        fields = mirror.parse_struct(src, struct)
    except mirror.Unsupported as e:
        r = Refused(f"param-struct {struct}: {str(e)[:36]}")
        _SSF_CACHE[key] = r
        raise r
    except Exception as e:
        r = Refused(f"param-struct {struct}: {type(e).__name__}")
        _SSF_CACHE[key] = r
        raise r
    out = {}
    for ctype, fname, extra in fields:
        if ctype in ("__nested__", "__ptr__") or extra is not None:
            continue
        if ctype in mirror.SCALAR:
            out[fname] = ctype
    if not out:
        r = Refused(f"param-struct {struct}: no scalar fields")
        _SSF_CACHE[key] = r
        raise r
    _SSF_CACHE[key] = out
    return out


_FUNCS_CACHE = {}


def _file_funcs(src):
    """Parsed {name: text} for a file, cached by src identity — _resolve_callee
    is hit once per opaque call, and re-parsing the whole file each time was the
    dominant cost of the interprocedural scan."""
    key = id(src)
    hit = _FUNCS_CACHE.get(key)
    if hit is None or hit[0] is not src:
        _FUNCS_CACHE.clear()
        funcs = {}
        try:
            for n, f in cluster.functions(src).items():
                funcs[n] = f["text"] if isinstance(f, dict) else f
        except Exception:
            funcs = {}
        _FUNCS_CACHE[key] = (src, funcs)
        hit = _FUNCS_CACHE[key]
    return hit[1]


def _resolve_callee(name, src, src_masked, near):
    """INTERPROCEDURAL LADDER: admit a same-file, depth-1 (pure-builtin-only)
    callee whose footprint is bounded — file-globals AND/OR fields of a struct
    pointer param (the common `F(p) calls setter(p)` shape; global-only helpers
    measured ~0 real reach). Returns a dict {text, globals, defines, sparams}
    where sparams maps each struct-ptr param name -> (struct, {fields touched}),
    or None (not admissible -> caller refuses as opaque).

    Soundness: the reference TU inlines the callee's REAL C, the caller's cell
    vector is extended with the callee's global + struct-field footprint, and
    the differential compares the COMPLETE composite. The callee is never
    trusted; admission needs only a BOUNDED footprint + includable source."""
    text = _file_funcs(src).get(name)
    if text is None:
        return None
    cret, cparams_str, cbody = _sig_split(text)
    cscan = purity.mask(cbody)
    if _ASM.search(cscan) or _MMIO.search(cscan) or entangle._GRAPH.search(cscan):
        return None
    if _FORBID.search(cscan) or _FN_STATIC.search(cscan[cscan.find("{") + 1:]):
        return None
    if _PTR_LOCAL.search(cscan):
        return None
    for m in purity.CALL.finditer(cscan):
        nm = m.group(1)
        if nm not in purity.NONCALL and nm not in purity.PURE_CALL and nm != name:
            return None                  # depth-1 (pure builtins only)

    # struct-pointer params: each may be accessed only as q->scalar_field
    sparams = {}                          # pname -> (struct, {field: ctype})
    for piece in [p.strip() for p in cparams_str.split(",") if p.strip()]:
        if piece == "void":
            continue
        sm = _PARAM_STRUCT.match(piece)
        if sm and sm.group(2) == "*":
            try:
                sparams[sm.group(3)] = (sm.group(1),
                                        _struct_scalar_fields(sm.group(1), near))
            except Refused:
                return None
        elif _SCALAR_PARAM.match(piece):
            continue
        else:
            return None                  # unmodelable param (out-ptr, ptr-ptr, ...)

    # every field path must be q->scalar where q is a struct param; collect fields
    touched = {p: set() for p in sparams}
    for m in _FIELD_PATH.finditer(cscan):
        full = re.sub(r"\s+", "", m.group(0))
        if full.count("->") + full.count(".") > 1:
            return None                  # chain
        var, arrow, fld = m.group(1), m.group(2), m.group(3)
        if arrow != "->" or var not in sparams or fld not in sparams[var][1]:
            return None
        touched[var].add(fld)
    if re.search(r"\w+\s*\.\s*\w+", cscan):
        return None                      # by-value struct field access

    owned = purity.owned_names(text) | purity.KEYWORDS
    cglobals, cdefines = {}, {}
    body_scan = cscan[cscan.find("{"):]
    body_scan = _FIELD_PATH.sub(" NFLD ", body_scan)
    for m in re.finditer(r"(?<![\w.>])([A-Za-z_]\w*)\b", body_scan):
        n = m.group(1)
        if (n in owned or n in purity.PURE_CALL or n in cglobals or n in cdefines
                or n in sparams or n == name or n == "NFLD"):
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
    return {"text": text, "globals": cglobals, "defines": cdefines,
            "sparams": {p: (sparams[p][0], sorted(touched[p])) for p in sparams}}


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
    text = _file_funcs(src).get(fn)
    if text is None:
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

    near = os.path.join(KSRC, rel)

    # calls: admit same-file bounded helpers (interprocedural ladder); record
    # each admitted callee's resolve dict, fold globals/defines now, fold the
    # struct-field footprint AFTER params are parsed (needs the caller's nodes).
    flags = {"locks_stripped": False}
    inlined = {}                 # callee name -> real C text (ladder)
    inlined_globals = {}         # folded callee global footprint
    inlined_defines = {}         # folded callee #define usage
    admitted = {}                # callee name -> resolve dict (for field folding)
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
        res = _resolve_callee(name, src, src_masked, near) if interproc else None
        if res is not None:
            inlined[name] = res["text"]
            inlined_globals.update(res["globals"])
            inlined_defines.update(res["defines"])
            admitted[name] = res
            continue
        raise Refused(f"opaque: {name}")

    # params: scalar | struct-of-(touched-)scalars ptr | scalar OUT-param ptr
    params, nodes, outp = {}, {}, set()
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

    # fold each admitted callee's STRUCT-field footprint into the caller's node
    # cells: map the callee's struct param (positionally, via the call site) to
    # a caller struct param of the same type, and extend that node's tracked
    # scalar fields with the fields the callee touches. So a helper writing
    # p->field lands in a compared cell of F's own struct.
    inlined_wfields = set()
    for cname, res in admitted.items():
        if not res["sparams"]:
            continue
        cm2 = re.search(rf"\b{cname}\s*\(([^()]*)\)", scan)
        if cm2 is None:
            raise Refused(f"callee {cname}: call site not found")
        cargs = [a.strip() for a in cm2.group(1).split(",")] if cm2.group(1).strip() else []
        # positional map: which call-arg feeds each of the callee's params.
        # the callee sig order == sparams+scalars; recover order from the sig.
        _, csig, _ = _sig_split(inlined[cname])
        corder = [re.match(r".*?([A-Za-z_]\w*)$", p.strip()).group(1)
                  for p in csig.split(",") if p.strip() and p.strip() != "void"]
        for qname, (qstruct, qfields) in res["sparams"].items():
            try:
                argexpr = cargs[corder.index(qname)]
            except (ValueError, IndexError):
                raise Refused(f"callee {cname}: arg map for {qname}")
            fp = argexpr if re.fullmatch(r"[A-Za-z_]\w*", argexpr) else None
            if fp is None or fp not in nodes or nodes[fp][0] != qstruct:
                raise Refused(f"callee {cname}: {qname} not a caller {qstruct} param")
            all_fields = _struct_scalar_fields(qstruct, near)
            merged = dict(nodes[fp][1])
            for f in qfields:                      # all touched -> cells (seeded)
                if f in all_fields:
                    merged[f] = all_fields[f]
            nodes[fp] = (nodes[fp][0], merged)
            params[fp] = {"kind": "node", "struct": nodes[fp][0]}
            cmask = purity.mask(inlined[cname])
            for f in qfields:                      # only WRITTEN -> coverage target
                if re.search(rf"\b{qname}\s*->\s*{f}\s*(?:=[^=]|\+\+|--|[-+*/|&^]=|<<=|>>=)",
                             cmask):
                    inlined_wfields.add((fp, f))

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
    wfields = set(inlined_wfields)       # callee struct-field writes (folded)
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
