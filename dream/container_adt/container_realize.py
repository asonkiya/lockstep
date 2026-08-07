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

v1 scope: single list, straight-line (no iteration), no allocation. `retire()`
(kfree) is T3 and is REFUSED here — it needs composition with the allocator
model, not a list oracle (per CONTAINERS-FEASIBILITY).

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
    if re.search(r"\b(kfree|kmem_cache_free|kzalloc|kmalloc)\b", body):
        raise Refused("allocation_present:T3_needs_allocmodel")
    it = _classify_iteration(body)
    ops = []
    for m in re.finditer(r"\b(" + "|".join(_C_OPS) + r")\s*\(", body):
        name = m.group(1)
        args, _ = _split_call(body, m.end() - 1)
        ops.append({"c_op": name, "adt": _C_OPS[name][0], "rs": _C_OPS[name][1],
                    "args": [a.strip() for a in args]})
    if not ops:
        raise Refused("no_list_ops_in_c")
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


def _norm_expr(e):
    return re.sub(r"[\s&]", "", e or "")


def _classify_iteration(body):
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
    if re.search(r"\bif\b|\?", body):
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
    if re.search(r"(?<![\w])retire\s*\(", body):
        raise Refused("retire:T3_needs_allocmodel")
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
                                         "move_tail", "move_front")]
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

def emit_realized(rel, fn, L, sabotage=None):
    cops, ctext, it = c_ops(rel, fn)
    aops, abody = adt_ops(rel, fn)
    if it is None:
        correspond(cops, aops)
    if len({o["c_op"] for o in cops}) != len(cops) and len(cops) > 1:
        pass                        # repeated same op is fine
    lines = []
    for o in cops:
        rs = o["rs"]
        if sabotage == "wrong_op":          # list_add <-> list_add_tail
            rs = {"list_add": "list_add_tail", "list_add_tail": "list_add"}.get(rs, rs)
        if sabotage == "del_not_init":      # the ADT-INVISIBLE defect class
            rs = {"list_del_init": "list_del"}.get(rs, rs)
        if rs in ("list_add", "list_add_tail"):
            lines.append(f"        {rs}(entry, head);")
        elif rs in ("list_del", "list_del_init", "INIT_LIST_HEAD"):
            lines.append(f"        {rs}(entry);")
        else:                                # list_move / list_move_tail
            lines.append(f"        __list_del((*entry).prev, (*entry).next);")
            lines.append(f"        {'list_add' if rs=='list_move' else 'list_add_tail'}(entry, head);")
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
        if o["c_op"] in ("list_add", "list_add_tail"):
            calls.append(f"    {o['c_op']}(entry, head);")
        elif o["c_op"] in ("list_del", "list_del_init", "INIT_LIST_HEAD"):
            calls.append(f"    {o['c_op']}(entry);")
        else:
            calls.append(f"    {o['c_op']}(entry, head);")
    iter_calls = []
    for o in cops:
        if o["c_op"] in ("list_del", "list_del_init", "INIT_LIST_HEAD"):
            iter_calls.append(f"        {o['c_op']}(&pos->lh);")
        elif o["c_op"] in ("list_add", "list_add_tail"):
            iter_calls.append(f"        {o['c_op']}(&pos->lh, head);")
        else:
            iter_calls.append(f"        {o['c_op']}(&pos->lh, &C_HEAD);")
    ITER_BODY = chr(10).join(iter_calls) if it else "        (void)pos;"
    return f"""
#include <stddef.h>
#define WRITE_ONCE(x, v) (*(volatile typeof(x) *)&(x) = (v))
#define LIST_POISON1 ((void *) {L['poison1']:#x}UL)
#define LIST_POISON2 ((void *) {L['poison2']:#x}UL)
struct list_head {{ struct list_head *next, *prev; }};
static inline void INIT_LIST_HEAD(struct list_head *l) {{ l->next = l; l->prev = l; }}
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
void c_reset(void) {{
    INIT_LIST_HEAD(&C_HEAD);
    for (int i = 0; i < {NN}; i++) {{ C_ARENA[i].id = i; INIT_LIST_HEAD(&C_ARENA[i].lh); }}
    /* pre-populate so del/move have something to operate on */
    for (int i = 0; i < {NN}; i++) list_add_tail(&C_ARENA[i].lh, &C_HEAD);
}}
#define lh_to_node(p) ((struct cgir_node *)((char *)(p) - offsetof(struct cgir_node, lh)))
/* the REAL list_for_each_entry_safe expansion: `n` is computed BEFORE the body */
void c_call_iter(void) {{
    struct cgir_node *pos, *n;
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
#[no_mangle] pub extern "C" fn r_reset() {{ unsafe {{
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
#if ITER_MODE
extern void c_call_iter(void); extern void r_call_iter(void);
#else
extern void c_call(int); extern void r_call(int);
#endif
static long CB[512], RB[512];
#define CMP(step) {                                                     \\
    int n1=c_snapshot(CB), n2=r_snapshot(RB);                           \\
    if (ADT_ONLY) { n1=adt_len(CB,n1); n2=adt_len(RB,n2); }             \\
    if (n1!=n2) { printf("CREALIZE verdict=DIVERGE step=%s reason=len %d!=%d\\n",step,n1,n2); return 1; } \\
    for (int j=0;j<n1;j++) if (CB[j]!=RB[j]) {                          \\
        printf("CREALIZE verdict=DIVERGE step=%s slot=%d c=%ld r=%ld\\n",step,j,CB[j],RB[j]); return 1; } }
static int adt_len(long *b, int n) { for (int i=0;i<n;i++) if (b[i]==-7) return i; return n; }
int main(void) {
    c_reset(); r_reset(); CMP("init");
#if ITER_MODE
    c_call_iter(); r_call_iter(); CMP("iter");
    printf("CREALIZE verdict=MATCH iter=1\\n");
#else
    for (int a = 0; a < NNODES; a++) { c_call(a); r_call(a); CMP("call"); }
    printf("CREALIZE verdict=MATCH calls=%d\\n", NNODES);
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
