#!/usr/bin/env python3
"""Container-ADT front gate — the honest reach measurement for PRODUCTIZING the
LIST-idiom ADT oracle (proof.py proved the mechanism; this measures which REAL
kernel functions the productized harness can host-lift and gate).

v1 (straight-line add/del/move over params only) measured ~ZERO real .c targets:
trivial list manipulators live in headers as inlines. Real mutators iterate,
anchor on static-global LIST_HEADs, run under mutex/spin brackets, and retire
nodes with kfree. So the v2 gate speaks that vocabulary — each extension either
preserves the ADT claim exactly or is STRIPPED-AND-FLAGGED with the composition
claim named:

  * list_for_each_entry[_safe][_reverse] — ADT-native (walk the sequence).
  * anchors: `struct list_head *` params, `&node->lh_field`, local
    `LIST_HEAD(x)`, file-static `LIST_HEAD(g)` (harness owns init).
  * spin/mutex brackets + lockdep asserts: stripped + flagged `locks_stripped`
    (host is single-threaded; verdict = container-transition half; locking half
    is concgate's composition claim). RCU variants NOT strippable.
  * kfree(node_cursor): arena no-op + flagged `alloc_stripped` (verdict = ADT
    transition; leak/UAF half is the allocator-model's claim).
  * node structs: embedded list_head + scalars + pointer fields AS OPAQUE
    TOKENS (read/compare only — token writes and token arithmetic refused).
  * unresolvable-struct pointer params as opaque tokens: identity compare and
    scalar field READS (each read field becomes a harness-supplied input,
    flagged `token_field_types_assumed`).

Anything else is refused and TALLIED by reason — the output is a worklist AND
the measured backlog for v3 (list_entry/list_first_entry, splice, allocation,
raw loops).
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("cluster", "mirror", "widerun"):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import cluster   # noqa: E402
import mirror    # noqa: E402
import purity    # noqa: E402

KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")

# closed op vocabulary: op -> (entry-arg indices, head-arg indices)
OPS = {
    "list_add": ((0,), (1,)), "list_add_tail": ((0,), (1,)),
    "list_del": ((0,), ()), "list_del_init": ((0,), ()),
    "list_move": ((0,), (1,)), "list_move_tail": ((0,), (1,)),
    "list_empty": ((), (0,)), "INIT_LIST_HEAD": ((), (0,)),
}
MUT_OPS = {"list_add", "list_add_tail", "list_del", "list_del_init",
           "list_move", "list_move_tail"}
ITERS = {"list_for_each_entry", "list_for_each_entry_safe",
         "list_for_each_entry_reverse"}
# v3: the pop/peek idiom — element extraction by position. _or_null returns
# NULL on empty (maps to id -1); bare first/last on an empty list is the C's
# own UB territory (kernel callers guard with list_empty).
PEEKS = {"list_first_entry", "list_last_entry", "list_first_entry_or_null"}
_PEEK = re.compile(r"\b(list_first_entry_or_null|list_first_entry|list_last_entry)"
                   r"\s*\(\s*([^,]+?)\s*,\s*struct\s+(\w+)\s*,\s*(\w+)\s*\)")
_NODE_DECL_INIT = re.compile(
    r"\bstruct\s+([A-Za-z_]\w*)\s*\*\s*([A-Za-z_]\w*)\s*=")
_RET_NODE = re.compile(r"^(?:const\s+)?struct\s+([A-Za-z_]\w*)\s*\*$")
# single-threaded host: bracket ops strippable; ADT verdict = container half only
LOCK_STRIP = {
    "spin_lock", "spin_unlock", "spin_lock_irqsave", "spin_unlock_irqrestore",
    "spin_lock_irq", "spin_unlock_irq", "spin_lock_bh", "spin_unlock_bh",
    "raw_spin_lock", "raw_spin_unlock", "raw_spin_lock_irqsave",
    "raw_spin_unlock_irqrestore", "mutex_lock", "mutex_unlock",
    "lockdep_assert_held", "assert_spin_locked",
}
# the rest of the container family: refused but TALLIED as the v3 backlog
_V2 = re.compile(r"^(list_|hlist_|llist_|klist_|rb_|rbtree_|xa_|xas_|idr_|ida_|"
                 r"radix_tree_|plist_)")

_ASM = re.compile(r"\basm\s+goto\b|\basm\s+volatile\b|\b__asm__\b|(?<![A-Za-z_])asm\s*\(")
_MMIO = re.compile(r"\b(readl|writel|read[bwq]|write[bwq]|ioread\d*|iowrite\d*)\b")
_IMPURE_RESIDUAL = re.compile(
    r"\bWRITE_ONCE\b|\bREAD_ONCE\b|\batomic_|\brefcount_|\bkref_|\bxchg\b|cmpxchg"
    r"|\bjiffies\b|\bktime|random|\bcurrent\b|this_cpu|per_cpu|\bpr_\w+\s*\(|\bprintk\b"
    r"|\bWARN|\bBUG\b|container_of|\bk[mzv]alloc")
_RAW_FLOW = re.compile(r"\bfor\s*\(|\bwhile\s*\(|\bswitch\s*\(|\bgoto\b|\bdo\s*\{")

_PARAM_STRUCT = re.compile(
    r"(?:const\s+)?struct\s+([A-Za-z_]\w*)\s*(\*+)\s*(?:const\s+)?([A-Za-z_]\w*)$")
_SCALAR_PARAM = re.compile(
    r"(?:const\s+)?(?:unsigned\s+|signed\s+)?"
    r"(?:int|long|short|char|bool|size_t|ssize_t|u8|u16|u32|u64|s8|s16|s32|s64)"
    r"(?:\s+long)?(?:\s+int)?\s+([A-Za-z_]\w*)$")
_NODE_DECL = re.compile(
    r"\bstruct\s+([A-Za-z_]\w*)\s*(\*\s*[A-Za-z_]\w*(?:\s*,\s*\*\s*[A-Za-z_]\w*)*)\s*;")
_LISTHEAD_LOCAL = re.compile(r"\bLIST_HEAD\s*\(\s*([A-Za-z_]\w*)\s*\)")
_LFE = re.compile(r"\b(list_for_each_entry(?:_safe|_reverse)?)\s*\(([^()]*)\)")
_OP_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(([^()]*)\)")

INT_RETURNS = {"void", "int", "bool", "unsigned", "unsigned int", "long",
               "unsigned long", "u8", "u16", "u32", "u64", "s8", "s16", "s32",
               "s64", "size_t", "ssize_t", "short", "char"}


class Refused(Exception):
    pass


def _sig_split(text):
    """(ret_type, params_str, body) from a function definition."""
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
    ret = " ".join(ret.split())
    return ret, text[op + 1:i], text[text.find("{", i):]


def _call_args(argstr):
    if "(" in argstr or ")" in argstr:
        raise Refused("complex op arg (nested call)")
    return [a.strip() for a in argstr.split(",")] if argstr.strip() else []


_NF_CACHE: dict = {}


def _node_fields(struct, near):
    """(lh_fields, scalar_fields{name: ctype}, token_fields[list]) or Refused.

    Cached: resolve_struct_source globs $KSRC/include per MISS, which dominated
    the corpus scan. Key is (struct, near) when the def is local to the file,
    else (struct, dir(near)) — matching resolve_struct_source's lookup order
    (near file -> near dir -> include/), so a cache hit is the same resolution.
    """
    try:
        nsrc = open(near, errors="ignore").read()
    except OSError:
        nsrc = ""
    local = re.search(rf"\bstruct\s+{re.escape(struct)}\s*\{{", nsrc)
    key = (struct, near) if local else (struct, os.path.dirname(near))
    if key in _NF_CACHE:
        hit = _NF_CACHE[key]
        if isinstance(hit, Refused):
            raise hit
        return hit
    try:
        r = _node_fields_uncached(struct, near)
    except Refused as e:
        _NF_CACHE[key] = e
        raise
    _NF_CACHE[key] = r
    return r


def _node_fields_uncached(struct, near):
    """LENIENT per-field resolution: the ADT harness emits its own host struct
    from the fields the fn TOUCHES, so unmodelable fields (nested structs,
    arrays, #if-conditional, bitfields, unions) are SKIPPED, not fatal — the
    later unknown-field check refuses soundly if the fn actually touches one.
    Only 'no resolvable source' and 'no embedded list_head' remain fatal.
    (The strict whole-struct version measured folio/inode/btrfs_* as walls the
    fn never actually looked behind.)"""
    try:
        src = mirror.resolve_struct_source(struct, near_file=near) or open(near, errors="ignore").read()
        body = mirror._extract_body(purity.mask(src), struct)
    except (mirror.Unsupported, Exception) as e:
        raise Refused(f"node-struct {struct}: {str(e)[:36] or type(e).__name__}")
    lh, scalars, tokens = [], {}, []
    seen = set()
    for raw in body.split(";"):
        decl = mirror.norm(re.sub(r"/\*.*?\*/", " ", raw, flags=re.DOTALL))
        if not decl or ":" in decl or re.search(r"\bunion\b", decl):
            continue
        for one in mirror._split_multi_decl(decl):
            try:
                ctype, fname, extra = mirror._field_from_decl(one, src)
            except (mirror.Unsupported, Exception):
                continue
            if fname in seen:          # #if branches both included: keep first
                continue
            seen.add(fname)
            if ctype == "__nested__":
                yname, n = extra
                if yname == "list_head" and n is None:
                    lh.append(fname)
                continue                       # other nested: skipped
            if ctype == "__ptr__":
                tokens.append(fname)           # opaque token: read/compare only
                continue
            if extra is None and ctype in mirror.SCALAR:
                scalars[fname] = ctype
    if not lh:
        raise Refused(f"node-struct {struct}: no embedded list_head")
    return lh, scalars, tokens


def _file_globals(src):
    """Names of file-level [static] LIST_HEAD(g) anchors."""
    return set(re.findall(r"^(?:static\s+)?LIST_HEAD\s*\(\s*(\w+)\s*\)",
                          purity.mask(src), re.M))


def gate(rel, fn, _srccache={}):
    if rel not in _srccache:
        _srccache.clear()   # keep one file hot; corpus iterates file-by-file
        _srccache[rel] = open(os.path.join(KSRC, rel), errors="ignore").read()
    src = _srccache[rel]
    try:
        text = cluster.functions(src)[fn]["text"]
    except Exception:
        raise Refused("no-source")
    ret, params_str, body = _sig_split(text)
    scan = purity.mask(body)

    if not any(re.search(rf"\b{op}\s*\(", scan) for op in MUT_OPS):
        raise Refused("no list mutation")
    if _ASM.search(scan) or _MMIO.search(scan):
        raise Refused("asm/mmio")
    ret_node = None
    rm = _RET_NODE.match(ret)
    if rm:
        ret_node = rm.group(1)      # node-pointer return: validated below
    elif ret not in INT_RETURNS:
        raise Refused(f"ret: {ret[:24]!r}")

    near = os.path.join(KSRC, rel)
    globals_lh = _file_globals(src)

    # ---- params: lh | node | scalar | token --------------------------------
    params, nodes, tokens = {}, {}, {}
    for piece in [p.strip() for p in params_str.split(",") if p.strip()]:
        if piece == "void":
            continue
        sm = _PARAM_STRUCT.match(piece)
        if sm:
            if sm.group(2) != "*":
                raise Refused(f"param: multi-star {piece!r}")
            st, name = sm.group(1), sm.group(3)
            if st == "list_head":
                params[name] = {"kind": "lh", "struct": None}
            else:
                try:
                    nodes[name] = (st,) + _node_fields(st, near)
                    params[name] = {"kind": "node", "struct": st}
                except Refused:
                    tokens[name] = st       # opaque token param
                    params[name] = {"kind": "token", "struct": st}
            continue
        cm = _SCALAR_PARAM.match(piece)
        if cm:
            params[cm.group(1)] = {"kind": "scalar", "struct": None}
            continue
        raise Refused(f"param: {piece!r}")

    # ---- locals: node cursors + LIST_HEAD anchors --------------------------
    cursors = {}
    for m in _NODE_DECL.finditer(scan):
        st = m.group(1)
        names = re.findall(r"\*\s*([A-Za-z_]\w*)", m.group(2))
        if st == "list_head":
            raise Refused("raw list_head local")
        try:
            info = (st,) + _node_fields(st, near)
        except Refused as e:
            raise Refused(f"local-{e}")
        for n in names:
            cursors[n] = info
    for m in _NODE_DECL_INIT.finditer(scan):        # `struct X *v = ...;`
        st, name = m.group(1), m.group(2)
        if st == "list_head":
            raise Refused("raw list_head local")
        if name in cursors:
            continue
        try:
            cursors[name] = (st,) + _node_fields(st, near)
        except Refused as e:
            raise Refused(f"local-{e}")
    # a struct param is a NODE only if the fn uses `&p->field` (linked/unlinked/
    # anchored); a merely-RESOLVABLE struct used via scalar reads + identity
    # compares is a TOKEN. (The lenient field resolver made big structs like
    # `module` resolvable, which must not flip their classification.)
    for name in list(nodes):
        if not re.search(rf"&\s*{name}\s*->", scan):
            tokens[name] = params[name]["struct"]
            params[name]["kind"] = "token"
            del nodes[name]
    local_lists = set(_LISTHEAD_LOCAL.findall(scan))
    nodes_all = dict(nodes) | cursors
    if ret_node is not None and ret_node not in {v[0] for v in nodes_all.values()}:
        raise Refused(f"ret: non-node ptr {ret_node}")

    # ---- call vocabulary ---------------------------------------------------
    flags = {"locks_stripped": False, "alloc_stripped": False}
    for m in purity.CALL.finditer(scan):
        name = m.group(1)
        if (name in OPS or name in ITERS or name in PEEKS
                or name in purity.NONCALL
                or name in purity.PURE_CALL or name == fn
                or name in ("LIST_HEAD", "kfree")):
            continue
        if name in LOCK_STRIP:
            flags["locks_stripped"] = True
            continue
        if _V2.match(name):
            raise Refused(f"vocab-v3: {name}")
        raise Refused(f"opaque: {name}")

    masked = scan

    def _anchor_ok(a):
        """list anchor: lh param | &node->lh_field | &LOCAL | &GLOBAL"""
        if a in params and params[a]["kind"] == "lh":
            return ("param", a)
        am = re.match(r"&\s*([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)$", a)
        if am and am.group(1) in nodes_all and am.group(2) in nodes_all[am.group(1)][1]:
            return ("field", a)
        gm = re.match(r"&\s*([A-Za-z_]\w*)$", a)
        if gm and gm.group(1) in local_lists:
            return ("local", gm.group(1))
        if gm and gm.group(1) in globals_lh:
            return ("global", gm.group(1))
        return None

    # ---- iteration headers -------------------------------------------------
    iters = []
    roles = {}
    for m in _LFE.finditer(scan):
        kind, args = m.group(1), _call_args(m.group(2))
        want = 4 if kind == "list_for_each_entry_safe" else 3
        if len(args) != want:
            raise Refused(f"iter arity: {kind}({len(args)})")
        pos, member = args[0], args[-1]
        cvars = [pos] + ([args[1]] if want == 4 else [])
        for v in cvars:
            if v not in cursors:
                raise Refused(f"iter cursor not a resolvable node local: {v}")
        anchor = _anchor_ok(args[-2])
        if not anchor:
            raise Refused(f"iter anchor: {args[-2][:24]!r}")
        if anchor[0] == "param":
            roles.setdefault(args[-2], set()).add("head")
        if member not in cursors[pos][1]:
            raise Refused(f"iter member {member} not an lh field of {cursors[pos][0]}")
        iters.append({"kind": kind, "cursor": pos, "anchor": anchor, "member": member})
        masked = masked.replace(m.group(0), " ITERHDR ")

    # ---- peek/pop element extraction (v3) ----------------------------------
    peeks = []
    struct_types = {v[0]: v for v in nodes_all.values()}
    for m in _PEEK.finditer(scan):
        pop, anchor_expr, st, member = m.group(1), m.group(2).strip(), m.group(3), m.group(4)
        anc = _anchor_ok(anchor_expr)
        if not anc:
            raise Refused(f"peek anchor: {anchor_expr[:24]!r}")
        if st not in struct_types or member not in struct_types[st][1]:
            raise Refused(f"peek type/member: {st}.{member}")
        if anc[0] == "param":
            roles.setdefault(anchor_expr, set()).add("head")
        peeks.append({"op": pop, "anchor": list(anc), "member": member})
        masked = masked.replace(m.group(0), " PEEKCALL ")

    # ---- list ops ----------------------------------------------------------
    op_sites = 0
    node_lh_roles = {}
    anchors_used = set()
    for m in _OP_CALL.finditer(masked):
        name = m.group(1)
        if name not in OPS:
            continue
        op_sites += name in MUT_OPS
        entry_ix, head_ix = OPS[name]
        args = _call_args(m.group(2))
        if len(args) != len(entry_ix) + len(head_ix):
            raise Refused(f"op arity: {name}({len(args)})")
        for i, a in enumerate(args):
            role = "entry" if i in entry_ix else "head"
            if a in params and params[a]["kind"] == "lh":
                roles.setdefault(a, set()).add(role)
                continue
            am = re.match(r"&\s*([A-Za-z_]\w*)\s*->\s*([A-Za-z_]\w*)$", a)
            if am and am.group(1) in nodes_all and am.group(2) in nodes_all[am.group(1)][1]:
                node_lh_roles.setdefault((am.group(1), am.group(2)), set()).add(role)
                continue
            if role == "head":
                anc = _anchor_ok(a)
                if anc:
                    anchors_used.add(anc)
                    continue
            raise Refused(f"op arg: {a[:24]!r}")
        masked = masked.replace(m.group(0), " OPCALL ")
    for pname, rs in roles.items():
        if len(rs) > 1:
            raise Refused(f"ambiguous role: {pname}")

    # ---- strippable brackets + arena kfree ---------------------------------
    for lk in LOCK_STRIP:
        masked = re.sub(rf"\b{lk}\s*\([^()]*\)", " OPCALL ", masked)
    masked = re.sub(r"\bLIST_HEAD\s*\(\s*\w+\s*\)", " OPCALL ", masked)
    for m in list(re.finditer(r"\bkfree\s*\(\s*([A-Za-z_]\w*)\s*\)", masked)):
        if m.group(1) not in nodes_all:
            raise Refused(f"kfree non-node: {m.group(1)}")
        flags["alloc_stripped"] = True
        masked = masked.replace(m.group(0), " OPCALL ")
    if re.search(r"\bkfree\s*\(", masked):
        raise Refused("kfree complex arg")

    # ---- node fields: scalars -> NFLD (rw), tokens -> TFLD (ro) ------------
    token_reads = {}
    for vname, (st, lhf, scalars, toks) in nodes_all.items():
        for f in scalars:
            masked = re.sub(rf"\b{vname}\s*->\s*{f}\b", " NFLD ", masked)
        for f in toks:
            masked = re.sub(rf"\b{vname}\s*->\s*{f}\b", " TFLD ", masked)
        # any other field name on a known node struct = stale/unknown field
        um = re.search(rf"\b{vname}\s*->\s*([A-Za-z_]\w*)", masked)
        if um:
            raise Refused(f"unknown field on {st}: {um.group(1)}")
    for tname in tokens:
        for fm in re.finditer(rf"\b{tname}\s*->\s*([A-Za-z_]\w*)\b", masked):
            token_reads.setdefault(tname, []).append(fm.group(1))
        masked = re.sub(rf"\b{tname}\s*->\s*[A-Za-z_]\w*\b", " TPF ", masked)

    # token discipline: reads/compares only — no writes, no arithmetic, no &
    if re.search(r"\bTFLD\s*(=[^=]|\+\+|--|[+\-|&^]=)", masked):
        raise Refused("token-field write")
    if re.search(r"\bTFLD\s*[-+*/%]|[-+*/%]\s*TFLD", masked):
        raise Refused("token arithmetic")
    if re.search(r"\bTPF\s*(=[^=]|\+\+|--|[+\-|&^]=)|&\s*TPF", masked):
        raise Refused("token-param write")

    # ---- residual ----------------------------------------------------------
    if "->" in masked:
        raise Refused(f"raw-deref: {masked[max(0,masked.find('->')-16):masked.find('->')+8]!r}")
    if _RAW_FLOW.search(masked):
        raise Refused(f"raw flow: {_RAW_FLOW.search(masked).group(0)[:8]}")
    if re.search(r"\*\s*[A-Za-z_]\w*\s*=[^=]", masked):
        raise Refused("deref write")
    if _IMPURE_RESIDUAL.search(masked):
        raise Refused(f"impure: {_IMPURE_RESIDUAL.search(masked).group(0)[:16]}")
    owned = (purity.owned_names(text) | purity.KEYWORDS | set(cursors)
             | {"OPCALL", "NFLD", "TFLD", "TPF", "ITERHDR", "PEEKCALL"}
             | local_lists)
    for am in purity._ASSIGN.finditer(masked):
        if am.group(1) not in owned:
            raise Refused(f"unowned write: {am.group(1)}")
    # residual identifiers must be resolvable object-like #defines (emitted by
    # the harness); enum constants are refused honestly (no host value).
    defines = {}
    call_names = {m.group(1) for m in purity.CALL.finditer(masked)}
    type_names = set(re.findall(r"\bstruct\s+(\w+)", masked))
    for im in re.finditer(r"(?<![\w.>])([A-Za-z_]\w*)\b", masked):
        n = im.group(1)
        if (n in owned or n in call_names or n in defines or n in params
                or n in globals_lh or n in purity.PURE_CALL or n in type_names):
            continue
        v = mirror._resolve_define(n, src)
        if v is None:
            raise Refused(f"unresolved: {n}")
        defines[n] = v

    shape = ("iter_node" if iters else "node" if nodes_all else "raw")
    return {
        "file": rel, "fn": fn, "shape": shape, "ret": ret or "void",
        "ret_node": ret_node, "peeks": peeks, "defines": defines,
        "flags": flags, "op_sites": op_sites, "n_iters": len(iters),
        "branches": len(re.findall(r"\bif\s*\(", masked)),
        "globals": sorted({a[1] for a in anchors_used if a[0] == "global"}
                          | {i["anchor"][1] for i in iters if i["anchor"][0] == "global"}
                          | {p["anchor"][1] for p in peeks if p["anchor"][0] == "global"}),
        "local_lists": sorted(local_lists),
        "token_reads": {k: sorted(set(v)) for k, v in token_reads.items()},
        "params": [
            {"name": p, "kind": params[p]["kind"], "struct": params[p]["struct"],
             "role": (sorted(roles[p])[0] if p in roles and roles[p] else None),
             **({"lh_fields": nodes[p][1], "scalar_fields": nodes[p][2],
                 "token_fields": nodes[p][3]} if p in nodes else {})}
            for p in params],
        "cursors": {c: {"struct": st, "lh_fields": lhf, "scalar_fields": sf,
                        "token_fields": tf}
                    for c, (st, lhf, sf, tf) in cursors.items()},
        "iters": [{"kind": i["kind"], "cursor": i["cursor"],
                   "anchor": list(i["anchor"]), "member": i["member"]}
                  for i in iters],
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
            src = open(p, errors="ignore").read()
            if "list_" not in src:      # cheap prefilter
                continue
            for fn in cluster.functions(src):
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
            accepted.append(gate(rel, fn))
            tally["ACCEPTED"] += 1
        except Refused as e:
            key = re.sub(r"['\"].*", "", str(e)).strip()[:30]
            tally[f"refuse:{key}"] += 1
        except Exception as e:
            tally[f"ERROR:{type(e).__name__}"] += 1
    print(f"=== container-ADT v2 front gate over {len(pairs)} fns ===")
    for k, c in tally.most_common(40):
        print(f"  {c:5d}  {k}")
    print(f"\nACCEPTED (host-liftable LIST mutators): {len(accepted)}")
    for a in accepted[:25]:
        print(f"  {a['fn']}  ({a['file']})  shape={a['shape']} "
              f"locks={a['flags']['locks_stripped']} alloc={a['flags']['alloc_stripped']} "
              f"sites={a['op_sites']} iters={a['n_iters']} branches={a['branches']}")
    out = os.path.join(HERE, "reach_accepted.json")
    json.dump(accepted, open(out, "w"), indent=1)
    print(f"\n-> {os.path.relpath(out)} ({len(accepted)} functions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
