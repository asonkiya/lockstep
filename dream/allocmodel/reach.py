#!/usr/bin/env python3
"""Allocator-init front gate — the honest reach measurement for PRODUCTIZING
the allocator-init oracle (proof.py proved the fresh-arena-slot mechanism; the
scratchpad probe measured 63 loosely-clean alloc-init fns in kernel+mm+lib).

The productized oracle models allocation as a FRESH ARENA SLOT: the real C runs
verbatim with k[mz]alloc* bump-allocating over a host arena of the allocated
struct; the Rust candidate produces the same fresh-slot sequence against a flat
cell model; the differential compares — after every call — the returned slot id
AND every footprint cell (allocated-object fields, globals, param-struct
fields). That claim is sound only if the gate can prove:

  1. Exactly ONE allocation site, of ONE resolvable struct type T, via the
     zeroing single-object forms (kzalloc/kzalloc_obj; kmalloc/kmalloc_obj is
     admitted but FLAGGED — the model zeroes both sides, so uninit-read
     equivalence is model-relative). Array/flex forms (kcalloc/_objs/_flex)
     are refused: one slot cannot model an indexed allocation.
  2. The function RETURNS `struct T *` (the fresh object escapes only via the
     return; NULL maps to id -1). Stores of the object into params/globals are
     refused v1 (that composition is ksdk-mirror territory).
  3. The alloc'd local is used only as: NULL-guard, `p->scalar_field`
     read/write, `return p`, `kfree(p)`.
  4. Every other call is a known-pure helper or a strippable lock/log bracket
     (stripped AND flagged, exactly like efftrace).
  5. Params are integer scalars or resolvable struct-of-scalars pointers;
     every other escaping identifier resolves to a file-scope scalar global or
     an object-like integer #define.

Soundness scope flags carried in the record: alloc_stripped (allocation always
succeeds — the failure path `if (!p)` is dead in the model; the verdict is the
SUCCESS-path init transition), kmalloc_zero_modeled (see 1), locks_stripped /
logging_stripped (as efftrace).

Everything refused is tallied by reason — worklist + measured backlog.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("cluster", "mirror", "widerun", "router"):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import cluster   # noqa: E402
import purity    # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "eff_reach_am", os.path.join(HERE, "..", "efftrace", "reach.py"))
E = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E)

KSRC = E.KSRC
Refused = E.Refused

# single-object zeroing forms -> modelable; kmalloc forms flagged; array/flex
# and copy/realloc forms refused (backlog, tallied). kmem_cache_[z]alloc is a
# single-object form too: the cache arg is discarded by the harness macro, so
# the cache global never reaches the compiled TU.
_ALLOC_ZERO = ("kzalloc", "kzalloc_obj", "kzalloc_node", "kmem_cache_zalloc")
_ALLOC_RAW = ("kmalloc", "kmalloc_obj", "kmalloc_node", "kmem_cache_alloc")
_ALLOC_REFUSE = re.compile(
    r"\b(kcalloc|kzalloc_objs|kmalloc_objs|kzalloc_flex|kmalloc_flex|"
    r"kvzalloc|kvmalloc|vmalloc|vzalloc|mempool_\w+|"
    r"devm_k\w+|kmemdup|kstrdup|krealloc)\s*\(")
# errno constants the harness header defines (EFF_H) — known without resolution.
_KNOWN_ERRNOS = {"EPERM", "ENOENT", "EIO", "EAGAIN", "ENOMEM", "EFAULT",
                 "EBUSY", "EEXIST", "ENODEV", "EINVAL", "ENOSPC"}
_ALLOC_CALL = re.compile(
    r"\b(" + "|".join(_ALLOC_ZERO + _ALLOC_RAW) + r")\s*\(")
_STRUCT_PTR_LOCAL = re.compile(r"\bstruct\s+([A-Za-z_]\w*)\s*\*\s*([A-Za-z_]\w*)\s*[;=]")
_CONTAINER = re.compile(r"\blist_\w+|\bhlist_\w+|\brb_\w+|\bxa_\w+|\bxas_\w+"
                        r"|\bidr_\w+|\bradix_\w+")


def _balanced_call(scan, start):
    """(argtext, end) for the call whose '(' is at scan.index('(', start)."""
    op = scan.index("(", start)
    depth, i = 0, op
    while i < len(scan):
        depth += (scan[i] == "(") - (scan[i] == ")")
        if depth == 0:
            break
        i += 1
    return scan[op + 1:i], i + 1


def gate(rel, fn, _cache={}):
    if rel not in _cache:
        _cache.clear()
        src = open(os.path.join(KSRC, rel), errors="ignore").read()
        _cache[rel] = (src, purity.mask(src))
    src, src_masked = _cache[rel]
    text = E._file_funcs(src).get(fn)
    if text is None:
        raise Refused("no-source")
    ret, params_str, body = E._sig_split(text)
    scan = purity.mask(body)

    if E._ASM.search(scan) or E._MMIO.search(scan):
        raise Refused("asm/mmio")
    if _CONTAINER.search(scan):
        raise Refused("container-op (container oracle territory)")
    if E._FORBID.search(scan):
        raise Refused(f"forbidden: {E._FORBID.search(scan).group(0)[:14]}")
    if _ALLOC_REFUSE.search(scan):
        raise Refused(f"alloc-form: {_ALLOC_REFUSE.search(scan).group(1)[:20]}")

    # ---- the allocation site: exactly one, single-object, one struct type ----
    sites = list(_ALLOC_CALL.finditer(scan))
    if not sites:
        raise Refused("no alloc site")
    if len(sites) > 1:
        raise Refused("multi-alloc")
    site = sites[0]
    zeroed = site.group(1) in _ALLOC_ZERO
    argtext, site_end = _balanced_call(scan, site.start())
    if re.search(r"=[^=]|\+\+|--", argtext):
        raise Refused("alloc-arg side effect")

    # the receiving local: `p = kzalloc*(...)` (with `struct T *p` declared) or
    # the direct `return kzalloc_obj(struct T, ...)` form.
    pre = scan[:site.start()]
    m = re.search(r"([A-Za-z_]\w*)\s*=\s*$", pre)
    ptr_locals = {n: t for t, n in
                  ((mm.group(1), mm.group(2)) for mm in _STRUCT_PTR_LOCAL.finditer(scan))}
    if m:
        pvar = m.group(1)
        if pvar not in ptr_locals:
            raise Refused(f"alloc target not a struct-ptr local: {pvar}")
        struct_t = ptr_locals[pvar]
    else:
        if not re.search(r"return\s*$", pre):
            raise Refused("alloc site neither assigned nor returned")
        # type from `kzalloc_obj(struct T, fl)` or `kzalloc(sizeof(struct T), fl)`
        tm = (re.match(r"\s*struct\s+([A-Za-z_]\w*)", argtext)
              or re.search(r"sizeof\s*\(\s*struct\s+([A-Za-z_]\w*)\s*\)", argtext))
        if not tm:
            raise Refused("direct-return alloc: type not in args")
        pvar, struct_t = "__direct__", tm.group(1)
    if any(n != pvar for n in ptr_locals):
        raise Refused(f"extra ptr local: {next(n for n in ptr_locals if n != pvar)}")

    if ret != f"struct {struct_t} *":
        raise Refused(f"ret: {ret[:24]!r} (need struct {struct_t} *)")
    afields = E._struct_scalar_fields(struct_t, os.path.join(KSRC, rel))

    # mask the alloc call + kfree/kmem_cache_free(p) so downstream scans see
    # plain tokens; ERR_PTR(-E) is a pure cast the harness defines — mask its
    # errno arg as the known constant it is.
    masked = scan[:site.start()] + " ALLOCCALL " + scan[site_end:]
    # decl-with-init `struct T *p = ALLOCCALL` -> decl; assignment (else the
    # `*p =` substring false-fires the deref-write check)
    masked = re.sub(rf"\bstruct\s+{struct_t}\s*\*\s*{re.escape(pvar)}\s*=\s*ALLOCCALL",
                    f"struct {struct_t} *{pvar}; {pvar} = ALLOCCALL", masked)
    masked = re.sub(rf"\bkfree\s*\(\s*{re.escape(pvar)}\s*\)", " KFREECALL ", masked)
    masked = re.sub(rf"\bkmem_cache_free\s*\([^()]*\b{re.escape(pvar)}\s*\)",
                    " KFREECALL ", masked)
    for em in re.finditer(r"\bERR_PTR\s*\(\s*-\s*([A-Za-z_]\w*)\s*\)", masked):
        if em.group(1) not in _KNOWN_ERRNOS:
            raise Refused(f"ERR_PTR arg: {em.group(1)[:20]}")
    masked = re.sub(r"\bERR_PTR\s*\(\s*(-\s*[A-Za-z_]\w*)\s*\)", r" ERRPTRCAST(\1) ", masked)
    # INIT_LIST_HEAD on a fresh object: the list field is not a modeled cell
    # either way, so stripping it (FLAGGED) claims the non-list state
    # transition; the list half composes with the container oracle. The
    # harness's variadic no-op macro discards the &p->list arg textually.
    n_li = len(re.findall(r"\bINIT_LIST_HEAD\s*\(\s*&\s*[A-Za-z_]\w*\s*->\s*[A-Za-z_]\w*\s*\)", masked))
    if n_li:
        masked = re.sub(r"\bINIT_LIST_HEAD\s*\(\s*&\s*[A-Za-z_]\w*\s*->\s*[A-Za-z_]\w*\s*\)",
                        " LISTINITCALL ", masked)
    if re.search(r"\bINIT_LIST_HEAD\b", masked):
        raise Refused("INIT_LIST_HEAD arg shape")

    # ---- calls: pure / lock-strip / log-strip only (no interproc v1) --------
    flags = {"locks_stripped": False, "logging_stripped": False,
             "alloc_stripped": True, "kmalloc_zero_modeled": not zeroed,
             "list_init_stripped": bool(n_li)}
    for cm in purity.CALL.finditer(masked):
        name = cm.group(1)
        if (name in purity.NONCALL or name in purity.PURE_CALL or name == fn
                or name == "ERRPTRCAST"):
            continue
        if name in E.LOCK_STRIP:
            flags["locks_stripped"] = True
            continue
        if name in E.LOG_STRIP:
            flags["logging_stripped"] = True
            continue
        raise Refused(f"opaque: {name}")
    for lk in E.LOCK_STRIP:
        masked = re.sub(rf"\b{lk}\s*\([^()]*\)", " LOCKCALL ", masked)

    inner = masked[masked.find("{") + 1:]
    if E._FN_STATIC.search(inner):
        raise Refused("fn-static (hidden state)")
    if E._PTR_LOCAL.search(inner):
        raise Refused("scalar pointer local")

    # ---- params: scalar | struct-of-scalars ptr (efftrace vocabulary) -------
    params, nodes = {}, {}
    for piece in [p.strip() for p in params_str.split(",") if p.strip()]:
        if piece == "void":
            continue
        sm = E._PARAM_STRUCT.match(piece)
        if sm:
            if sm.group(2) != "*":
                raise Refused(f"param: multi-star {piece!r}")
            nodes[sm.group(3)] = (sm.group(1),
                                  E._struct_scalar_fields(sm.group(1),
                                                          os.path.join(KSRC, rel)))
            params[sm.group(3)] = {"kind": "node", "struct": sm.group(1)}
            continue
        cm2 = E._SCALAR_PARAM.match(piece)
        gm = re.match(r"(?:const\s+)?gfp_t\s+([A-Za-z_]\w*)$", piece)
        if cm2 or gm:
            params[(cm2 or gm).group(1)] = {"kind": "scalar", "struct": None}
            continue
        raise Refused(f"param: {piece!r}")

    # ---- every field path: p->scalar(T) or param->scalar ---------------------
    for fm in E._FIELD_PATH.finditer(masked):
        var, arrow, fld = fm.group(1), fm.group(2), fm.group(3)
        full = re.sub(r"\s+", "", fm.group(0))
        if full.count("->") + full.count(".") > 1:
            raise Refused(f"field chain: {full[:24]}")
        if arrow != "->":
            raise Refused(f"by-value field: {full[:24]}")
        if var == pvar:
            if fld not in afields:
                raise Refused(f"alloc field {struct_t}.{fld}: not scalar")
        elif var in nodes:
            if fld not in nodes[var][1]:
                raise Refused(f"field path: {full[:24]}")
        else:
            raise Refused(f"field on non-param non-alloc: {full[:24]}")

    fp_masked = E._FIELD_PATH.sub(" NFLD ", masked)
    if re.search(r"\b[A-Za-z_]\w*\s*\[", fp_masked):
        raise Refused("array access")
    if re.search(r"\*\s*[A-Za-z_]\w*\s*=[^=]", fp_masked):
        raise Refused("deref write")
    if re.search(r"(?<![\w)\]&])&(?!&)\s*[A-Za-z_]", fp_masked):
        raise Refused("address-of")
    # the alloc'd local may not escape into anything but return/kfree/guards:
    # with fields, kfree and the alloc masked out, `pvar` may appear only in
    # `return pvar`, `!pvar`, `pvar ==/!= ...`, or a bare-truth if.
    if pvar != "__direct__":
        for om in re.finditer(rf"\b{re.escape(pvar)}\b", fp_masked):
            tail = fp_masked[om.end():om.end() + 4]
            head = fp_masked[max(0, om.start() - 12):om.start()]
            ok = (re.search(r"return\s+$|!\s*$|if\s*\(\s*$|struct\s+\w+\s*\*\s*$", head)
                  or re.match(r"\s*(=[^=]|==|!=|\)|;|,)", tail))
            if not ok:
                raise Refused(f"alloc local escapes: ...{head[-8:]}{pvar}{tail}")

    # ---- globals / defines ---------------------------------------------------
    owned = (purity.owned_names(text) | purity.KEYWORDS | _KNOWN_ERRNOS
             | {"NFLD", "LOCKCALL", "ALLOCCALL", "KFREECALL", "ERRPTRCAST",
                "LISTINITCALL", "NULL"})
    call_names = {m2.group(1) for m2 in purity.CALL.finditer(fp_masked)}
    globals_, defines = {}, {}
    for m2 in re.finditer(r"(?<![\w.>])([A-Za-z_]\w*)\b", fp_masked):
        n = m2.group(1)
        if (n in owned or n in call_names or n in globals_ or n in defines
                or n in purity.PURE_CALL or n == pvar or n == struct_t):
            continue
        g = E._global_decl(n, src_masked)
        if g:
            if g["init"] is None:
                raise Refused(f"global {n}: non-literal init")
            globals_[n] = g
            continue
        import mirror
        v = mirror._resolve_define(n, src)
        if v is not None:
            defines[n] = v
            continue
        raise Refused(f"unresolved: {n}")

    # ---- writes ---------------------------------------------------------------
    wafields, wfields, writes = set(), set(), set()
    for m2 in re.finditer(
            r"([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)\s*(?:=[^=]|\+\+|--|[-+*/|&^]=|<<=|>>=)",
            masked):
        if m2.group(1) == pvar:
            wafields.add(m2.group(2))
        else:
            wfields.add((m2.group(1), m2.group(2)))
    for rx in (purity._ASSIGN, purity._PREINC, purity._IDX_ASSIGN):
        for m2 in rx.finditer(masked):
            if m2.group(1) in globals_:
                writes.add(m2.group(1))

    return {
        "file": rel, "fn": fn, "ret": ret, "flags": flags,
        "alloc_struct": struct_t, "alloc_var": pvar,
        "alloc_fields": dict(sorted(afields.items())),
        "write_afields": sorted(wafields),
        "globals": {n: g for n, g in sorted(globals_.items())},
        "defines": dict(sorted(defines.items())),
        "write_globals": sorted(writes),
        "write_fields": sorted(f"{v}->{f}" for v, f in wfields),
        "branches": len(re.findall(r"\bif\s*\(|\bwhile\s*\(|\bfor\s*\(", masked)),
        "params": [{"name": p, "kind": params[p]["kind"],
                    "struct": params[p]["struct"],
                    **({"scalar_fields": nodes[p][1]} if p in nodes else {})}
                   for p in params],
    }


def main():
    import glob
    pairs = []
    for sub in ("kernel", "mm", "lib", "fs", "block", "crypto", "security",
                "net/core", "net/ipv4"):
        for pth in glob.glob(os.path.join(KSRC, sub, "**", "*.c"), recursive=True):
            rel = os.path.relpath(pth, KSRC)
            try:
                src = open(pth, errors="ignore").read()
                if not _ALLOC_CALL.search(src):
                    continue
                for f in cluster.functions(src):
                    pairs.append((rel, f))
            except Exception:
                continue
    tally = Counter()
    accepted = []
    examples = {}          # refusal class -> [(fn, file), ...] for the census
    for rel, f in pairs:
        try:
            src = open(os.path.join(KSRC, rel), errors="ignore").read()
            fntext = E._file_funcs(src).get(f, "")
            if not _ALLOC_CALL.search(purity.mask(fntext)):
                tally["no-alloc-in-fn"] += 1
                continue
            r = gate(rel, f)
            accepted.append(r)
            tally["ACCEPTED"] += 1
        except Refused as e:
            key = re.sub(r"['\"].*", "", str(e)).strip()[:30]
            tally[f"refuse:{key}"] += 1
            examples.setdefault(key, []).append((f, rel))
        except Exception as e:
            tally[f"ERROR:{type(e).__name__}"] += 1
    json.dump({k: v[:12] for k, v in examples.items()},
              open(os.path.join(HERE, "refusals.json"), "w"), indent=1)
    n_alloc = sum(c for k, c in tally.items() if k != "no-alloc-in-fn")
    print(f"=== allocator-init front gate: {n_alloc} alloc-touching fns ===")
    for k, c in tally.most_common(40):
        if k != "no-alloc-in-fn":
            print(f"  {c:6d}  {k}")
    print(f"\nACCEPTED (fresh-slot modelable alloc-init fns): {len(accepted)}")
    for a in accepted[:30]:
        print(f"  {a['fn']}  ({a['file']})  T={a['alloc_struct']} "
              f"wa={a['write_afields']} zeroed={not a['flags']['kmalloc_zero_modeled']}")
    out = os.path.join(HERE, "reach_accepted.json")
    json.dump(accepted, open(out, "w"), indent=1)
    print(f"\n-> {os.path.relpath(out)} ({len(accepted)} functions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
