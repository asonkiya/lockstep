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

Conditional classes (all fail-closed, each predicate EXECUTED on both gate
sides, never re-emitted from guard-stripped ops): bare `!?list_empty(expr)`
guards; tokf equality (`cursor->field ==|!= token` inside an iteration);
or_null pop (`x = list_first_entry_or_null(...); if (x) del; return x` — the
value-return `realized_pop` ABI, probe drains past empty); param null-guards
(`if (p) ops` / `if (!p) return;` — the probe adds a NULL-param phase).

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
        # param names from the real signature — the pnull class must prove
        # the guarded pointer is a parameter (fail-closed on parse trouble)
        proto = text[:text.index("{")]
        params = []
        try:
            pargs0, _ = _split_call(proto, proto.index("("))
            for a in pargs0:
                mm = re.search(r"([A-Za-z_]\w*)\s*$", a.strip())
                if mm:
                    params.append(mm.group(1))
        except Exception:
            params = []
        guard = _parse_guard(body, it, params)
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
    # the gate arena links nodes through ONE probed member: ops that mutate
    # through TWO different members of the node (rio_mport_delete_*_filter,
    # free_cg_rpool_locked) would be collapsed onto one offset — a
    # double-del there is a LIST_POISON deref (measured CRASH, 2026-08-09),
    # and a silent collapse is worse. Refused by name; the multi-member
    # arena is a future feature, same family as multi_head_iteration.
    # INIT_LIST_HEAD is exempt: a self-loop init is a no-op on the fresh
    # arena regardless of which member it names.
    mut_members = set()
    for o in ops:
        if o["c_op"] in ("kfree", "INIT_LIST_HEAD"):
            continue
        am1 = re.fullmatch(r"&\s*([A-Za-z_]\w*)\s*->\s*(\w+)",
                           (o["args"][0] or "").strip())
        if am1:
            mut_members.add(am1.group(2))
    if len(mut_members) > 1:
        raise Refused(f"multi_member_ops:{','.join(sorted(mut_members))}")
    if guard and it is None and guard.get("kind") == "ornull":
        # the POP shape: exactly one del-class op on &x->member, inside the
        # guard, plus the value leg (`return x`) — anything else is an unseen
        # or_null shape and stays refused
        g = guard["ornull"]
        if len(ops) != 1 or ops[0]["c_op"] not in ("list_del", "list_del_init"):
            raise Refused("ornull:op_shape")
        o = ops[0]
        am2 = re.fullmatch(r"&\s*([A-Za-z_]\w*)\s*->\s*(\w+)",
                           o["args"][0].strip())
        if not am2 or am2.group(1) != g["var"] or am2.group(2) != g["member"]:
            raise Refused(f"ornull:op_target:{o['args'][0][:30]}")
        if not (guard["extent"][0] <= o["pos"] < guard["extent"][1]):
            raise Refused("ornull:op_outside_guard")
        if not re.search(rf"\breturn\s+{re.escape(g['var'])}\s*;", body):
            raise Refused("ornull:no_value_return")
        o["cond"] = ("ornull", "first")
        o["tgt"] = "first"
    elif guard and it is None:
        _apply_guard(ops, guard)
    if guard and it is not None:
        it["guard"] = guard["loop_guard"]
        if guard.get("tok"):
            it["tok"] = guard["tok"]
            lo, hi = guard["tok_extent"]
            for o in ops:
                if not (lo <= o["pos"] < hi):
                    raise Refused("tok_guard:ops_outside_guard")
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


def _parse_guard(body, it, params=None):
    """The supported guard classes, fail-closed. Exactly ONE `if`, no else
    with ops, no ternary. Predicate classes:
      * `!?list_empty(expr)`     — both gate sides execute the real predicate
      * `x` where x is bound by list_first_entry_or_null — the or_null POP
        (kind='ornull'); needs the value-return gate ABI
      * `x` / `!x` where x is a PARAM whose member the ops target — the
        null-tolerant guard (kind='pnull'); the probe adds a NULL-param phase
    The truthiness classes activate only when the caller supplies `params`
    (the weave caller does not — it stays on the boot-proven list_empty
    class). Iteration bodies additionally support the early-return-before-walk
    list_empty shape and the tokf equality class."""
    if "?" in re.sub(r'"[^"]*"', "", body):
        raise Refused("conditional_body:ternary")
    ifs = list(re.finditer(r"\bif\s*\(", body))
    if len(ifs) != 1:
        raise Refused("conditional_body:multi_guard")
    m = ifs[0]
    if it is not None:
        lm0 = re.search(r"\blist_for_each_entry(?:_safe)?\s*\(", body)
        if lm0 and m.start() > lm0.start():
            # a guard INSIDE the loop body: the only supported shape is the
            # tokf equality class (member ==/!= loop-invariant token) — the
            # per-element condition the ADT models express as
            # `if tokf(id, T_*) == a0`. Everything else stays refused.
            return _parse_tok_guard(body, m, lm0)
    pargs, pend = _split_call(body, m.end() - 1)
    pred = ",".join(pargs).strip()
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
    pm = re.fullmatch(r"(!?)\s*list_empty\s*\(\s*([^()]+?)\s*\)", pred)
    if pm:
        pol = "not_empty" if pm.group(1) else "empty"   # TRUE-branch polarity
        pred_expr = _norm_expr(pm.group(2))
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
    # step 3: pointer-truthiness predicates — bare `x` / `!x` only
    tm = re.fullmatch(r"(!?)\s*([A-Za-z_]\w*)", pred)
    if params is None or not tm:
        raise Refused(f"conditional_body:non_list_empty_pred:{pred[:40]}")
    if it is not None:
        raise Refused("conditional_loop_body:truthiness_in_loop")
    neg, var = tm.group(1), tm.group(2)
    om = re.search(rf"\b{re.escape(var)}\s*=\s*list_first_entry_or_null\s*\(",
                   body)
    if om:
        # the or_null POP: x = first-or-null(head); if (x) del(&x->member).
        # Truthiness of x IS first-of-head existence — but only if x is bound
        # exactly once and never negated into an unseen shape.
        if neg:
            raise Refused("ornull:negated_pred_unseen")
        largs, _ = _split_call(body, om.end() - 1)
        if len(largs) != 3:
            raise Refused("ornull:bad_binding")
        if len(re.findall(rf"\b{re.escape(var)}\s*=(?!=)", body)) != 1:
            raise Refused("ornull:var_rebound")
        return {"loop_guard": None, "kind": "ornull",
                "ornull": {"var": var, "head": _norm_expr(largs[0]),
                           "member": largs[2].strip()},
                "extent": extent, "if_start": m.start()}
    if var not in params:
        # truthiness of a local that is neither the or_null binding nor a
        # param: state the arena does not model — refused, not guessed
        raise Refused(f"conditional_body:truthiness_nonparam:{var}")
    if re.findall(rf"\b{re.escape(var)}\s*=(?!=)", body):
        raise Refused("pnull:param_reassigned")
    return {"loop_guard": None, "kind": "pnull",
            "pol": "null" if neg else "nonnull", "pred": var,
            "extent": extent, "inverts_rest": inverts_rest,
            "if_start": m.start()}


def _parse_tok_guard(body, ifm, lm0):
    """Tokf equality class, fail-closed: iterate, compare ONE cursor member
    against a loop-invariant token, del/kfree on match. Predicate forms
    `cursor->field ==|!= rhs` or `rhs ==|!= cursor->field`; rhs must be a
    call-free param path (`x` / `x->y` / `x.y`) that never names the cursor
    (a cursor-dependent rhs is not a token). `break`/`continue`/`goto` change
    which matches are deleted under DUPLICATE tokens — the delete-first-and-
    stop shape is refused this slice, not silently modeled as delete-all."""
    if re.search(r"\b(break|continue|goto)\b", body):
        raise Refused("tok_guard:break_in_loop")
    if re.search(r"\belse\b", body):
        raise Refused("tok_guard:else_branch")
    largs, _ = _split_call(body, lm0.end() - 1)
    cursor = largs[0].strip()
    pargs, pend = _split_call(body, ifm.end() - 1)
    pred = " ".join(",".join(pargs).split())
    rhs_pat = r"[A-Za-z_]\w*(?:\s*(?:->|\.)\s*\w+)*"
    ma = re.fullmatch(rf"{re.escape(cursor)}\s*->\s*(\w+)\s*(==|!=)\s*({rhs_pat})", pred)
    mb = re.fullmatch(rf"({rhs_pat})\s*(==|!=)\s*{re.escape(cursor)}\s*->\s*(\w+)", pred)
    if ma:
        field, op, rhs = ma.group(1), ma.group(2), ma.group(3)
    elif mb:
        rhs, op, field = mb.group(1), mb.group(2), mb.group(3)
    else:
        raise Refused(f"conditional_loop_body:non_tok_pred:{pred[:40]}")
    if re.search(rf"\b{re.escape(cursor)}\b", rhs):
        raise Refused("tok_guard:rhs_uses_cursor")
    # guard extent: every list op / kfree in the loop must sit INSIDE it —
    # an op outside the guard would be silently unconditionalized
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
    return {"loop_guard": None, "tok": {"field": field, "op": op},
            "tok_extent": extent}


def _check_ornull_model(abody):
    """The pop model must consult first-of-list emptiness — `empty(L)` or a
    sentinel compare on `first(L)`'s result. A model popping unconditionally
    verified while describing a different function (the net_unlink_todo
    banked-model-defect family)."""
    if not re.search(r"\bfirst\s*\(", abody):
        raise Refused("ornull_model:no_first")
    if not (re.search(r"\bempty\s*\(", abody)
            or re.search(r"(?:!=|==)\s*-1|>=\s*0|<\s*0", abody)):
        raise Refused("ornull_model:no_empty_or_sentinel")


def _check_pnull_model(abody):
    """The model must guard on the ARG's null sentinel (`a0 != -1`,
    `a0 == -1 { return }`, `a0 >= 0`). Dialects that encode the null check as
    a tokf/field read of the id (nfp_port_free, nand_ecc_unregister_...)
    cannot be executed-and-corresponded by this gate — named worklist,
    never gated."""
    if not re.search(r"\ba\d+(?:\s+as\s+\w+)*\s*(?:!=|==)\s*-1|\ba\d+\s*>=\s*0",
                     abody):
        raise Refused("pnull_model:no_arg_sentinel_guard")


def _check_tok_model(tok, abody, fn):
    """Correspondence for the tokf class: the verified model must compare the
    SAME member the C compares (its T_*/F_* constant names the field), with
    the same operator. A model comparing a different member verified while
    describing a different function — the banked-model defect family
    (net_unlink_todo); refused by name, never gated."""
    want = tok["field"].upper()
    hits = re.findall(r"(?:tokf|field)\s*\(\s*\w+\s*,\s*[TF]_([A-Z0-9_]+)\s*\)"
                      r"(?:\s+as\s+\w+)?\s*(==|!=)", abody)
    if not hits:
        raise Refused(f"tokf_field_mismatch:model_has_no_tok_compare,c={tok['field']}")
    fields = {f for f, _o in hits}
    if want not in fields:
        raise Refused(f"tokf_field_mismatch:model={sorted(fields)},c={tok['field']}")
    ops = {o for f, o in hits if f == want}
    if tok["op"] not in ops:
        raise Refused(f"tokf_polarity_mismatch:model={sorted(ops)},c={tok['op']}")


def _apply_guard(ops, g):
    """Annotate straight-line ops with (polarity, target) from the single
    parsed guard; every op must land in a branch whose polarity is known."""
    if g.get("kind") == "pnull":
        # null-tolerant param guard: every op (kfree included) must be in the
        # nonnull branch and rooted at the guarded param — a NULL-branch op
        # or an op on another object is an unseen shape, refused
        invn = {"nonnull": "null", "null": "nonnull"}
        for o in ops:
            if g["extent"][0] <= o["pos"] < g["extent"][1]:
                pol = g["pol"]
            elif o["pos"] >= g["extent"][1] and g["inverts_rest"]:
                pol = invn[g["pol"]]
            else:
                raise Refused("pnull:unguarded_op")
            if pol != "nonnull":
                raise Refused("pnull:op_in_null_branch")
            if _base_of(o["args"][0]) != g["pred"]:
                raise Refused(f"pnull:op_base_mismatch:{_base_of(o['args'][0])}")
            o["cond"] = ("nonnull", "entry")
        return
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

_ADT_OPS = ("push_back", "push_front", "del_m", "del", "move_tail",
            "move_front", "iter", "empty", "first", "last", "retire",
            "set_field", "field")


def _adt_seq(body):
    """Ordered ADT op names in a model body. `del_m(M_X, id)` — the
    multi-membership dialect the surface itself generates — counts as `del`:
    8 T3 banked models were structurally CORRECT and refused only because
    this parser could not see del_m (worklist repair 2026-08-09)."""
    seq = [mm.group(1) for mm in
           re.finditer(r"(?<![\w])(" + "|".join(_ADT_OPS) + r")\s*\(", body)]
    return ["del" if o == "del_m" else o
            for o in seq if o not in ("field", "set_field")]


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
    return _adt_seq(body), body


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
    moves into del+push on both sides. A C-side INIT_LIST_HEAD op is OPTIONAL
    on the model side: the harness surface documents fresh-node / sub-anchor
    INIT as a no-op (models were told to omit it), while the older banked
    dialect renders it as `del` — both align. The realized emission takes its
    concrete ops from the REAL C either way, so the woven code still performs
    the INIT and the gate compares identical concrete ops on both sides; only
    the correspondence witness is relaxed, and only for INIT."""
    mutators = [o for o in aops if o in ("push_back", "push_front", "del",
                                         "move_tail", "move_front", "retire")]
    a_can = _canon(mutators)
    c_items = []                     # (class, optional) in canonical order
    for o in cops:
        for cls in _CANON.get(o["adt"], [o["adt"]]):
            c_items.append((cls, o["c_op"] == "INIT_LIST_HEAD"))

    def _align(ci, ai):
        if ci == len(c_items):
            return ai == len(a_can)
        cls, opt = c_items[ci]
        if ai < len(a_can) and a_can[ai] == cls and _align(ci + 1, ai + 1):
            return True
        return opt and _align(ci + 1, ai)

    if not _align(0, 0):
        n_req = sum(1 for _, opt in c_items if not opt)
        raise Refused(f"op_count_mismatch:c={len(c_items)},adt={len(a_can)}"
                      if len(a_can) < n_req or len(a_can) > len(c_items)
                      else f"op_class_mismatch:c={[c for c, _ in c_items]},"
                           f"adt={a_can}")
    return list(zip(mutators, cops))


# ---------------------------------------------------------------------------
# 4. emission — Rust over the ListHead mirror, real pointers
# ---------------------------------------------------------------------------

_INV_POL = {"empty": "not_empty", "not_empty": "empty",
            "nonnull": "null", "null": "nonnull"}


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


def _check_empty_consult(cops, it, abody):
    """The model must consult the SAME emptiness the C guards on — else it
    verified while describing a different function (the original
    no_empty_in_model family). Dialects by guard target:
      * head-target (`list_empty(&head)`): `empty(` — the list_empty class.
      * entry-target (`list_empty(&node->member)`, i.e. "is the NODE
        linked?"): `linked(`/`linked_m(` or an `iter(..).contains` scan.
      * EXCEPTION (canonically redundant): a not_empty guard on a del-class
        op's OWN member needs no consult — del of an unlinked-inited node is
        a no-op on both gate sides (measured in the list_empty class:
        drop_guard on a guarded del_init MATCHes); the guard is a C-side
        optimization, and the emission still emits it from the C ops."""
    need_head = it is not None and bool(it.get("guard"))
    need_entry = False
    for o in cops:
        c = o.get("cond")
        if not c or c[0] not in ("empty", "not_empty"):
            continue
        if c[1] == "head":
            need_head = True
        elif c[0] == "not_empty" and o["adt"] == "del":
            continue                     # redundant self-membership guard
        else:
            need_entry = True
    if need_head and "empty" not in abody:
        raise Refused("no_empty_in_model")
    if need_entry and not re.search(r"\blinked(?:_m)?\s*\(|\.contains\s*\(",
                                    abody):
        raise Refused("no_empty_in_model:entry_target_needs_linked")


def model_check(rel, fn, abody):
    """Structural correspondence of a model BODY against the real C — the
    full model-side check battery, usable at synth time (before banking) and
    at re-verification. Raises Refused; C-side front refusals propagate (the
    fn is out of realize scope, which is not a model defect)."""
    cops, _t, it = c_ops(rel, fn)
    if it is None:
        correspond(cops, _adt_seq(abody))
    conds = {o["cond"][0] for o in cops if o.get("cond")}
    if it and it.get("guard"):
        conds.add("not_empty")
    if conds & {"empty", "not_empty"}:
        _check_empty_consult(cops, it, abody)
    if "ornull" in conds:
        _check_ornull_model(abody)
    if conds & {"nonnull", "null"}:
        _check_pnull_model(abody)
    if it and it.get("tok"):
        _check_tok_model(it["tok"], abody, fn)


def emit_realized(rel, fn, L, sabotage=None):
    cops, ctext, it = c_ops(rel, fn)
    aops, abody = adt_ops(rel, fn)
    if it is None:
        correspond(cops, aops)
    conds = {o["cond"][0] for o in cops if o.get("cond")}
    if it and it.get("guard"):
        conds.add("not_empty")
    if conds & {"empty", "not_empty"}:
        _check_empty_consult(cops, it, abody)
    if "ornull" in conds:
        _check_ornull_model(abody)
    if conds & {"nonnull", "null"}:
        _check_pnull_model(abody)
    if it and it.get("tok"):
        _check_tok_model(it["tok"], abody, fn)
    if "ornull" in conds:
        # the POP shape: guarded first-del + the value leg, one emission
        o = cops[0]
        rs = o["rs"]
        if sabotage == "del_not_init":
            rs = {"list_del_init": "list_del"}.get(rs, rs)
        if sabotage == "drop_guard":
            inner = (f"        let e = (*head).next;\n"
                     f"        {rs}(e);\n"
                     f"        e")
        else:
            cmp_ = "==" if sabotage == "flip_guard" else "!="
            e_expr = ("(*(*head).next).next" if sabotage == "skip_first"
                      else "(*head).next")
            inner = (f"        if (*head).next {cmp_} head {{\n"
                     f"            let e = {e_expr};\n"
                     f"            {rs}(e);\n"
                     f"            e\n"
                     f"        }} else {{\n"
                     f"            core::ptr::null_mut()\n"
                     f"        }}")
        return f"""
#[no_mangle]
pub extern "C" fn realized_pop(head: *mut ListHead) -> *mut ListHead {{
    unsafe {{
{inner}
    }}
}}
""", cops, aops, it
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
            core = ["free_ev(head);" if sabotage == "wrong_free"
                    else "free_ev(entry);"]
        elif rs in ("list_add", "list_add_tail"):
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
            if pol in ("nonnull", "null"):
                neg = "!" if pol == "nonnull" else ""
                lines.append(f"        if {neg}entry.is_null() {{")
            else:
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
        tok = None if sabotage == "drop_guard" else it.get("tok")
        if tok:
            # token read via container arithmetic from the link pointer —
            # wrong_field reads a DIFFERENT member (the id), the exact
            # translation defect this class risks
            fld = "id as i64" if sabotage == "wrong_field" else "payload"
            cmp_ = tok["op"]
            if sabotage == "flip_guard":
                cmp_ = "!=" if cmp_ == "==" else "=="
            inner = (f"            let node = (pos as *mut u8)"
                     f".sub(core::mem::offset_of!(Node, lh)) as *mut Node;\n"
                     f"            if ((*node).{fld}) {cmp_} tok {{\n"
                     + "\n".join("    " + l for l in inner.splitlines())
                     + "\n            }")
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
            # flip_guard must reach THIS shape too — without it the sabotage
            # silently no-ops and the negative control is vacuous (measured:
            # cxgb4 flip_guard -> MATCH before this branch existed)
            g_cmp = "==" if sabotage == "flip_guard" else "!="
            walk = (f"        if (*head).next {g_cmp} head {{\n"
                    + walk + "\n        }")
        sig_tok = ", tok: i64" if it.get("tok") else ""
        return f"""
#[no_mangle] #[allow(unused_variables)]
pub extern "C" fn realized_iter(head: *mut ListHead{sig_tok}) {{
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
    pop = bool(cops and cops[0].get("cond")
               and cops[0]["cond"][0] == "ornull")
    calls = []
    for o in ([] if pop else cops):
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
            if pol in ("nonnull", "null"):
                neg = "" if pol == "nonnull" else "!"
                calls.append(f"    if ({neg}entry) {{ "
                             + " ".join(core) + " }")
            else:
                t = "entry" if ct == "entry" else "head"
                neg = "" if pol == "empty" else "!"
                calls.append(f"    if ({neg}list_empty({t})) {{ "
                             + " ".join(core) + " }")
        else:
            calls += [f"    {c}" for c in core]
    if pop:
        calls = ["    (void)entry; (void)head;"]
    # the REAL list_first_entry_or_null expansion + truthiness guard,
    # EXECUTED against arena state (never re-emitted from the guard-stripped
    # op list — the T3-audit rule), plus the value leg the probe compares
    POP_FN = (f"""
long c_call_pop(void) {{
    struct cgir_node *x = C_HEAD.next != &C_HEAD ? lh_to_node(C_HEAD.next) : 0;
    if (x)
        {cops[0]['c_op']}(&x->lh);
    return x ? normp(&x->lh) : -2;
}}
""" if pop else "")
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
    if it and it.get("tok"):
        # the C side reads the REAL arena member and applies the C's operator
        ITER_BODY = (f"        if (pos->payload {it['tok']['op']} tok) {{\n"
                     + "\n".join("    " + l for l in ITER_BODY.splitlines())
                     + "\n        }")
    ITER_SIG = "long tok" if (it and it.get("tok")) else "void"
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
    /* payload doubles as the TOKEN field, with DUPLICATES (i%3) so token
     * guards distinguish match-subsets: delete-all, delete-none and
     * wrong-field all produce different chains */
    for (int i = 0; i < {NN}; i++) {{ C_ARENA[i].id = i;
        C_ARENA[i].payload = i % 3; INIT_LIST_HEAD(&C_ARENA[i].lh); }}
    /* pre-populate so del/move have something to operate on */
    for (int i = 0; i < {NN}; i++) list_add_tail(&C_ARENA[i].lh, &C_HEAD);
}}
/* COND_MODE phase 2: drained arena — every node self-looped, head empty, so
 * list_empty guards take their OTHER branch (both polarities exercised) */
void c_reset_empty(void) {{
    FN = 0;
    INIT_LIST_HEAD(&C_HEAD);
    for (int i = 0; i < {NN}; i++) {{ C_ARENA[i].id = i;
        C_ARENA[i].payload = i % 3; INIT_LIST_HEAD(&C_ARENA[i].lh); }}
}}
#define lh_to_node(p) ((struct cgir_node *)((char *)(p) - offsetof(struct cgir_node, lh)))
/* the REAL list_for_each_entry_safe expansion: `n` is computed BEFORE the body */
void c_call_iter({ITER_SIG}) {{
{ITER_GUARD}    struct cgir_node *pos, *n;
    for (pos = lh_to_node(C_HEAD.next), n = lh_to_node(pos->lh.next);
         &pos->lh != &C_HEAD;
         pos = n, n = lh_to_node(n->lh.next)) {{
{ITER_BODY}
    }}
}}
/* the REAL function's op sequence, applied to node `a` (a<0 = NULL param —
 * the pnull class's probe phase exercises the real truthiness branch) */
void c_call(int a) {{
    struct list_head *entry = a < 0 ? 0 : &C_ARENA[a].lh, *head = &C_HEAD;
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
{POP_FN}"""


def _cand_rs(realized, L, it=None, pop=False):
    if pop:
        ENTRY = ('#[no_mangle] pub extern "C" fn r_call_pop() -> i64 { unsafe { '
                 'let p = realized_pop(&raw mut R_HEAD); '
                 'if p.is_null() { -2 } else { normp(p) } }}')
    elif it and it.get("tok"):
        ENTRY = ('#[no_mangle] pub extern "C" fn r_call_iter(tok: i64) { unsafe { '
                 'realized_iter(&raw mut R_HEAD, tok); }}')
    elif it:
        ENTRY = ('#[no_mangle] pub extern "C" fn r_call_iter() { unsafe { '
                 'realized_iter(&raw mut R_HEAD); }}')
    else:
        ENTRY = ('#[no_mangle] pub extern "C" fn r_call(a: i32) { unsafe { '
                 'let e = if a < 0 { core::ptr::null_mut() } '
                 'else { &raw mut R_ARENA[a as usize].lh }; '
                 'realized_op(e, &raw mut R_HEAD); }}')
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
    for i in 0..{NN} {{ R_ARENA[i].id = i as i32;
        R_ARENA[i].payload = (i % 3) as i64;
        INIT_LIST_HEAD(&raw mut R_ARENA[i].lh); }}
}}}}
#[no_mangle] pub extern "C" fn r_reset() {{ unsafe {{
    FN_ = 0;
    INIT_LIST_HEAD(&raw mut R_HEAD);
    for i in 0..{NN} {{ R_ARENA[i].id = i as i32;
        R_ARENA[i].payload = (i % 3) as i64;
        INIT_LIST_HEAD(&raw mut R_ARENA[i].lh); }}
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
#if POP_MODE
extern long c_call_pop(void); extern long r_call_pop(void);
#elif ITER_MODE
#if TOK_MODE
extern void c_call_iter(long); extern void r_call_iter(long);
#else
extern void c_call_iter(void); extern void r_call_iter(void);
#endif
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
#if COND_MODE || TOK_MODE
extern void c_reset_empty(void); extern void r_reset_empty(void);
#endif
int main(void) {
    c_reset(); r_reset(); CMP("init"); FCMP("init");
#if POP_MODE
    /* pop until well past drain: the empty->null->no-op tail must be
     * distinguishable from one-extra-pop, and the VALUE leg must agree */
    for (int k = 0; k < NNODES + 2; k++) {
        long rc = c_call_pop(), rr = r_call_pop();
        if (rc != rr) { printf("CREALIZE verdict=DIVERGE step=popval k=%d c=%ld r=%ld\\n", k, rc, rr); return 1; }
        CMP("pop"); FCMP("pop");
    }
    printf("CREALIZE verdict=MATCH pops=%d\\n", NNODES + 2);
#elif ITER_MODE
#if TOK_MODE
    /* token sweep with DUPLICATE arena tokens (i%3): t=0..2 delete different
     * subsets, t=3 matches nothing; plus the drained-arena phase */
    for (long t = 0; t <= 3; t++) {
        c_reset(); r_reset(); CMP("treset"); FCMP("treset");
        c_call_iter(t); r_call_iter(t); CMP("titer"); FCMP("titer");
    }
    c_reset_empty(); r_reset_empty(); CMP("treset_e"); FCMP("treset_e");
    c_call_iter(0); r_call_iter(0); CMP("titer_e"); FCMP("titer_e");
    printf("CREALIZE verdict=MATCH iter=1 toksweep=1 frees=%d\\n", c_freelog(CB)/2);
#else
    c_call_iter(); r_call_iter(); CMP("iter"); FCMP("iter");
#if COND_MODE
    /* phase 2: drained arena — the guard's other branch */
    c_reset_empty(); r_reset_empty(); CMP("reset2"); FCMP("reset2");
    c_call_iter(); r_call_iter(); CMP("iter2"); FCMP("iter2");
#endif
    printf("CREALIZE verdict=MATCH iter=1 frees=%d\\n", c_freelog(CB)/2);
#endif
#else
#if PNULL_MODE
    /* the NULL-param phase: the real truthiness guard's other branch */
    c_call(-1); r_call(-1); CMP("nullcall"); FCMP("nullcall");
#endif
    for (int a = 0; a < NNODES; a++) { c_call(a); r_call(a); CMP("call"); FCMP("call"); }
#if PNULL_MODE
    c_call(-1); r_call(-1); CMP("nullcall2"); FCMP("nullcall2");
#endif
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
    pop = bool(cops and cops[0].get("cond") and cops[0]["cond"][0] == "ornull")
    pnull = any((o.get("cond") or ("",))[0] == "nonnull" for o in cops)
    d = tempfile.mkdtemp(prefix="crealize_")
    open(os.path.join(d, "ref.c"), "w").write(_ref_c(cops, L, it))
    open(os.path.join(d, "cand.rs"), "w").write(_cand_rs(realized, L, it, pop))
    open(os.path.join(d, "probe.c"), "w").write(_PROBE)
    r = subprocess.run(["rustc", "--edition", "2021", "-O", "--crate-type=staticlib",
                        os.path.join(d, "cand.rs"), "-o", os.path.join(d, "libcand.a")],
                       capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL_RS", r.stderr[-900:], d
    r = subprocess.run(["cc", "-O2", "-w", f"-DNNODES={NN}",
                        f"-DADT_ONLY={1 if adt_only else 0}", f"-DITER_MODE={1 if it else 0}",
                        f"-DPOP_MODE={1 if pop else 0}",
                        f"-DPNULL_MODE={1 if pnull else 0}",
                        f"-DCOND_MODE={1 if (any(o.get('cond') for o in cops) or (it or {}).get('guard')) else 0}",
                        f"-DTOK_MODE={1 if (it or {}).get('tok') else 0}",
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
