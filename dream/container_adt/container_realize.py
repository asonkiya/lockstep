#!/usr/bin/env python3
"""Step 3 — realize container candidates against the `list_head` mirror.

Step 2 (`listmirror.py`) proved the mirror + faithful ops + a chain-walking
oracle, and MEASURED that oracle strictly stronger than the ADT one: the class
it uniquely catches is *unlink-without-poison*.

That measurement dictates this module's central design rule:

  **The ADT model cannot tell us which concrete op to emit.** `list_del` and
  `list_del_init` both render as `del(id)` in the model — they differ only in
  the removed node's state, exactly the axis the ADT view is blind to. So
  realization reads the CONCRETE op sequence out of the real C, and uses the
  verified ADT body only to check correspondence. Emitting `list_del` where the
  kernel wrote `list_del_init` would be a real (and ADT-invisible) defect.

Pipeline per candidate:
  1. parse the REAL C function -> ordered concrete list ops (+ entry/head exprs)
  2. parse the verified ADT model body -> ordered abstract ops
  3. require a 1:1, same-order, same-class correspondence -> else REFUSE
     (fail-closed, tallied; ambiguity is never guessed)
  4. emit Rust over the ListHead mirror with real pointers
  5. gate: chain-walking differential, real C vs realized Rust, over an arena

Scope: single list; straight-line and unconditional iteration; T3's bare
`kfree(node)` as a first-class op verified by the COMPOSED gate — the chain
differential plus an order-sensitive free-event log where every event records
(slot, chain-digest-at-free-time). The digest makes within-call ordering
visible: free-before-unlink (a UAF in situ) produces a different digest than
unlink-then-free, a class the ADT retire log (slots only) cannot see. Other
allocator entry points (kmalloc/kmem_cache_free/kfree_rcu/...) stay refused —
the t3_census measured ZERO of them in the banked population.

  container_realize.py map <file> <fn>    # show the C ops / ADT ops / mapping
  container_realize.py prove <file> <fn>  # realize + chain-walking differential
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
VERIFIED = os.path.join(REPO, "dream", "firstrun", "verified")

_spec = importlib.util.spec_from_file_location("listmirror_cr",
                                               os.path.join(HERE, "listmirror.py"))
LM = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(LM)

sys.path.insert(0, os.path.join(REPO, "dream", "cluster"))
import cluster  # noqa: E402

NN = 6          # arena nodes for the gate


class Refused(Exception):
    pass


# ---------------------------------------------------------------------------
# 1. concrete ops from the REAL C  (the authority on which op to emit)
# ---------------------------------------------------------------------------

# concrete C op -> (abstract ADT class, rust mirror op)
_C_OPS = {
    "list_add":       ("push_front", "list_add"),
    "list_add_tail":  ("push_back",  "list_add_tail"),
    "list_del":       ("del",        "list_del"),
    "list_del_init":  ("del",        "list_del_init"),
    "list_move":      ("move_front", "list_move"),
    "list_move_tail": ("move_tail",  "list_move_tail"),
    # INIT_LIST_HEAD detaches at the ADT level (self-loop = on no list); the
    # verified models render it as `del`. NOTE: on a node that is still linked
    # this does NOT fix the neighbours — but faithfulness is what we verify, and
    # the C ref performs the identical operation, so the differential still
    # arbitrates the translation rather than the kernel's own idiom.
    "INIT_LIST_HEAD": ("del",        "INIT_LIST_HEAD"),
    # T3: kfree is a concrete op like any other, kept IN ORDER with the list
    # ops. It emits a free EVENT (slot + chain-digest-at-free-time) rather than
    # touching memory — the composed gate compares the event stream, so a
    # dropped free, a wrong target, or a free-before-unlink (UAF) all DIVERGE.
    "kfree":          ("retire",     "free_ev"),
}
_UNSUPPORTED_C = ("_rcu", "list_splice", "list_cut", "list_bulk", "list_replace",
                  "list_swap", "list_rotate", "hlist_")


def c_ops(rel, fn):
    """Ordered concrete list ops in the real C body, with their arg text."""
    src = open(os.path.join(KSRC, rel), errors="ignore").read()
    try:
        text = cluster.functions(src)[fn]["text"]
    except KeyError:
        raise Refused(f"fn_not_found:{fn}")
    body = text[text.index("{"):]
    for bad in _UNSUPPORTED_C:
        if bad in body:
            raise Refused(f"unsupported_c_op:{bad}")
    # bare kfree(node) is the T3 op (129/131 of the census); every OTHER
    # allocator entry point stays refused — zero occurrences in the banked
    # population, so refuse-on-sight rather than model-by-guess.
    if re.search(r"\b(kmem_cache_free|kmem_cache_alloc|kzalloc|kmalloc|kcalloc"
                 r"|kvfree|kfree_rcu|kfree_sensitive|devm_kfree)\b", body):
        raise Refused("allocation_present:unsupported_variant")
    it = _classify_iteration(body, allow_cond=True)
    # CONDITIONALS (audit 2026-08-07 → list_empty class 2026-08-07): the only
    # supported predicate class is a bare `!?list_empty(expr)` — the one
    # expressible entirely in list vocabulary, so BOTH gate sides execute the
    # real predicate. Everything else stays fail-closed (the audit's lesson:
    # a dropped guard is invisible when the C ref is re-emitted from the same
    # unconditionally-extracted ops).
    guard = None
    if re.search(r"\bif\b|\?", body):
        guard = _parse_guard(body, it)
    # locals bound by `x = list_first_entry(head, T, member)` — the pop idiom
    # resolves ops on `&x->member` to FIRST-of-head
    firsts = {}
    for fm in re.finditer(r"([A-Za-z_]\w*)\s*=\s*list_first_entry\s*\(", body):
        fargs, _ = _split_call(body, fm.end() - 1)
        if len(fargs) == 3:
            firsts[fm.group(1)] = (_norm_expr(fargs[0]), fargs[2].strip())
    ops = []
    for m in re.finditer(r"\b(" + "|".join(_C_OPS) + r")\s*\(", body):
        name = m.group(1)
        args, _ = _split_call(body, m.end() - 1)
        o = {"c_op": name, "adt": _C_OPS[name][0], "rs": _C_OPS[name][1],
             "args": [a.strip() for a in args], "cond": None, "tgt": "entry",
             "pos": m.start()}
        am = re.fullmatch(r"&\s*([A-Za-z_]\w*)\s*->\s*(\w+)", (args[0] or "").strip())
        if am and am.group(1) in firsts:
            fhead, fmember = firsts[am.group(1)]
            if am.group(2) != fmember:
                raise Refused(f"first_member_mismatch:{am.group(2)}")
            if name not in ("list_del", "list_del_init"):
                raise Refused(f"first_target_op:{name}")
            o["tgt"] = "first"
            o["first_head"] = fhead
        ops.append(o)
    if not ops:
        raise Refused("no_list_ops_in_c")
    if guard and it is None:
        _apply_guard(ops, guard)
    if guard and it is not None:
        it["guard"] = guard["loop_guard"]
    # T3 guards, fail-closed. (a) A body with 2+ distinct iterated heads is a
    # multi-list function — collapsing them into one emitted walk would be
    # silently wrong. (b) The freed pointer must be the node being operated on:
    # the iteration cursor, or the base of a list-op's entry arg. `kfree(x)`
    # where x is anything else (a sub-buffer, the wrong cursor) is refused, not
    # guessed.
    heads = set()
    for lm_ in re.finditer(r"\blist_for_each_entry(_safe)?\s*\(", body):
        largs_, _ = _split_call(body, lm_.end() - 1)
        hidx = 2 if lm_.group(1) else 1
        if len(largs_) > hidx:
            heads.add(_norm_expr(largs_[hidx]))
    if len(heads) > 1:
        raise Refused("multi_head_iteration:multi_list")
    if any(o["c_op"] == "kfree" for o in ops):
        ok_bases = {_base_of(o["args"][0]) for o in ops if o["c_op"] != "kfree"}
        lm_ = re.search(r"\blist_for_each_entry(?:_safe)?\s*\(", body)
        if lm_:
            largs_, _ = _split_call(body, lm_.end() - 1)
            ok_bases = {_norm_expr(largs_[0])}          # only the cursor
        for o in ops:
            if o["c_op"] != "kfree":
                continue
            a = o["args"][0].strip()
            if not re.fullmatch(r"[A-Za-z_]\w*", a):
                raise Refused(f"free_arg_complex:{a[:30]}")
            if a not in ok_bases:
                raise Refused(f"free_target_mismatch:{a}")
    # CROSS-LIST guard: inside an iteration, a list_move* whose DESTINATION head
    # differs from the iterated head is a two-list function (T4 in the census),
    # not the single-list shape v1 models. Caught live on dev_exceptions_move
    # (iterates `orig`, moves to `dest`): collapsing the two heads turns it into
    # a self-move and the walk never terminates. Refuse rather than mis-model.
    if it is not None:
        lm = re.search(r"\blist_for_each_entry(?:_safe)?\s*\(", body)
        if lm:
            largs, _ = _split_call(body, lm.end() - 1)
            head_expr = _norm_expr(largs[3 if "_safe" in lm.group(0) else 2]
                                   if len(largs) > 2 else "")
            for o in ops:
                if o["c_op"].startswith("list_move") and len(o["args"]) > 1:
                    if _norm_expr(o["args"][1]) != head_expr:
                        raise Refused("cross_list_move:v1_single_list_only")
    return ops, text, it


def _parse_guard(body, it):
    """The list_empty guard class, fail-closed. Exactly ONE `if`, no else, no
    ternary, predicate `!?list_empty(expr)`. Returns:
      straight-line: {pol, pred, extent, inverts_rest, loop_guard: None}
      iteration:     {loop_guard: 'not_empty'} — the early-return-before-walk
                     shape only; a guard INSIDE the loop body (e.g. on a
                     second list_head member of the node) is out of the
                     single-list arena's model and refused."""
    if "?" in re.sub(r'"[^"]*"', "", body):
        raise Refused("conditional_body:ternary")
    ifs = list(re.finditer(r"\bif\s*\(", body))
    if len(ifs) != 1:
        raise Refused("conditional_body:multi_guard")
    m = ifs[0]
    if it is not None:
        lm0 = re.search(r"\blist_for_each_entry(?:_safe)?\s*\(", body)
        if lm0 and m.start() > lm0.start():
            # a guard INSIDE the loop body — regardless of predicate form,
            # per-element conditions are out of this class (fuse's second
            # list_head per node, abx500's token equality)
            raise Refused("conditional_loop_body:guard_in_loop")
    pargs, pend = _split_call(body, m.end() - 1)
    pred = ",".join(pargs).strip()
    pm = re.fullmatch(r"(!?)\s*list_empty\s*\(\s*([^()]+?)\s*\)", pred)
    if not pm:
        raise Refused(f"conditional_body:non_list_empty_pred:{pred[:40]}")
    pol = "not_empty" if pm.group(1) else "empty"   # polarity of the TRUE branch
    pred_expr = _norm_expr(pm.group(2))
    i = pend
    while i < len(body) and body[i] in " \t\n":
        i += 1
    if body[i] == "{":
        d, j = 0, i
        while j < len(body):
            d += body[j] == "{"
            d -= body[j] == "}"
            j += 1
            if d == 0:
                break
        extent = (i, j)
    else:
        extent = (i, body.index(";", i) + 1)
    if re.search(r"\belse\b", body):
        # an else branch is allowed ONLY when it contains no list ops (the
        # `else { rwi = NULL; }` pop shape) — anything else stays refused
        j = extent[1]
        while j < len(body) and body[j] in " \t\n":
            j += 1
        if not body.startswith("else", j):
            raise Refused("conditional_body:else_branch")
        k = j + 4
        while k < len(body) and body[k] in " \t\n":
            k += 1
        if body[k] == "{":
            d, e2 = 0, k
            while e2 < len(body):
                d += body[e2] == "{"
                d -= body[e2] == "}"
                e2 += 1
                if d == 0:
                    break
        else:
            e2 = body.index(";", k) + 1
        if re.search(r"\b(" + "|".join(_C_OPS) + r")\s*\(", body[k:e2]):
            raise Refused("conditional_body:else_branch_ops")
    inverts_rest = bool(re.search(r"\breturn\b", body[extent[0]:extent[1]]))
    if it is not None:
        lm = re.search(r"\blist_for_each_entry(?:_safe)?\s*\(", body)
        largs, _ = _split_call(body, lm.end() - 1)
        head = largs[2 if "_safe" in lm.group(0) else 1]
        if not (inverts_rest and pol == "empty"
                and pred_expr == _norm_expr(head)):
            raise Refused("conditional_loop_body:guard_shape")
        if re.search(r"\b(" + "|".join(_C_OPS) + r")\s*\(",
                     body[extent[0]:extent[1]]):
            raise Refused("conditional_loop_body:ops_in_guard")
        return {"loop_guard": "not_empty"}
    return {"pol": pol, "pred": pred_expr, "extent": extent,
            "inverts_rest": inverts_rest, "if_start": m.start(),
            "loop_guard": None}


def _apply_guard(ops, g):
    """Annotate straight-line ops with (polarity, target) from the single
    parsed guard; every op must land in a branch whose polarity is known."""
    inv = {"empty": "not_empty", "not_empty": "empty"}
    for o in ops:
        if o["c_op"] == "kfree" and (g["extent"][0] <= o["pos"] < g["extent"][1]
                                     or o["pos"] > g["extent"][1]):
            raise Refused("guard_kfree_unsupported")
        if g["extent"][0] <= o["pos"] < g["extent"][1]:
            pol = g["pol"]
        elif o["pos"] >= g["extent"][1] and g["inverts_rest"]:
            pol = inv[g["pol"]]
        elif o["pos"] >= g["extent"][1] or o["pos"] < g["if_start"]:
            continue        # outside the if, no early return: unguarded
        else:
            raise Refused("guard_scope_unknown")
        if _norm_expr(o["args"][0]) == g["pred"]:
            tgt = "entry"
        elif o["tgt"] == "first" and o.get("first_head") == g["pred"]:
            tgt = "head"
        elif len(o["args"]) > 1 and _norm_expr(o["args"][1]) == g["pred"]:
            tgt = "head"
        else:
            raise Refused(f"guard_target_unknown:{g['pred'][:30]}")
        o["cond"] = (pol, tgt)


def _norm_expr(e):
    return re.sub(r"[\s&]", "", e or "")


def _base_of(e):
    """`&cl->node` -> `cl`; the identifier a list-op entry arg is rooted at."""
    m = re.match(r"\s*&?\s*([A-Za-z_]\w*)", e or "")
    return m.group(1) if m else ""


def _classify_iteration(body, allow_cond=False):
    """None | {'safe': bool}. The safe/plain distinction is LOAD-BEARING and,
    like list_del vs list_del_init, is INVISIBLE to the ADT model (whose iter()
    returns a snapshot, i.e. always _safe-like semantics).

    `list_for_each_entry_safe` caches the next pointer BEFORE running the body,
    so a body that unlinks `pos` is sound. Plain `list_for_each_entry` reads
    `pos->next` AFTER the body — if the body unlinked pos, that reads
    LIST_POISON1 (a wild pointer, i.e. a kernel crash). So:
      * _safe  -> emit the cached-next walk
      * plain  -> emit the read-after-body walk, and REFUSE if the body mutates
                  the list (that combination would be a use-after-poison, and we
                  will not emit it even if the C somehow contains it)."""
    if "list_for_each_entry_safe" in body:
        it = {"safe": True}
    elif "list_for_each_entry" in body:
        it = {"safe": False}
    elif re.search(r"\b(while|for)\b", body) or "list_for_each" in body:
        raise Refused("unsupported_iteration_form")
    else:
        return None
    if not allow_cond and re.search(r"\bif\b|\?", body):
        raise Refused("conditional_loop_body:v1_unconditional_only")
    mutates = re.search(r"\b(list_del|list_del_init|list_move|list_move_tail)\b", body)
    if mutates and not it["safe"]:
        raise Refused("plain_iteration_with_mutation:use_after_poison")
    return it


def _split_call(s, open_idx):
    depth, i, args, cur = 0, open_idx, [], ""
    while i < len(s):
        c = s[i]
        if c in "([":
            depth += 1
            if depth > 1:
                cur += c
        elif c in ")]":
            depth -= 1
            if depth == 0:
                args.append(cur)
                return args, i + 1
            cur += c
        elif c == "," and depth == 1:
            args.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    raise Refused("unbalanced_call")


# ---------------------------------------------------------------------------
# 2. abstract ops from the VERIFIED ADT model  (used only for correspondence)
# ---------------------------------------------------------------------------

_ADT_OPS = ("push_back", "push_front", "del", "move_tail", "move_front",
            "iter", "empty", "first", "last", "retire", "set_field", "field")


def adt_ops(rel, fn):
    key = f"container_{rel.replace('/', '__')}_{fn}.rs"
    path = os.path.join(VERIFIED, key)
    if not os.path.exists(path):
        raise Refused("no_verified_candidate")
    src = open(path).read()
    m = re.search(r'extern "C" fn rs_call\([^)]*\) -> i64 \{\n(.*)\n\}\s*$', src, re.DOTALL)
    if not m:
        raise Refused("no_rs_call_body")
    body = m.group(1)
    seq = [mm.group(1) for mm in
           re.finditer(r"(?<![\w])(" + "|".join(_ADT_OPS) + r")\s*\(", body)]
    return [o for o in seq if o not in ("field", "set_field")], body


# ---------------------------------------------------------------------------
# 3. correspondence — fail-closed
# ---------------------------------------------------------------------------

_CANON = {"move_tail": ["del", "push_back"], "move_front": ["del", "push_front"]}


def _canon(seq):
    """Canonical ADT sequence: a move IS a del followed by a push, and the two
    sides are free to express it either way (the C may write list_del +
    list_add_tail where the model wrote move_tail, and vice versa)."""
    out = []
    for o in seq:
        out.extend(_CANON.get(o, [o]))
    return out


def correspond(cops, aops):
    """The C's mutating ops must line up with the model's, after canonicalising
    moves into del+push on both sides."""
    mutators = [o for o in aops if o in ("push_back", "push_front", "del",
                                         "move_tail", "move_front", "retire")]
    a_can = _canon(mutators)
    c_can = _canon([o["adt"] for o in cops])
    if len(a_can) != len(c_can):
        raise Refused(f"op_count_mismatch:c={len(c_can)},adt={len(a_can)}")
    for i, (a, c) in enumerate(zip(a_can, c_can)):
        if a != c:
            raise Refused(f"op_class_mismatch@{i}:adt={a},c={c}")
    return list(zip(mutators, cops))


# ---------------------------------------------------------------------------
# 4. emission — Rust over the ListHead mirror, real pointers
# ---------------------------------------------------------------------------

_INV_POL = {"empty": "not_empty", "not_empty": "empty"}


def _eff_cond(o, sabotage):
    """The Rust side's view of an op's guard. Sabotages NEVER mutate the op
    dicts — run_gate hands the same cops to the C ref, and a mutated guard
    there would drop the reference's guard too (a vacuous negative control)."""
    c = o.get("cond")
    if sabotage == "drop_guard":
        return None
    if sabotage == "flip_guard" and c:
        return (_INV_POL[c[0]], c[1])
    return c


def emit_realized(rel, fn, L, sabotage=None):
    cops, ctext, it = c_ops(rel, fn)
    aops, abody = adt_ops(rel, fn)
    if it is None:
        correspond(cops, aops)
    if (any(o.get("cond") for o in cops) or (it and it.get("guard"))) \
            and "empty" not in abody:
        # the verified model never consulted emptiness while the C guards on
        # it — a correspondence failure, same family as op_count_mismatch
        raise Refused("no_empty_in_model")
    if len({o["c_op"] for o in cops}) != len(cops) and len(cops) > 1:
        pass                        # repeated same op is fine
    lines = []
    for o in cops:
        rs = o["rs"]
        if sabotage == "wrong_op":          # list_add <-> list_add_tail
            rs = {"list_add": "list_add_tail", "list_add_tail": "list_add"}.get(rs, rs)
        if sabotage == "del_not_init":      # the ADT-INVISIBLE defect class
            rs = {"list_del_init": "list_del"}.get(rs, rs)
        if rs == "free_ev":
            if sabotage == "no_free":       # right membership, dropped free
                continue
            lines.append("        free_ev(head);" if sabotage == "wrong_free"
                         else "        free_ev(entry);")
            continue
        if rs in ("list_add", "list_add_tail"):
            core = [f"{rs}(entry, head);"]
        elif rs in ("list_del", "list_del_init", "INIT_LIST_HEAD"):
            core = (["let e = (*head).next;", f"{rs}(e);"]
                    if o.get("tgt") == "first" else [f"{rs}(entry);"])
        else:                                # list_move / list_move_tail
            core = ["__list_del((*entry).prev, (*entry).next);",
                    f"{'list_add' if rs == 'list_move' else 'list_add_tail'}(entry, head);"]
        cond = _eff_cond(o, sabotage)
        if cond:
            pol, ct = cond
            t = "entry" if ct == "entry" else "head"
            cmp_ = "==" if pol == "empty" else "!="
            lines.append(f"        if (*{t}).next {cmp_} {t} {{")
            lines += [f"            {c}" for c in core]
            lines.append("        }")
        else:
            lines += [f"        {c}" for c in core]
    if sabotage == "free_before_del":
        # the UAF ordering: the free fires BEFORE the op that precedes it.
        # Chain state at call boundaries and freed slots are identical — only
        # the chain-digest-at-free-time distinguishes the two orders.
        i = next((k for k, l in enumerate(lines) if "free_ev" in l), 0)
        if i > 0:
            lines[i - 1], lines[i] = lines[i], lines[i - 1]
    body = "\n".join(lines)
    if it is not None:
        # per-iteration ops act on `pos`; the walk shape is dictated by the
        # C's safe/plain choice (see _classify_iteration).
        inner = "\n".join(l.replace("entry", "pos") for l in lines)
        if sabotage == "unsafe_iter":       # emit the plain walk for a _safe loop
            walk = f"""        let mut pos = (*head).next;
        while pos != head {{
{inner}
            pos = (*pos).next;   // READ AFTER BODY — poisoned if the body unlinked
        }}"""
        elif it["safe"]:
            walk = f"""        let mut pos = (*head).next;
        while pos != head {{
            let n = (*pos).next;   // _safe: cache BEFORE the body
{inner}
            pos = n;
        }}"""
        else:
            walk = f"""        let mut pos = (*head).next;
        while pos != head {{
{inner}
            pos = (*pos).next;
        }}"""
        loop_guard = None if sabotage == "drop_guard" else it.get("guard")
        if loop_guard:                       # the early-return-before-walk form
            walk = ("        if (*head).next != head {\n"
                    + walk + "\n        }")
        return f"""
#[no_mangle] pub extern "C" fn realized_iter(head: *mut ListHead) {{
    unsafe {{
{walk}
    }}
}}
""", cops, aops, it
    return f"""
#[no_mangle] pub extern "C" fn realized_op(entry: *mut ListHead, head: *mut ListHead) {{
    unsafe {{
{body}
    }}
}}
""", cops, aops, it


# ---------------------------------------------------------------------------
# 5. the gate — real C vs realized Rust, chain-walking
# ---------------------------------------------------------------------------

def _ref_c(cops, L, it=None):
    """Host C reference: the real ops, applied in the real order."""
    calls = []
    for o in cops:
        if o["c_op"] == "kfree":
            core = ["cgir_free_ev(entry);"]
        elif o["c_op"] in ("list_add", "list_add_tail"):
            core = [f"{o['c_op']}(entry, head);"]
        elif o["c_op"] in ("list_del", "list_del_init", "INIT_LIST_HEAD"):
            core = (["struct list_head *e = head->next;", f"{o['c_op']}(e);"]
                    if o.get("tgt") == "first" else [f"{o['c_op']}(entry);"])
        else:
            core = [f"{o['c_op']}(entry, head);"]
        if o.get("cond"):
            pol, ct = o["cond"]
            t = "entry" if ct == "entry" else "head"
            neg = "" if pol == "empty" else "!"
            calls.append(f"    if ({neg}list_empty({t})) {{ "
                         + " ".join(core) + " }")
        else:
            calls += [f"    {c}" for c in core]
    iter_calls = []
    for o in cops:
        if o["c_op"] == "kfree":
            iter_calls.append("        cgir_free_ev(&pos->lh);")
        elif o["c_op"] in ("list_del", "list_del_init", "INIT_LIST_HEAD"):
            iter_calls.append(f"        {o['c_op']}(&pos->lh);")
        elif o["c_op"] in ("list_add", "list_add_tail"):
            iter_calls.append(f"        {o['c_op']}(&pos->lh, head);")
        else:
            iter_calls.append(f"        {o['c_op']}(&pos->lh, &C_HEAD);")
    ITER_BODY = chr(10).join(iter_calls) if it else "        (void)pos;"
    ITER_GUARD = ("    if (list_empty(&C_HEAD)) return;\n"
                  if (it and it.get("guard")) else "")
    return f"""
#include <stddef.h>
#define WRITE_ONCE(x, v) (*(volatile typeof(x) *)&(x) = (v))
#define LIST_POISON1 ((void *) {L['poison1']:#x}UL)
#define LIST_POISON2 ((void *) {L['poison2']:#x}UL)
struct list_head {{ struct list_head *next, *prev; }};
static inline void INIT_LIST_HEAD(struct list_head *l) {{ l->next = l; l->prev = l; }}
static inline int list_empty(const struct list_head *h) {{ return h->next == h; }}
static inline void __list_add(struct list_head *n, struct list_head *p,
                              struct list_head *x) {{
    x->prev = n; n->next = x; n->prev = p; WRITE_ONCE(p->next, n);
}}
static inline void list_add(struct list_head *n, struct list_head *h) {{ __list_add(n, h, h->next); }}
static inline void list_add_tail(struct list_head *n, struct list_head *h) {{ __list_add(n, h->prev, h); }}
static inline void __list_del(struct list_head *p, struct list_head *x) {{
    x->prev = p; WRITE_ONCE(p->next, x);
}}
static inline void list_del(struct list_head *e) {{
    __list_del(e->prev, e->next); e->next = LIST_POISON1; e->prev = LIST_POISON2;
}}
static inline void list_del_init(struct list_head *e) {{ __list_del(e->prev, e->next); INIT_LIST_HEAD(e); }}
static inline void list_move(struct list_head *e, struct list_head *h) {{ __list_del(e->prev, e->next); list_add(e, h); }}
static inline void list_move_tail(struct list_head *e, struct list_head *h) {{ __list_del(e->prev, e->next); list_add_tail(e, h); }}

struct cgir_node {{ int id; struct list_head lh; long payload; }};
static struct cgir_node C_ARENA[{NN}];
static struct list_head C_HEAD;
/* free-event log: pairs of (slot, chain-digest-at-free-time). kfree in the
 * gate is an EVENT, not a memory op — the digest captures WHEN in the op
 * sequence the free fired, so free-before-unlink != unlink-then-free. */
static long FLOG[4096]; static int FN;
static void cgir_free_ev(struct list_head *p);
void c_reset(void) {{
    FN = 0;
    INIT_LIST_HEAD(&C_HEAD);
    for (int i = 0; i < {NN}; i++) {{ C_ARENA[i].id = i; INIT_LIST_HEAD(&C_ARENA[i].lh); }}
    /* pre-populate so del/move have something to operate on */
    for (int i = 0; i < {NN}; i++) list_add_tail(&C_ARENA[i].lh, &C_HEAD);
}}
/* COND_MODE phase 2: drained arena — every node self-looped, head empty, so
 * list_empty guards take their OTHER branch (both polarities exercised) */
void c_reset_empty(void) {{
    FN = 0;
    INIT_LIST_HEAD(&C_HEAD);
    for (int i = 0; i < {NN}; i++) {{ C_ARENA[i].id = i; INIT_LIST_HEAD(&C_ARENA[i].lh); }}
}}
#define lh_to_node(p) ((struct cgir_node *)((char *)(p) - offsetof(struct cgir_node, lh)))
/* the REAL list_for_each_entry_safe expansion: `n` is computed BEFORE the body */
void c_call_iter(void) {{
{ITER_GUARD}    struct cgir_node *pos, *n;
    for (pos = lh_to_node(C_HEAD.next), n = lh_to_node(pos->lh.next);
         &pos->lh != &C_HEAD;
         pos = n, n = lh_to_node(n->lh.next)) {{
{ITER_BODY}
    }}
}}
/* the REAL function's op sequence, applied to node `a` */
void c_call(int a) {{
    struct list_head *entry = &C_ARENA[a].lh, *head = &C_HEAD;
{chr(10).join(calls)}
}}
static long normp(void *p) {{
    if (p == (void *)&C_HEAD) return -1;
    if (p == LIST_POISON1) return -100;
    if (p == LIST_POISON2) return -101;
    for (int i = 0; i < {NN}; i++) if (p == (void *)&C_ARENA[i].lh) return i;
    return -999;
}}
static unsigned long chain_digest(void) {{
    unsigned long h = 14695981039346656037UL; int g = 0;
    struct list_head *w = C_HEAD.next;
    while (g++ < {NN} + 2) {{
        if (w == &C_HEAD) break;
        long id = normp(w);
        h = h * 1099511628211UL + (unsigned long)(id + 1000);
        if (id < 0) break;
        w = w->next;
    }}
    return h;
}}
static void cgir_free_ev(struct list_head *p) {{
    FLOG[FN++] = normp(p); FLOG[FN++] = (long)chain_digest();
}}
int c_freelog(long *buf) {{
    for (int i = 0; i < FN; i++) buf[i] = FLOG[i];
    return FN;
}}
int c_snapshot(long *buf) {{
    int k = 0; struct list_head *w = C_HEAD.next; int g = 0;
    while (w != &C_HEAD && g++ < {NN} + 2) {{
        long id = normp(w); buf[k++] = id; if (id < 0) break; w = w->next; }}
    buf[k++] = -7; w = C_HEAD.prev; g = 0;
    while (w != &C_HEAD && g++ < {NN} + 2) {{
        long id = normp(w); buf[k++] = id; if (id < 0) break; w = w->prev; }}
    buf[k++] = -8;
    for (int i = 0; i < {NN}; i++) {{
        buf[k++] = normp(C_ARENA[i].lh.next); buf[k++] = normp(C_ARENA[i].lh.prev); }}
    buf[k++] = normp(C_HEAD.next); buf[k++] = normp(C_HEAD.prev);
    return k;
}}
"""


def _cand_rs(realized, L, it=None):
    ENTRY = ('#[no_mangle] pub extern "C" fn r_call_iter() { unsafe { '
             'realized_iter(&raw mut R_HEAD); }}' if it else
             '#[no_mangle] pub extern "C" fn r_call(a: i32) { unsafe { '
             'realized_op(&raw mut R_ARENA[a as usize].lh, &raw mut R_HEAD); }}')
    return LM.emit_mirror(L) + realized + f"""
#[repr(C)]
pub struct Node {{ pub id: i32, pub lh: ListHead, pub payload: i64 }}
static mut R_ARENA: [Node; {NN}] = [const {{ Node {{ id: 0,
    lh: ListHead {{ next: core::ptr::null_mut(), prev: core::ptr::null_mut() }},
    payload: 0 }} }}; {NN}];
static mut R_HEAD: ListHead = ListHead {{ next: core::ptr::null_mut(),
                                         prev: core::ptr::null_mut() }};
static mut FLOG: [i64; 4096] = [0; 4096];
static mut FN_: usize = 0;
unsafe fn chain_digest() -> u64 {{
    let mut h: u64 = 14695981039346656037;
    let mut g = 0;
    let mut w = R_HEAD.next;
    while g < {NN} + 2 {{
        g += 1;
        if w == &raw mut R_HEAD {{ break; }}
        let id = normp(w);
        h = h.wrapping_mul(1099511628211).wrapping_add((id + 1000) as u64);
        if id < 0 {{ break; }}
        w = (*w).next;
    }}
    h
}}
#[no_mangle] pub extern "C" fn free_ev(p: *mut ListHead) {{ unsafe {{
    FLOG[FN_] = normp(p); FLOG[FN_ + 1] = chain_digest() as i64; FN_ += 2;
}}}}
#[no_mangle] pub extern "C" fn r_freelog(buf: *mut i64) -> i32 {{ unsafe {{
    for i in 0..FN_ {{ *buf.add(i) = FLOG[i]; }}
    FN_ as i32
}}}}
#[no_mangle] pub extern "C" fn r_reset_empty() {{ unsafe {{
    FN_ = 0;
    INIT_LIST_HEAD(&raw mut R_HEAD);
    for i in 0..{NN} {{ R_ARENA[i].id = i as i32; INIT_LIST_HEAD(&raw mut R_ARENA[i].lh); }}
}}}}
#[no_mangle] pub extern "C" fn r_reset() {{ unsafe {{
    FN_ = 0;
    INIT_LIST_HEAD(&raw mut R_HEAD);
    for i in 0..{NN} {{ R_ARENA[i].id = i as i32; INIT_LIST_HEAD(&raw mut R_ARENA[i].lh); }}
    for i in 0..{NN} {{ list_add_tail(&raw mut R_ARENA[i].lh, &raw mut R_HEAD); }}
}}}}
{ENTRY}
unsafe fn normp(p: *mut ListHead) -> i64 {{
    if p == &raw mut R_HEAD {{ return -1; }}
    if p as usize == {L['poison1']:#x} {{ return -100; }}
    if p as usize == {L['poison2']:#x} {{ return -101; }}
    for i in 0..{NN} {{ if p == &raw mut R_ARENA[i].lh {{ return i as i64; }} }}
    -999
}}
#[no_mangle] pub extern "C" fn r_snapshot(buf: *mut i64) -> i32 {{ unsafe {{
    let mut k = 0usize; let mut w = R_HEAD.next; let mut g = 0;
    while w != &raw mut R_HEAD && g < {NN} + 2 {{
        let id = normp(w); *buf.add(k) = id; k += 1; if id < 0 {{ break; }}
        w = (*w).next; g += 1; }}
    *buf.add(k) = -7; k += 1; w = R_HEAD.prev; g = 0;
    while w != &raw mut R_HEAD && g < {NN} + 2 {{
        let id = normp(w); *buf.add(k) = id; k += 1; if id < 0 {{ break; }}
        w = (*w).prev; g += 1; }}
    *buf.add(k) = -8; k += 1;
    for i in 0..{NN} {{
        *buf.add(k) = normp(R_ARENA[i].lh.next); k += 1;
        *buf.add(k) = normp(R_ARENA[i].lh.prev); k += 1; }}
    *buf.add(k) = normp(R_HEAD.next); k += 1;
    *buf.add(k) = normp(R_HEAD.prev); k += 1;
    k as i32
}}}}
"""


_PROBE = """#include <stdio.h>
extern void c_reset(void); extern int c_snapshot(long*); extern int r_snapshot(long*);
extern void r_reset(void);
extern int c_freelog(long*); extern int r_freelog(long*);
#if ITER_MODE
extern void c_call_iter(void); extern void r_call_iter(void);
#else
extern void c_call(int); extern void r_call(int);
#endif
static long CB[4096], RB[4096];
#define CMP(step) {                                                     \\
    int n1=c_snapshot(CB), n2=r_snapshot(RB);                           \\
    if (ADT_ONLY) { n1=adt_len(CB,n1); n2=adt_len(RB,n2); }             \\
    if (n1!=n2) { printf("CREALIZE verdict=DIVERGE step=%s reason=len %d!=%d\\n",step,n1,n2); return 1; } \\
    for (int j=0;j<n1;j++) if (CB[j]!=RB[j]) {                          \\
        printf("CREALIZE verdict=DIVERGE step=%s slot=%d c=%ld r=%ld\\n",step,j,CB[j],RB[j]); return 1; } }
/* the composed axis: the free-event log, order-sensitive. ADT_ONLY keeps the
 * slots and drops the digests — the retire-log view the ADT oracle had. */
#define FCMP(step) {                                                    \\
    int n1=c_freelog(CB), n2=r_freelog(RB);                             \\
    if (ADT_ONLY) { n1=adt_flog(CB,n1); n2=adt_flog(RB,n2); }           \\
    if (n1!=n2) { printf("CREALIZE verdict=DIVERGE step=%s reason=freelog_len %d!=%d\\n",step,n1,n2); return 1; } \\
    for (int j=0;j<n1;j++) if (CB[j]!=RB[j]) {                          \\
        printf("CREALIZE verdict=DIVERGE step=%s freelog=%d c=%ld r=%ld\\n",step,j,CB[j],RB[j]); return 1; } }
static int adt_len(long *b, int n) { for (int i=0;i<n;i++) if (b[i]==-7) return i; return n; }
static int adt_flog(long *b, int n) { for (int i=0;i+1<n;i+=2) b[i/2]=b[i]; return n/2; }
#if COND_MODE
extern void c_reset_empty(void); extern void r_reset_empty(void);
#endif
int main(void) {
    c_reset(); r_reset(); CMP("init"); FCMP("init");
#if ITER_MODE
    c_call_iter(); r_call_iter(); CMP("iter"); FCMP("iter");
#if COND_MODE
    /* phase 2: drained arena — the guard's other branch */
    c_reset_empty(); r_reset_empty(); CMP("reset2"); FCMP("reset2");
    c_call_iter(); r_call_iter(); CMP("iter2"); FCMP("iter2");
#endif
    printf("CREALIZE verdict=MATCH iter=1 frees=%d\\n", c_freelog(CB)/2);
#else
    for (int a = 0; a < NNODES; a++) { c_call(a); r_call(a); CMP("call"); FCMP("call"); }
#if COND_MODE
    /* phase 2: drained arena — every list_empty guard flips branch here */
    c_reset_empty(); r_reset_empty(); CMP("reset2"); FCMP("reset2");
    for (int a = 0; a < NNODES; a++) { c_call(a); r_call(a); CMP("call2"); FCMP("call2"); }
#endif
    printf("CREALIZE verdict=MATCH calls=%d frees=%d\\n", NNODES, c_freelog(CB)/2);
#endif
    return 0;
}
"""


def run_gate(rel, fn, L, sabotage=None, adt_only=False):
    realized, cops, aops, it = emit_realized(rel, fn, L, sabotage)
    d = tempfile.mkdtemp(prefix="crealize_")
    open(os.path.join(d, "ref.c"), "w").write(_ref_c(cops, L, it))
    open(os.path.join(d, "cand.rs"), "w").write(_cand_rs(realized, L, it))
    open(os.path.join(d, "probe.c"), "w").write(_PROBE)
    r = subprocess.run(["rustc", "--edition", "2021", "-O", "--crate-type=staticlib",
                        os.path.join(d, "cand.rs"), "-o", os.path.join(d, "libcand.a")],
                       capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL_RS", r.stderr[-900:], d
    r = subprocess.run(["cc", "-O2", "-w", f"-DNNODES={NN}",
                        f"-DADT_ONLY={1 if adt_only else 0}", f"-DITER_MODE={1 if it else 0}",
                        f"-DCOND_MODE={1 if (any(o.get('cond') for o in cops) or (it or {}).get('guard')) else 0}",
                        os.path.join(d, "probe.c"), os.path.join(d, "ref.c"),
                        os.path.join(d, "libcand.a"), "-o", os.path.join(d, "run")],
                       capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL_C", r.stderr[-900:], d
    try:
        r = subprocess.run([os.path.join(d, "run")], capture_output=True,
                           text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "HANG", "candidate did not terminate (cyclic/corrupted chain)", d
    out = (r.stdout + r.stderr).strip()
    m = re.search(r"verdict=(\w+)", out)
    if m:
        return m.group(1), out, d
    # No verdict line => the candidate died mid-run. For list code that is the
    # REAL failure mode, not a harness defect: walking a plain (non-cached)
    # iteration over a body that unlinks reads LIST_POISON1 and dereferences a
    # wild pointer — a segfault here, a kernel oops in situ. Report it as a
    # rejection with its signal, never as UNKNOWN and never as a pass.
    if r.returncode < 0:
        return "CRASH", f"killed by signal {-r.returncode} (wild-pointer deref)", d
    return "UNKNOWN", out or f"exit={r.returncode}", d


_TARGETS = [
    ("drivers/crypto/intel/qat/qat_common/adf_init.c", "adf_service_add"),
    ("drivers/crypto/intel/qat/qat_common/adf_init.c", "adf_service_remove"),
    ("drivers/crypto/cavium/nitrox/nitrox_reqmgr.c", "response_list_add"),
    ("drivers/base/syscore.c", "register_syscore_ops"),
    ("drivers/base/syscore.c", "unregister_syscore_ops"),
    ("drivers/dma-buf/dma-buf.c", "__dma_buf_list_add"),
    ("drivers/acpi/scan.c", "acpi_scan_add_handler"),
    ("drivers/clk/clkdev.c", "__clkdev_add"),
    # list_del_init users — the class where emitting `list_del` instead would be
    # an ADT-INVISIBLE defect (step-2 measurement); the reason this module reads
    # the concrete op out of the C rather than trusting the abstract model.
    ("drivers/md/dm-cache-policy.c", "dm_cache_policy_unregister"),
    ("drivers/infiniband/core/iwcm.c", "get_work"),
    # ITERATION (list_for_each_entry_safe): the walk shape is dictated by the
    # C's safe/plain choice — emitting the plain walk over a deleting body
    # dereferences LIST_POISON1 (kernel oops).
    ("drivers/net/ethernet/mellanox/mlx5/core/diag/fw_tracer.c",
     "mlx5_fw_tracer_clean_ready_list"),
    ("drivers/usb/usbip/stub_main.c", "stub_priv_pop_from_listhead"),
    ("drivers/scsi/qedi/qedi_iscsi.c", "qedi_cleanup_active_cmd_list"),
    ("drivers/mfd/abx500-core.c", "abx500_remove_ops"),
    ("security/device_cgroup.c", "dev_exceptions_move"),
]


def cmd_map(rel, fn):
    cops, _, it = c_ops(rel, fn)
    aops, _ = adt_ops(rel, fn)
    print(f"{rel}:{fn}")
    print(f"  C concrete ops : {[o['c_op'] for o in cops]}")
    print(f"  ADT model ops  : {aops}")
    print(f"  iteration      : {it}")
    print(f"  correspondence : {[(a, o['c_op']) for a, o in correspond(cops, aops)] if it is None else '(iteration: per-element)'}")
    print(f"  emitted Rust   : {[o['rs'] for o in cops]}")
    return 0


def cmd_prove(rel, fn):
    L = LM.probe_layout()
    v, out, d = run_gate(rel, fn, L)
    print(f"CREALIZE {rel}:{fn} -> {v}  [{out.splitlines()[-1][:60] if out else ''}]")
    if v != "MATCH":
        print(f"  dir={d}")
    return 0 if v == "MATCH" else 1


def cmd_batch():
    L = LM.probe_layout()
    ok = fail = ref = 0
    rows = []
    for rel, fn in _TARGETS:
        try:
            v, out, d = run_gate(rel, fn, L)
        except Refused as e:
            print(f"  {fn:28s} REFUSED: {e}")
            ref += 1
            continue
        except Exception as e:
            print(f"  {fn:28s} ERROR: {str(e)[:60]}")
            fail += 1
            continue
        cops, _, itc = c_ops(rel, fn)
        mark = "✓" if v == "MATCH" else "✗"
        tag = "  iter[safe]" if (itc and itc["safe"]) else ("  iter[plain]" if itc else "")
        print(f"  {mark} {fn:32s} {v:8s}  ops={[o['c_op'] for o in cops]}{tag}")
        if v == "MATCH":
            ok += 1
            rows.append((rel, fn, cops))
        else:
            fail += 1
    print(f"\ncontainer realize v1: {ok} REALIZED+chain-verified, {fail} failed, "
          f"{ref} refused (fail-closed)")
    # negative controls on a realized candidate — the gate must be load-bearing
    if rows:
        rel, fn, cops = rows[0]
        print(f"\nnegative controls on {fn}:")
        for sab in ("wrong_op",):
            v, _, _ = run_gate(rel, fn, L, sabotage=sab)
            rej = v in ("DIVERGE", "CRASH", "HANG")
            print(f"  {'✓' if rej else '✗ UNEXPECTED'} {sab:14s} -> {v}")
            ok &= rej
    # the ADT-invisible class, on a del_init candidate if we have one
    di = [(r, f) for r, f, c in rows if any(o["c_op"] == "list_del_init" for o in c)]
    if di:
        rel, fn = di[0]
        full, _, _ = run_gate(rel, fn, L, sabotage="del_not_init")
        adt, _, _ = run_gate(rel, fn, L, sabotage="del_not_init", adt_only=True)
        print(f"\n  del_not_init on {fn}: structural={full}, ADT-only={adt}"
              f"  {'<-- ADT ORACLE BLIND' if adt == 'MATCH' and full == 'DIVERGE' else ''}")
    # the ITERATION control: plain walk over a deleting body = poison deref
    iters = [(r, f) for r, f, c in rows
             if (c_ops(r, f)[2] or {}).get("safe")]
    if iters:
        rel, fn = iters[0]
        v, out, _ = run_gate(rel, fn, L, sabotage="unsafe_iter")
        rej = v in ("CRASH", "DIVERGE", "HANG")
        print(f"\n  {'✓' if rej else '✗ UNEXPECTED'} unsafe_iter on {fn} -> {v}"
              f"  ({out.splitlines()[-1][:50] if out else ''})")
        print("    (the plain walk reads pos->next AFTER list_del poisoned it —"
              " a wild pointer here and a kernel oops in situ)")
        if not rej:
            fail += 1
    return 0 if fail == 0 else 1


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "batch":
        return cmd_batch()
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    cmd, rel, fn = sys.argv[1], sys.argv[2], sys.argv[3]
    return {"map": cmd_map, "prove": cmd_prove}[cmd](rel, fn)


if __name__ == "__main__":
    raise SystemExit(main())
