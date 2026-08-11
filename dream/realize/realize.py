#!/usr/bin/env python3
"""realize.py — model->real translation for verified efftrace candidates.

A sweep-verified efftrace candidate holds its logic in a CLOSED helper
vocabulary over a flat cell model:

    field(F0_BD_WRITERS, a0) / set_field(F0_BD_WRITERS, a0, v)   # node fields
    g(G_NAME) / set_g(G_NAME, v)                                 # globals
    out(OUT_NAME) / set_out(OUT_NAME, v)                         # out-params

and the cell-index constants were DERIVED from the real struct fields by the
efftrace reach gate. So realization is a deterministic transpile: rewrite each
helper call into a real-struct access (`field(F0_BD_WRITERS, a0)` ->
`((*bdev).bd_writers as i64)`), keep every other token of the verified body
VERBATIM, and emit a real-signature `#[no_mangle] extern "C" fn <fn>_rs(...)`.

THE TRANSPILER IS NOT TRUSTED. Each realized function is re-gated by the SAME
differential the model passed: harness.prepare()'s C reference arena + workload
+ probe are reused unchanged; only the Rust side swaps the cell model for an
arena of real-layout #[repr(C)] structs and routes rs_call through the realized
function. A transpile bug is a state divergence — the 0-false-pass invariant is
inherited, not re-argued.

Anything outside the closed vocabulary is REFUSED and tallied (fail-closed):
direct S[]/norm/CW access, early `return`, a node arg used anywhere but its own
slot position, `a{i}` arithmetic on node/out handles. Refusals are the v2
worklist, not silent drops.

Usage:
  realize.py prove <file> <fn>    # transpile + host differential re-verify
  realize.py show <file> <fn>     # print the realized function source
  realize.py selfcheck            # mechanism proof: correct->MATCH, sabotage->DIVERGE
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
EFF = os.path.join(HERE, "..", "efftrace")
VERIFIED = os.path.join(HERE, "..", "firstrun", "verified")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


reach = _load("eff_reach_rz", os.path.join(EFF, "reach.py"))
harness = _load("eff_harness_rz", os.path.join(EFF, "harness.py"))


class Refused(Exception):
    pass


# ---------------------------------------------------------------------------
# candidate loading
# ---------------------------------------------------------------------------

def load_body(file, fn):
    """The verified rs_call body, verbatim, from the sweep bank."""
    key = f"efftrace_{file.replace('/', '__')}_{fn}.rs"
    path = os.path.join(VERIFIED, key)
    src = open(path).read()
    m = re.search(r'extern "C" fn rs_call\([^)]*\) -> i64 \{\n(.*)\n\}\s*$',
                  src, re.DOTALL)
    if not m:
        raise Refused("no_rs_call_body")
    return m.group(1)


def load_fconsts(file, fn):
    """value -> F-const name, from the model's OWN `const F0_X: usize = N;`
    decls. Used to resolve a literal field base (the model inlined the const's
    value). Cell indexes are unique in the flat vector; a duplicated value is
    dropped so resolution stays unambiguous (fail-closed at use)."""
    key = f"efftrace_{file.replace('/', '__')}_{fn}.rs"
    src = open(os.path.join(VERIFIED, key)).read()
    out = {}
    for m in re.finditer(r"const (F\d+_[A-Za-z0-9_]+): usize = (\d+);", src):
        v = int(m.group(2))
        out[v] = None if v in out else m.group(1)
    return {v: n for v, n in out.items() if n}


# ---------------------------------------------------------------------------
# type mapping (host LP64, same table the harness normalizes with)
# ---------------------------------------------------------------------------

_RS_KEYWORDS = {
    "as", "async", "await", "box", "break", "const", "continue", "crate", "dyn",
    "else", "enum", "extern", "false", "fn", "for", "if", "impl", "in", "let",
    "loop", "match", "mod", "move", "mut", "priv", "pub", "ref", "return",
    "static", "struct", "super", "trait", "true", "try", "type", "unsafe",
    "use", "where", "while"}


def rid(f):
    """Rust field identifier for a C field name (r#-escape keywords)."""
    return f"r#{f}" if f in _RS_KEYWORDS else f


def wty(w):
    """Rust type for a (bits, signed) param width; C bool arrives as u8 0/1."""
    if w is None:
        return "i64"
    bits, signed = w
    if bits == 1:
        return "u8"
    return f"{'i' if signed else 'u'}{bits}"


def rust_ty(ctype):
    bits, signed = harness._cell_width(ctype)
    if bits == 1:
        return "u8"          # C bool: 1 byte, stored 0/1
    return f"{'i' if signed else 'u'}{bits}"


def store_cast(ctype):
    """Rust cast that reproduces the C typed store `field = (T)v` from i64."""
    bits, signed = harness._cell_width(ctype)
    if bits == 1:
        return "(({v}) != 0) as u8"
    return f"({{v}}) as {'i' if signed else 'u'}{bits}"


# ---------------------------------------------------------------------------
# the transpile
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Wrapping arithmetic (A4 finding). The verified bodies use bare `+ - *`, which
# PANIC on overflow when overflow-checks are on. C wraps (the kernel builds
# -fno-strict-overflow; unsigned C wraps by definition), and a panic inside a
# freestanding kernel object lands in `loop {}` = a kernel hang. Kani proved
# the exposure real (seqbuf_seek: `pos + offset` over the full i64 domain).
# So emit EXPLICITLY wrapping arithmetic: same values as today's -O build (which
# has checks off), but pinned in the source regardless of build flags.
#
# Precedence-aware, paren-safe, and CONSERVATIVE: anything it cannot split
# confidently is returned unchanged (the candidate then stays PANIC_RISK —
# flagged, never silently "fixed"). The differential + Kani arbitrate the
# result, so a bad rewrite cannot ship.
# ---------------------------------------------------------------------------

_WRAP_OP = {"+": "wrapping_add", "-": "wrapping_sub", "*": "wrapping_mul"}
def _is_const_expr(s):
    """True if `s` is built ONLY from numeric literals, operators and parens —
    i.e. a constant expression of ambiguous `{integer}` type, which cannot take
    a method (`(166666 * 2).wrapping_add(1)` is E0689). Such expressions are
    const-folded and literal overflow is a COMPILE error, so there is no
    runtime hang to prevent by rewriting them."""
    t = re.sub(r"0[xX][0-9a-fA-F_]+|0[bB][01_]+|[0-9][0-9_]*", "", s)
    return re.sub(r"[\s()+\-*/%]", "", t) == ""
# operator chars that, immediately before a +/-/*, mean it is NOT a binary op
_NOT_OPERAND_END = set("+-*/%<>=!&|^~,;([{:")


def _split_top(e, ops):
    """Index of the LAST top-level binary operator from `ops` (left-assoc), or
    -1. Skips anything inside brackets and rejects unary/compound forms."""
    depth = 0
    for i in range(len(e) - 1, -1, -1):
        c = e[i]
        if c in ")]}":
            depth += 1
        elif c in "([{":
            depth -= 1
        elif depth == 0 and c in ops:
            if i == 0 or i + 1 >= len(e):
                continue
            # compound assignment / shift / comparison / arrow -> not our op
            if e[i + 1] in "=><+-*&|" or e[i - 1] in "=><&|":
                continue
            prev = e[:i].rstrip()
            if not prev or prev[-1] in _NOT_OPERAND_END:
                continue                      # unary sign, not binary
            return i
    return -1


def wrapify(e, depth=0):
    """Rewrite top-level binary + - * into wrapping method calls, recursively."""
    if depth > 24:
        return e
    s = e.strip()
    if not s:
        return e
    for ops in ("+-", "*"):                   # lowest precedence first
        i = _split_top(s, ops)
        if i < 0:
            continue
        op = _WRAP_OP.get(s[i])
        if op is None:
            continue
        lhs, rhs = s[:i], s[i + 1:]
        if not lhs.strip() or not rhs.strip():
            return e
        # A bare integer literal RECEIVER is an ambiguous numeric type in Rust —
        # `(166667).wrapping_mul(2)` is E0689. Leave such expressions alone:
        # literal-only arithmetic is const-folded (and literal overflow is a
        # COMPILE error, not a runtime panic), so there is no hang to prevent.
        # Conservative by design — if a real overflow risk remains, Kani reports
        # PANIC_RISK rather than us shipping code that does not build.
        if _is_const_expr(lhs):
            return e
        return f"({wrapify(lhs, depth + 1)}).{op}({wrapify(rhs, depth + 1)})"
    # no top-level op: peel ONE fully-enclosing paren pair and recurse
    if s.startswith("(") and s.endswith(")"):
        d, closes_at_end = 0, True
        for j, c in enumerate(s):
            if c == "(":
                d += 1
            elif c == ")":
                d -= 1
                if d == 0 and j != len(s) - 1:
                    closes_at_end = False
                    break
        if closes_at_end:
            inner = wrapify(s[1:-1], depth + 1)
            return f"({inner})"
    return e


def wrapify_stmts(body):
    """Apply wrapify to the RHS of `let` bindings and to whole expression
    statements — the two shapes the verified bodies use for arithmetic. Lines
    it does not recognise pass through untouched."""
    out = []
    for ln in body.splitlines():
        m = re.match(r"^(\s*let\s+(?:mut\s+)?[\w:<>, ]+=\s*)(.+?)(;\s*)$", ln)
        if m and any(o in m.group(2) for o in "+-*"):
            out.append(m.group(1) + wrapify(m.group(2)) + m.group(3))
            continue
        out.append(ln)
    return "\n".join(out)


_HELPER_RE = re.compile(r"(?<![\w])(set_field|field|set_g|g|set_out|out)\s*\(")
_FORBIDDEN = re.compile(r"(?<![\w])(S\s*\[|norm\s*\(|CW\s*\[|NSTATE|rs_set|rs_reset|rs_state)\b")


def _labelize(body):
    """v2: lower `return X;` to label-break-value so the value flows through
    the single cast site (`let __r: i64 = 'cgir: { .. }`). v1 refused returns
    outright (79 fns): a mid-body `return` escapes the wrapper uncast — a
    type error at best, a wrong-typed value at worst. Bare `return;` stays
    refused (an rs_call body always yields i64). Returns (body, n_returns);
    n_returns == 0 -> caller emits the v1 plain block, byte-identical."""
    if re.search(r"(?<![\w])return\s*;", body):
        raise Refused("bare_return")
    return re.subn(r"(?<![\w])return\b\s*([^;]*);", r"break 'cgir (\1);", body)


def _split_args(text, start):
    """text[start] == '(' -> (args list split at top-level commas, index past ')')."""
    depth, i, args, cur = 0, start, [], ""
    while i < len(text):
        ch = text[i]
        if ch in "([{":
            depth += 1
            if depth > 1:
                cur += ch
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                args.append(cur)
                return args, i + 1
            cur += ch
        elif ch == "," and depth == 1:
            args.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    raise Refused("unbalanced_parens")


_F_CONST = re.compile(r"^F\d+_[A-Za-z0-9_]+$")
_SLOT_TOK = re.compile(r"^a\d+$")
_INT_TOK = re.compile(r"^\d+$")
_G_CONST = re.compile(r"^G_[A-Za-z0-9_]+$")


def _peel(a):
    """Strip the measured no-op decorations off a helper arg: trailing
    `as usize|i64|u64|i32|u32` casts and `.try_into().unwrap()`. Conservative —
    the caller only ACTS on the peeled form when it fully matches a known token
    shape; anything else refuses downstream."""
    s = a.strip()
    while True:
        if s.endswith(".try_into().unwrap()"):
            s = s[: -len(".try_into().unwrap()")].strip()
            continue
        m = re.match(r"^(.*?)\s+as\s+(?:usize|i64|u64|i32|u32)$", s)
        if m:
            s = m.group(1).strip()
            continue
        return s


def _canon_helper(h, args, fconsts):
    """Canonicalize the field-helper DIALECTS the bank actually contains. The
    model arena's helper is `S[base + slot]` — commutative — so the
    synthesizer emitted both argument orders (and cast/`try_into` decorations,
    globals through the field helper, literal bases). Every dialect here was
    measured in the non_const_field_base census; anything else stays refused.
    Returns (h, args) with args[0]=F-const, args[1]=slot for field/set_field —
    or reroutes to (g/set_g) for the global-cell shape."""
    if h not in ("field", "set_field"):
        return h, args
    b, s = _peel(args[0]), _peel(args[1])
    fb, fs = bool(_F_CONST.match(b)), bool(_F_CONST.match(s))
    if fb and fs:
        raise Refused("non_const_field_base")      # ambiguous — never guess
    if fb:
        return h, [b, s] + args[2:]                # canonical (peeled)
    if fs and _SLOT_TOK.match(b):                  # swapped dialect
        return h, [s, b] + args[2:]
    if _G_CONST.match(b) and s == "0":             # global cell via field()
        return ("g" if h == "field" else "set_g"), [b] + args[2:]
    if _INT_TOK.match(b) and fconsts and int(b) in fconsts:
        if _SLOT_TOK.match(s):                     # model inlined the F-const
            return h, [fconsts[int(b)], s] + args[2:]
        raise Refused("literal_base_foreign_slot")
    raise Refused("non_const_field_base")


# ---------------------------------------------------------------------------
# slot-handle aliasing (the `slot_not_own_param` class). The synthesizer often
# binds a node handle to a readable local — `let rqd = a0;` — and then uses the
# LOCAL as the field-helper slot: `field(F0_X, rqd)`. That slot token is not the
# param's own canonical `a{k}`, so the raw check below rejected it. It is trivial
# aliasing, not a foreign access: an immutable `let NAME = aK;` means NAME === aK
# for its whole scope. We resolve such aliases before the own-slot check, then
# STRIP the (now consumed) binding — otherwise the unbound node handle aK would
# survive `handle_arg_used_as_value`, and worse, NAME frequently EQUALS the real
# pointer param name, so an un-stripped `let NAME = aK;` (an i64) would shadow
# the `*mut Mirror` param. Genuine foreign/arithmetic slots never resolve to the
# param's own aK, so every real refusal is preserved (fail-closed).
_SLOT_ALIAS_LET = re.compile(r"\blet\s+(mut\s+)?([A-Za-z_]\w*)\s*=\s*([^;]+);")


def _slot_alias_map(body):
    """NAME -> bare slot token `aK`, for every immutable, singly-bound
    `let NAME = aK;` (aK possibly `as`/`try_into`-decorated). `let mut` and
    shadowed (bound >1x) names are EXCLUDED — a reassignable or ambiguous alias
    stays unresolved, so a slot using it refuses downstream (fail-closed)."""
    binds, counts, mut = {}, {}, set()
    for m in _SLOT_ALIAS_LET.finditer(body):
        nm, rhs = m.group(2), _peel(m.group(3).strip())
        counts[nm] = counts.get(nm, 0) + 1
        if m.group(1):
            mut.add(nm)
        if _SLOT_TOK.match(rhs):
            binds[nm] = rhs
    return {n: s for n, s in binds.items() if counts[n] == 1 and n not in mut}


def _resolve_slot(tok, amap, depth=0):
    """Follow an alias chain to its ultimate bare slot token (bounded walk)."""
    tok = tok.strip()
    if _SLOT_TOK.match(tok):
        return tok
    if depth < 8 and tok in amap:
        return _resolve_slot(amap[tok], amap, depth + 1)
    return tok


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", text)


def _blank_helpers(text):
    """Replace every helper-call span (field/set_field/...) with a space, so a
    residual scan sees only NON-slot-position tokens."""
    out, i = "", 0
    while True:
        m = _HELPER_RE.search(text, i)
        if not m:
            return out + text[i:]
        out += text[i:m.start()]
        try:
            _args, nxt = _split_args(text, m.end() - 1)
        except Refused:
            return out + text[i:]
        out += " "
        i = nxt


def transpile(rec, body, fconsts=None):
    """Rewrite the verified body's helper calls into real-struct accesses.

    Returns {body, sig_params, ret, node_params, uses_globals, uses_outp}.
    """
    if _FORBIDDEN.search(body):
        raise Refused("forbidden_token:" + _FORBIDDEN.search(body).group(1))

    node_ps = [p for p in rec["params"] if p["kind"] == "node"]
    outs = [p for p in rec["params"] if p["kind"] == "outp"]
    # node param pi -> (its a{k} token, its real name); field-name map per pi
    node_slot = {}
    fmap = {}
    for pi, p in enumerate(node_ps):
        k = rec["params"].index(p)
        node_slot[pi] = f"a{k}"
        up = {}
        for f in p["scalar_fields"]:
            if f.upper() in up:
                raise Refused("field_case_collision")
            up[f.upper()] = f
        fmap[pi] = up
    gtypes = {n: g["ctype"] for n, g in rec["globals"].items()}
    otypes = {p["name"]: p["ctype"] for p in outs}
    used = {"globals": False, "outp": False}
    accessed = {}          # struct -> set(fields), recorded during rewrite

    # resolve + consume slot-handle aliases (`let rqd = a0;`) before rewriting.
    # Engage ONLY for aliases that a field/set_field call actually uses in slot
    # position — a body with no such use (every prior-passing candidate) is left
    # untouched, so its emission stays byte-identical.
    amap = _slot_alias_map(body)
    node_slot_vals = set(node_slot.values())
    node_aliases = {n: s for n, s in amap.items() if s in node_slot_vals}
    _clean = _strip_comments(body)
    helper_args = []
    for m in _HELPER_RE.finditer(_clean):
        try:
            a, _n = _split_args(_clean, m.end() - 1)
        except Refused:
            continue
        helper_args += [x.strip() for x in a]
    node_aliases = {n: s for n, s in node_aliases.items()
                    if any(re.search(rf"(?<![\w]){n}(?![\w])", a) for a in helper_args)}
    if node_aliases:
        # a node handle alias may appear ONLY in slot position — a bare value
        # use of it is `handle used as value`. Blank the helper spans (slot
        # positions live inside them) and its own binding; any residual is a
        # value use.
        probe = _blank_helpers(_strip_comments(body))
        for n in node_aliases:
            p2 = re.sub(rf"\blet\s+{n}\b[^;]*;", " ", probe, count=1)
            if re.search(rf"(?<![\w]){n}(?![\w])", p2):
                raise Refused("handle_alias_used_as_value")
        for n in node_aliases:            # consume the binding
            body = re.sub(rf"\blet\s+{n}\b[^;]*;\s*", "", body, count=1)

    def rw(text, safe=False):
        out_s = ""
        i = 0
        while True:
            m = _HELPER_RE.search(text, i)
            if not m:
                return out_s + text[i:]
            out_s += text[i:m.start()]
            args, nxt = _split_args(text, m.end() - 1)
            args = [rw(a, safe) for a in args]    # nested helper calls
            h, args = _canon_helper(m.group(1), args, fconsts)
            if h in ("field", "set_field"):
                cm = re.match(r"\s*F(\d+)_([A-Za-z0-9_]+?)\s*$", args[0])
                if not cm:
                    raise Refused("non_const_field_base")
                pi = int(cm.group(1))
                fname = fmap.get(pi, {}).get(cm.group(2))
                if fname is None:
                    raise Refused("unknown_field_const")
                res = _resolve_slot(args[1].strip(), amap)
                if res != node_slot[pi]:
                    if re.search(r"\ba\d+\b", res) and re.search(r"[-+*/%<>|&^]", res):
                        raise Refused("slot_handle_arithmetic")
                    raise Refused("slot_not_own_param")
                pname = node_ps[pi]["name"]
                t = node_ps[pi]["scalar_fields"][fname]
                accessed.setdefault(node_ps[pi]["struct"], set()).add(fname)
                # safe mode is FIELD-GRANULAR: the core takes one `&mut TY` per
                # accessed field (`f_<field>`), so exclusivity is asserted over
                # exactly that field's bytes, never the whole struct (A1: a
                # whole-struct &mut noalias-covers padding = other real kernel
                # fields; a field-scoped &mut does not). Deref the ref lvalue.
                lv = f"(*f_{fname})" if safe else f"(*{pname}).{rid(fname)}"
                if h == "field":
                    out_s += f"({lv} as i64)"
                else:
                    cast = store_cast(t).replace("{v}", wrapify(args[2].strip()))
                    out_s += f"{{ {lv} = {cast}; }}"
            elif h in ("g", "set_g"):
                cm = re.match(r"\s*G_([A-Za-z0-9_]+?)\s*$", args[0])
                gname = None
                if cm:
                    low = {n.upper(): n for n in gtypes}
                    gname = low.get(cm.group(1))
                if gname is None:
                    raise Refused("unknown_global_const")
                used["globals"] = True
                t = gtypes[gname]
                if h == "g":
                    out_s += f"(GV_{gname} as i64)"
                else:
                    cast = store_cast(t).replace("{v}", wrapify(args[1].strip()))
                    out_s += f"{{ GV_{gname} = {cast}; }}"
            else:                                  # out / set_out
                cm = re.match(r"\s*OUT_([A-Za-z0-9_]+?)\s*$", args[0])
                oname = None
                if cm:
                    low = {n.upper(): n for n in otypes}
                    oname = low.get(cm.group(1))
                if oname is None:
                    raise Refused("unknown_out_const")
                used["outp"] = True
                t = otypes[oname]
                if h == "out":
                    out_s += f"((*{oname}) as i64)"
                else:
                    cast = store_cast(t).replace("{v}", wrapify(args[1].strip()))
                    out_s += f"{{ *{oname} = {cast}; }}"
            i = 0
            text = text[nxt:]

    # A4 fix: pin wrapping semantics in the SOURCE (see wrapify). Applied to the
    # body BEFORE the helper rewrite so tier-(a) and tier-(b) get the identical
    # transform and stay provably equivalent.
    body = wrapify_stmts(body)
    realized = rw(body)

    # every remaining ALL-CAPS identifier must be a known define: an unresolved
    # path in a Rust MATCH PATTERN silently becomes a catch-all binding (hit
    # live on __cxl_access_coordinate_set — compiled clean, first arm matched
    # everything, differential caught it). Fail closed instead.
    known_consts = set(rec["defines"])
    for tok in set(re.findall(r"(?<![\w])([A-Z][A-Z0-9_]{2,})(?![\w])", realized)):
        if tok not in known_consts:
            raise Refused(f"unknown_const_token:{tok}")

    # a node/out arg must not survive into the realized body as a bare value
    # (its only legitimate model use was the slot/handle position, now consumed)
    for i, p in enumerate(rec["params"]):
        if p["kind"] in ("node", "outp") and re.search(rf"(?<![\w])a{i}(?![\w])", realized):
            raise Refused("handle_arg_used_as_value")

    # scalar params: the realized fn binds a{i}: i64 from the native param, so
    # the verified i64 value-logic is untouched.
    pw = harness._param_widths(harness._fn_text(rec), rec["params"])
    sig, binds = [], []
    for i, p in enumerate(rec["params"]):
        if p["kind"] == "node":
            sig.append(f"{p['name']}: *mut {p['struct'].capitalize()}Mirror")
        elif p["kind"] == "outp":
            sig.append(f"{p['name']}: *mut {rust_ty(p['ctype'])}")
        else:
            w = pw[i]
            nt = wty(w)
            sig.append(f"{p['name']}_arg: {nt}")
            binds.append(f"    let a{i}: i64 = {p['name']}_arg as i64;")

    ret_c = rec["ret"]
    if ret_c == "void":
        ret_sig, ret_expr = "", "let _ = __r;"
    else:
        rt = rust_ty(ret_c)
        ret_sig, ret_expr = f" -> {rt}", f"__r as {rt}"

    # the fn is self-contained: resolved defines become fn-local consts (an
    # unemitted define would compile as a match-pattern binding — see above)
    defc = [f"    const {n}: i64 = {v};" for n, v in rec["defines"].items()]
    # v2: early returns lower to label-break-value; n_ret == 0 keeps the v1
    # emission byte-identical (the 480 verified census results stay stable).
    # The safe core needs NO transform: `return X;` inside `core() -> i64`
    # already returns the i64 through the boundary's single cast site.
    realized_lb, n_ret = _labelize(realized)
    fn_src = (
        f'#[no_mangle]\npub unsafe extern "C" fn {rec["fn"]}_rs({", ".join(sig)}){ret_sig} {{\n'
        + "\n".join(defc) + ("\n" if defc else "")
        + "\n".join(binds) + ("\n" if binds else "")
        + (f"    let __r: i64 = 'cgir: {{\n{realized_lb}\n    }};\n    {ret_expr}\n}}\n"
           if n_ret else
           f"    let __r: i64 = {{\n{realized}\n    }};\n    {ret_expr}\n}}\n"))

    # ---- tier-(b) SAFE form: machine-checked safe core + FIELD-GRANULAR
    # boundary (A1). The core is a #![forbid(unsafe_code)] module whose params
    # are one `&mut TY` per ACCESSED field — rustc PROVES no raw-pointer access.
    # The whole unsafe surface is the boundary's per-field `&mut (*p).field`:
    # each borrows exactly that field's bytes (Tree Borrows scopes the tag to
    # the field; rustc emits `noalias dereferenceable(sizeof field)`), so NO
    # whole-struct exclusivity is asserted and padding-covered real kernel
    # fields are outside every borrow. Disjoint-field &mut in one call is the
    # standard safe split-borrow-through-raw-pointer. Same boundary text works
    # for the host arena Mirror and the woven padded Mirror (both name the
    # fields). Restricted to ONE node param: two &mut from two pointers could
    # alias in C → UB in Rust, so multi-node stays tier (a). NOTE: this is the
    # STRUCTURAL gate only; the per-field CONCURRENCY audit (field_audit) is
    # applied by the weave/lift-census and can further demote to tier (a).
    fn_src_safe, liftable = None, False
    if (not used["globals"] and not used["outp"] and len(node_ps) == 1
            and accessed):
        liftable = True
        realized_safe = rw(body, safe=True)
        struct = node_ps[0]["struct"]
        pname = node_ps[0]["name"]
        fld_order = sorted(accessed[struct])       # deterministic
        core_sig, call_args = [], []
        for fn in fld_order:
            ty = rust_ty(node_ps[0]["scalar_fields"][fn])
            core_sig.append(f"f_{fn}: &mut {ty}")
            call_args.append(f"&mut (*{pname}).{rid(fn)}")
        for i, p in enumerate(rec["params"]):
            if p["kind"] == "scalar":
                core_sig.append(f"a{i}: i64")
                call_args.append(f"{p['name']}_arg as i64")
        mod = f"{rec['fn'].lstrip('_')}_safe_core"
        fn_src_safe = (
            f"mod {mod} {{\n"
            f"    #![forbid(unsafe_code)]\n"
            f"    pub fn core({', '.join(core_sig)}) -> i64 {{\n"
            + "\n".join("    " + d for d in defc) + ("\n" if defc else "")
            + f"        {{\n{realized_safe}\n        }}\n    }}\n}}\n"
            f'#[no_mangle]\npub unsafe extern "C" fn {rec["fn"]}_rs({", ".join(sig)}){ret_sig} {{\n'
            f"    let __r: i64 = {mod}::core({', '.join(call_args)});\n"
            f"    {ret_expr}\n}}\n")

    return {"fn_src": fn_src, "fn_src_safe": fn_src_safe, "liftable": liftable,
            "lift_fields": sorted(accessed.get(node_ps[0]["struct"], ())) if (liftable) else [],
            "accessed": accessed, "uses_globals": used["globals"],
            "uses_outp": used["outp"], "node_params": node_ps, "pw": pw}


# ---------------------------------------------------------------------------
# host re-verification: same C reference + workload; Rust side = real structs
# ---------------------------------------------------------------------------

def _mirror_struct(p):
    """Reduced #[repr(C)] mirror matching the C reference arena's struct def
    EXACTLY (same sorted field order, same C layout rules)."""
    lines = [f"#[repr(C)]\n#[derive(Copy, Clone)]\npub struct {p['struct'].capitalize()}Mirror {{"]
    for f, t in sorted(p["scalar_fields"].items()):
        lines.append(f"    pub {rid(f)}: {rust_ty(t)},")
    lines.append("}")
    return "\n".join(lines)


def rust_host_tu(rec, prep, tr, safe=False):
    """The differential's Rust TU: real-layout arenas + the realized fn +
    rs_reset/rs_set/rs_state/rs_call speaking the SAME cell protocol as the C."""
    node_ps = tr["node_params"]
    outs = [p for p in rec["params"] if p["kind"] == "outp"]
    NN = harness.NN
    parts = ['#![allow(non_snake_case, dead_code, static_mut_refs, unused_unsafe, '
             'unused_imports, unused_variables, non_upper_case_globals, unused_braces)]',
             "// realized model->real candidate over real-layout structs"]
    emitted = set()
    for p in node_ps:
        if p["struct"] not in emitted:
            emitted.add(p["struct"])
            parts.append(_mirror_struct(p))
    for pi, p in enumerate(node_ps):
        mn = p["struct"].capitalize() + "Mirror"
        parts.append(f"static mut RP{pi}: [{mn}; {NN}] = [{mn} {{ "
                     + ", ".join(f"{rid(f)}: 0" for f in sorted(p["scalar_fields"]))
                     + f" }}; {NN}];")
    for n, g in rec["globals"].items():
        parts.append(f"static mut GV_{n}: {rust_ty(g['ctype'])} = 0;")
    for p in outs:
        parts.append(f"static mut OV_{p['name']}: {rust_ty(p['ctype'])} = 0;")

    # cell protocol, same index map as the C side (prep['cells'])
    set_arms, get_arms = [], []
    outnames = [p["name"] for p in outs]
    for i, cell in enumerate(prep["cells"]):
        if cell[0] == "g":
            t = rec["globals"][cell[1]]["ctype"]
            set_arms.append(f"        {i} => GV_{cell[1]} = {store_cast(t).replace('{v}', 'v')},")
            get_arms.append(f"        {i} => GV_{cell[1]} as i64,")
        elif cell[0] == "out":
            t = otype = [p for p in outs if p["name"] == cell[1]][0]["ctype"]
            set_arms.append(f"        {i} => OV_{cell[1]} = {store_cast(t).replace('{v}', 'v')},")
            get_arms.append(f"        {i} => OV_{cell[1]} as i64,")
        else:
            _, pi, f, slot = cell
            t = node_ps[pi]["scalar_fields"][f]
            set_arms.append(f"        {i} => RP{pi}[{slot}].{rid(f)} = {store_cast(t).replace('{v}', 'v')},")
            get_arms.append(f"        {i} => RP{pi}[{slot}].{rid(f)} as i64,")
    ginit = []
    for n, g in rec["globals"].items():
        if g["init"]:
            ginit.append(f"    GV_{n} = {store_cast(g['ctype']).replace('{v}', str(g['init']))};")
    parts.append(f"""
#[no_mangle] pub extern "C" fn rs_reset() {{ unsafe {{
{chr(10).join("    RP%d = [%sMirror { %s }; %d];" % (pi, p["struct"].capitalize(),
              ", ".join(f"{rid(f)}: 0" for f in sorted(p["scalar_fields"])), NN)
              for pi, p in enumerate(node_ps))}
{chr(10).join(f"    GV_{n} = 0;" for n in rec["globals"])}
{chr(10).join(f"    OV_{p['name']} = 0;" for p in outs)}
{chr(10).join(ginit)}
}}}}
#[no_mangle] pub extern "C" fn rs_set(ix: i32, v: i64) {{ unsafe {{
    match ix as usize {{
{chr(10).join(set_arms)}
        _ => {{}}
    }}
}}}}
#[no_mangle] pub extern "C" fn rs_state(buf: *mut i64) {{ unsafe {{
    for i in 0..{prep["nstate"]}usize {{
        *buf.add(i) = match i {{
{chr(10).join(get_arms)}
            _ => 0,
        }};
    }}
}}}}""")
    if safe:
        if not tr["liftable"]:
            raise Refused("not_liftable")
        parts.append(tr["fn_src_safe"])
    else:
        parts.append(tr["fn_src"])

    # rs_call trampoline: slot indices / handles -> real pointers, native args
    call_args, tramp = [], []
    for i, p in enumerate(rec["params"]):
        tramp.append(f"a{i}: i64")
        if p["kind"] == "node":
            pi = node_ps.index(p)
            call_args.append(f"RP{pi}.as_mut_ptr().add(a{i} as usize)")
        elif p["kind"] == "outp":
            call_args.append(f"&mut OV_{p['name']} as *mut _")
        else:
            w = tr["pw"][i]
            nt = wty(w)
            call_args.append(f"a{i} as {nt}")
    callexpr = f'{rec["fn"]}_rs({", ".join(call_args)})'
    if rec["ret"] == "void":
        body = f"    unsafe {{ {callexpr} }}; 0"
    else:
        body = f"    (unsafe {{ {callexpr} }}) as i64"
    parts.append(f'#[no_mangle] pub extern "C" fn rs_call({", ".join(tramp) or ""}) -> i64 {{\n{body}\n}}')
    return "\n".join(parts) + "\n"


def close_realized(prep, rust_tu, workdir=None):
    """harness.close(), but with our full Rust TU instead of surface+body."""
    d = workdir or tempfile.mkdtemp(prefix="realize_")
    open(os.path.join(d, "ref.c"), "w").write(prep["csrc"])
    open(os.path.join(d, "cand.rs"), "w").write(rust_tu)
    open(os.path.join(d, "probe.c"), "w").write(harness._probe_c(prep))
    r = harness._run(["rustc", "--edition", "2021", "-O", "--crate-type=staticlib",
                      os.path.join(d, "cand.rs"), "-o", os.path.join(d, "libcand.a")], 90)
    if r is None or r.returncode:
        return {"verdict": "BUILD_FAIL_RS", "out": (r.stderr[-2000:] if r else ""), "dir": d}
    r = harness._run(["cc", "-O2", "-w", os.path.join(d, "probe.c"), os.path.join(d, "ref.c"),
                      os.path.join(d, "libcand.a"), "-o", os.path.join(d, "run")], 90)
    if r is None or r.returncode:
        return {"verdict": "BUILD_FAIL_C", "out": (r.stderr[-2000:] if r else ""), "dir": d}
    r = harness._run([os.path.join(d, "run")], 30)
    if r is None:
        return {"verdict": "TIMEOUT:run", "out": "", "dir": d}
    out = (r.stdout + r.stderr).strip()
    m = re.search(r"verdict=([A-Z_]+(?::[a-z]+)?)", out)
    return {"verdict": m.group(1) if m else f"UNKNOWN(rc={r.returncode})", "out": out, "dir": d}


def realize(file, fn):
    rec = reach.gate(file, fn)
    prep = harness.prepare(rec)
    prep = harness.with_directed(prep)
    body = load_body(file, fn)
    tr = transpile(rec, body, load_fconsts(file, fn))
    return rec, prep, tr


def realize_light(file, fn):
    """rec + transpile only — for weave-time artifact generation of candidates
    the census ALREADY re-verified (no differential re-run needed)."""
    rec = reach.gate(file, fn)
    tr = transpile(rec, load_body(file, fn), load_fconsts(file, fn))
    return rec, tr


# ---------------------------------------------------------------------------
# A1 per-field concurrency audit. A field-scoped `&mut (*p).field` is still UB
# if ANOTHER cpu accesses that field's bytes during the borrow — and kernel-
# "benign" races (READ_ONCE/WRITE_ONCE/data_race) are Rust UB (no benign-race
# category). So a field named in any of those markers ANYWHERE in the tree is
# conservatively treated as lockless-accessed → the fn holding it stays tier
# (a). Name-level over-approximation (a same-named field on another struct
# also demotes — the SAFE direction). Robust by construction: greps the three
# markers as FIXED strings then extracts `->field` in Python (the mega-regex
# alternation with `\w` inside a bracket class silently matched NOTHING — a
# vacuous zero; do not reintroduce it). Self-proves non-vacuous: `flags` MUST
# appear or the audit raises.
# ---------------------------------------------------------------------------

_RACY_CACHE = None
_LOCKLESS_MARKERS = ("READ_ONCE", "WRITE_ONCE", "data_race")


def _racy_field_names():
    """All struct field names appearing in a lockless-access marker, tree-wide.
    Cached per process. Raises if the scan comes back vacuous."""
    global _RACY_CACHE
    if _RACY_CACHE is not None:
        return _RACY_CACHE
    ksrc = harness.KSRC
    dirs = [os.path.join(ksrc, d) for d in
            ("kernel", "mm", "block", "fs", "net", "drivers", "lib", "sound",
             "crypto", "security", "include", "ipc", "arch/arm64")]
    dirs = [d for d in dirs if os.path.isdir(d)]
    import subprocess as _sp
    seen = set()
    for marker in _LOCKLESS_MARKERS:
        r = _sp.run(
            ["grep", "-rhoE", "--include=*.c", "--include=*.h",
             marker + r"\([^;]*->[A-Za-z_][A-Za-z0-9_]*", *dirs],
            capture_output=True, text=True)
        if r.returncode not in (0, 1):
            raise RuntimeError(f"racy-field grep failed: {r.stderr[:200]}")
        for ln in r.stdout.splitlines():
            for m in re.finditer(r"->([A-Za-z_][A-Za-z0-9_]*)", ln):
                seen.add(m.group(1))
    if "flags" not in seen:          # non-vacuous guard (flags is always racy)
        raise RuntimeError("racy-field audit came back VACUOUS (no 'flags') — "
                           "grep pattern likely broken; refusing to trust it")
    _RACY_CACHE = seen
    return seen


def field_audit(fields):
    """Subset of `fields` that appear in a lockless-access marker tree-wide
    (i.e. the fields that force a tier-(a) demotion)."""
    return set(fields) & _racy_field_names()


def lift_gate(tr, audit=True):
    """(tier_b_ok, demoted_fields). Structural liftability AND (optionally) the
    per-field concurrency audit. A fn is tier-(b) eligible iff it is
    structurally liftable and none of its accessed fields is lockless."""
    if not tr.get("liftable"):
        return False, set()
    if not audit:
        return True, set()
    racy = field_audit(tr.get("lift_fields", []))
    return (len(racy) == 0), racy


def prove(file, fn):
    rec, prep, tr = realize(file, fn)
    tu = rust_host_tu(rec, prep, tr)
    r = close_realized(prep, tu)
    print(f"REALIZE {file}:{fn} verdict={r['verdict']}  [{r['out'][:80]}]")
    if r["verdict"] != "MATCH":
        print(f"  dir={r['dir']}")
    return 0 if r["verdict"] == "MATCH" else 1


def show(file, fn):
    _, _, tr = realize(file, fn)
    print(tr["fn_src"])
    return 0


# ---------------------------------------------------------------------------
# selfcheck: the mechanism proof — correct realizes to MATCH; a sabotaged
# transpile (one store off by one) must DIVERGE (the differential is
# load-bearing over the realized output, not just the model).
# ---------------------------------------------------------------------------

def selfcheck():
    file, fn = "block/bdev.c", "bdev_block_writes"
    rec, prep, tr = realize(file, fn)
    print(f"=== realize selfcheck: {fn} ({file}) ===")
    r = close_realized(prep, rust_host_tu(rec, prep, tr))
    ok = r["verdict"] == "MATCH"
    print(f"  {'✓' if ok else '✗ UNEXPECTED'}  realized(correct)  -> {r['verdict']}")

    # sabotage: corrupt the realized store by +1 (a transpiler bug surrogate)
    sab = dict(tr)
    m = re.search(r"= (\(.*\)) as (\w+);", tr["fn_src"])
    assert m, "expected a store to sabotage"
    sab["fn_src"] = tr["fn_src"].replace(m.group(0),
        f"= ({m.group(1)} + 1) as {m.group(2)};", 1)
    r2 = close_realized(prep, rust_host_tu(rec, prep, sab))
    ok2 = r2["verdict"].startswith("DIVERGE")
    print(f"  {'✓' if ok2 else '✗ UNEXPECTED'}  realized(sabotaged store) -> {r2['verdict']}")
    print("REALIZE MECHANISM:", "PASS — verified body transpiled to a real-struct fn, "
          "re-certified by the same differential; sabotage caught" if ok and ok2 else "FAIL")
    return 0 if ok and ok2 else 1


# ---------------------------------------------------------------------------
# census: transpile + re-verify EVERY banked efftrace candidate; tally honestly.
# Checkpointed (jsonl) and resumable; refusals are a named worklist, never
# silent drops.
# ---------------------------------------------------------------------------

CENSUS = os.path.join(HERE, "census.jsonl")


def _census_one(pair):
    file, fn = pair
    import json
    key = f"{file}:{fn}"
    try:
        body = load_body(file, fn)
    except Exception as e:
        return {"key": key, "stage": "load", "result": f"REFUSED:{e}"}
    try:
        rec = reach.gate(file, fn)
    except Exception as e:
        return {"key": key, "stage": "gate", "result": f"GATE_FAIL:{str(e)[:80]}"}
    try:
        prep = harness.prepare(rec)
        prep = harness.with_directed(prep)
    except Exception as e:
        return {"key": key, "stage": "prepare", "result": f"PREP_FAIL:{str(e)[:80]}"}
    try:
        tr = transpile(rec, body, load_fconsts(file, fn))
    except Refused as e:
        return {"key": key, "stage": "transpile", "result": f"REFUSED:{e}"}
    except Exception as e:
        return {"key": key, "stage": "transpile", "result": f"ERROR:{str(e)[:80]}"}
    try:
        r = close_realized(prep, rust_host_tu(rec, prep, tr))
    except Exception as e:
        return {"key": key, "stage": "verify", "result": f"ERROR:{str(e)[:80]}"}
    return {"key": key, "stage": "verify", "result": r["verdict"],
            "uses_globals": tr["uses_globals"], "uses_outp": tr["uses_outp"],
            "n_node": len(tr["node_params"])}


def census():
    import json
    from concurrent.futures import ProcessPoolExecutor, as_completed
    solved = json.load(open(os.path.join(HERE, "..", "firstrun", "sweep", "solved.json")))
    pairs = []
    for r in solved["recs"]:
        if r["kind"] != "efftrace":
            continue
        p = os.path.join(VERIFIED, f"efftrace_{r['file'].replace('/', '__')}_{r['sym']}.rs")
        if os.path.exists(p):
            pairs.append((r["file"], r["sym"]))
    done = {}
    if os.path.exists(CENSUS):
        for ln in open(CENSUS):
            try:
                row = json.loads(ln)
                done[row["key"]] = row
            except Exception:
                pass
    todo = [p for p in pairs if f"{p[0]}:{p[1]}" not in done]
    print(f"census: {len(pairs)} banked efftrace candidates, {len(done)} done, {len(todo)} to run")
    out = open(CENSUS, "a")
    with ProcessPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_census_one, p): p for p in todo}
        for i, fu in enumerate(as_completed(futs)):
            row = fu.result()
            out.write(json.dumps(row) + "\n")
            out.flush()
            done[row["key"]] = row
            if (i + 1) % 25 == 0:
                print(f"  {i+1}/{len(todo)}...")
    out.close()
    from collections import Counter
    tally = Counter(r["result"].split(":")[0] if not r["result"].startswith("DIVERGE")
                    else r["result"] for r in done.values())
    verified_real = [k for k, r in done.items() if r["result"] == "MATCH"]
    print("\n=== realize census ===")
    for k, v in tally.most_common():
        print(f"  {k:24s} {v}")
    print(f"  REALIZED+RE-VERIFIED: {len(verified_real)}/{len(pairs)}")
    ref = Counter(r["result"] for r in done.values() if r["result"].startswith("REFUSED"))
    if ref:
        print("  refusal reasons:")
        for k, v in ref.most_common(10):
            print(f"    {v:4d}  {k}")
    div = [k for k, r in done.items() if r["result"].startswith("DIVERGE")]
    if div:
        print(f"  DIVERGES (transpile bugs or model/real semantic gaps — investigate): {div[:10]}")
    return 0


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "selfcheck":
        return selfcheck()
    if len(sys.argv) >= 2 and sys.argv[1] == "census":
        return census()
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    cmd, file, fn = sys.argv[1], sys.argv[2], sys.argv[3]
    if cmd == "prove":
        return prove(file, fn)
    if cmd == "show":
        return show(file, fn)
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
