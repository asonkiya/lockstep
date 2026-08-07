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
    if re.search(r"\b(list_for_each|while|for)\b", body):
        raise Refused("iteration:v1_is_straight_line_only")
    ops = []
    for m in re.finditer(r"\b(" + "|".join(_C_OPS) + r")\s*\(", body):
        name = m.group(1)
        args, _ = _split_call(body, m.end() - 1)
        ops.append({"c_op": name, "adt": _C_OPS[name][0], "rs": _C_OPS[name][1],
                    "args": [a.strip() for a in args]})
    if not ops:
        raise Refused("no_list_ops_in_c")
    return ops, text


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

def correspond(cops, aops):
    """The C's mutating ops must line up 1:1 and in order with the model's."""
    mutators = [o for o in aops if o in ("push_back", "push_front", "del",
                                         "move_tail", "move_front")]
    c_classes = [o["adt"] for o in cops]
    if len(mutators) != len(c_classes):
        raise Refused(f"op_count_mismatch:c={len(c_classes)},adt={len(mutators)}")
    for i, (a, c) in enumerate(zip(mutators, c_classes)):
        if a != c:
            raise Refused(f"op_class_mismatch@{i}:adt={a},c={c}")
    return list(zip(mutators, cops))


# ---------------------------------------------------------------------------
# 4. emission — Rust over the ListHead mirror, real pointers
# ---------------------------------------------------------------------------

def emit_realized(rel, fn, L, sabotage=None):
    cops, ctext = c_ops(rel, fn)
    aops, abody = adt_ops(rel, fn)
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
        elif rs in ("list_del", "list_del_init"):
            lines.append(f"        {rs}(entry);")
        else:                                # list_move / list_move_tail
            lines.append(f"        __list_del((*entry).prev, (*entry).next);")
            lines.append(f"        {'list_add' if rs=='list_move' else 'list_add_tail'}(entry, head);")
    body = "\n".join(lines)
    return f"""
#[no_mangle] pub extern "C" fn realized_op(entry: *mut ListHead, head: *mut ListHead) {{
    unsafe {{
{body}
    }}
}}
""", cops, aops


# ---------------------------------------------------------------------------
# 5. the gate — real C vs realized Rust, chain-walking
# ---------------------------------------------------------------------------

def _ref_c(cops, L):
    """Host C reference: the real ops, applied in the real order."""
    calls = []
    for o in cops:
        if o["c_op"] in ("list_add", "list_add_tail"):
            calls.append(f"    {o['c_op']}(entry, head);")
        elif o["c_op"] in ("list_del", "list_del_init"):
            calls.append(f"    {o['c_op']}(entry);")
        else:
            calls.append(f"    {o['c_op']}(entry, head);")
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


def _cand_rs(realized, L):
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
#[no_mangle] pub extern "C" fn r_call(a: i32) {{ unsafe {{
    realized_op(&raw mut R_ARENA[a as usize].lh, &raw mut R_HEAD);
}}}}
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
extern void c_reset(void); extern void c_call(int); extern int c_snapshot(long*);
extern void r_reset(void); extern void r_call(int); extern int r_snapshot(long*);
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
    for (int a = 0; a < NNODES; a++) { c_call(a); r_call(a); CMP("call"); }
    printf("CREALIZE verdict=MATCH calls=%d\\n", NNODES);
    return 0;
}
"""


def run_gate(rel, fn, L, sabotage=None, adt_only=False):
    realized, cops, aops = emit_realized(rel, fn, L, sabotage)
    d = tempfile.mkdtemp(prefix="crealize_")
    open(os.path.join(d, "ref.c"), "w").write(_ref_c(cops, L))
    open(os.path.join(d, "cand.rs"), "w").write(_cand_rs(realized, L))
    open(os.path.join(d, "probe.c"), "w").write(_PROBE)
    r = subprocess.run(["rustc", "--edition", "2021", "-O", "--crate-type=staticlib",
                        os.path.join(d, "cand.rs"), "-o", os.path.join(d, "libcand.a")],
                       capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL_RS", r.stderr[-900:], d
    r = subprocess.run(["cc", "-O2", "-w", f"-DNNODES={NN}",
                        f"-DADT_ONLY={1 if adt_only else 0}",
                        os.path.join(d, "probe.c"), os.path.join(d, "ref.c"),
                        os.path.join(d, "libcand.a"), "-o", os.path.join(d, "run")],
                       capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL_C", r.stderr[-900:], d
    r = subprocess.run([os.path.join(d, "run")], capture_output=True, text=True, timeout=60)
    out = (r.stdout + r.stderr).strip()
    m = re.search(r"verdict=(\w+)", out)
    return (m.group(1) if m else "UNKNOWN"), out, d


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
]


def cmd_map(rel, fn):
    cops, _ = c_ops(rel, fn)
    aops, _ = adt_ops(rel, fn)
    print(f"{rel}:{fn}")
    print(f"  C concrete ops : {[o['c_op'] for o in cops]}")
    print(f"  ADT model ops  : {aops}")
    print(f"  correspondence : {[(a, o['c_op']) for a, o in correspond(cops, aops)]}")
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
        cops, _ = c_ops(rel, fn)
        mark = "✓" if v == "MATCH" else "✗"
        print(f"  {mark} {fn:28s} {v:8s}  ops={[o['c_op'] for o in cops]}")
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
            print(f"  {'✓' if v == 'DIVERGE' else '✗ UNEXPECTED'} {sab:14s} -> {v}")
            ok &= (v == "DIVERGE")
    # the ADT-invisible class, on a del_init candidate if we have one
    di = [(r, f) for r, f, c in rows if any(o["c_op"] == "list_del_init" for o in c)]
    if di:
        rel, fn = di[0]
        full, _, _ = run_gate(rel, fn, L, sabotage="del_not_init")
        adt, _, _ = run_gate(rel, fn, L, sabotage="del_not_init", adt_only=True)
        print(f"\n  del_not_init on {fn}: structural={full}, ADT-only={adt}"
              f"  {'<-- ADT ORACLE BLIND' if adt == 'MATCH' and full == 'DIVERGE' else ''}")
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
