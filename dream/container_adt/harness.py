#!/usr/bin/env python3
"""Container-ADT harness — PRODUCTIZE the LIST-idiom ADT oracle over REAL kernel
functions (proof.py proved the mechanism on a synthetic subject; this runs the
same representation-independent differential against a function taken verbatim
from the tree, the way structdiff's prepare/close productized the mirror
differential and template_synth productized gpio_family).

prepare(rec)  — rec is a reach.py accepted record. Emits:
  * ref.c   : host cadt.h (faithful list.h subset + iteration macros +
              container_of), the REAL function text verbatim under `#line 1000`
              with every mutation op instrumented for OP-SITE COVERAGE, node
              arena + generated plumbing (setters / attach / seq-extractor /
              trampoline), lock brackets + lockdep as no-op macros, kfree as a
              logged arena retire.
  * surface : a generated Rust ADT model (lists = Vec<u32> id-sequences, node
              scalar fields as i64 tables, pointer fields as opaque u64 tokens,
              iter() = snapshot — exactly list_for_each_entry_safe semantics)
              plus named constants; the candidate writes ONLY the rs_call body
              against safe helpers.
  * meta    : deterministic workload tables (LCG-seeded, no runtime randomness).

close(prep, rust_body) — build both sides + probe, run the workload, and gate:
  * ADT state (every list's id-sequence) compared after EVERY call — strictly
    stronger than final-state-only;
  * per-call return values compared;
  * retire logs compared (order-sensitive);
  * COVERAGE: every static mutation site in the C function must have fired,
    else REFUSED_COVERAGE — an un-exercised site can never certify.
Verdicts: MATCH | DIVERGE:* | REFUSED_COVERAGE | BUILD_FAIL_* | TIMEOUT.

Soundness scope (flagged, not hidden): locks_stripped => the verdict is the
container-transition half (locking half = concgate composition);
alloc_stripped => leak/UAF half is the allocator model's claim. The ADT claim
itself is exact on the exercised workload.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
import sys                                # noqa: E402
for p in ("cluster", "widerun"):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import cluster                            # noqa: E402

KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")

MUT_OPS = ("list_add_tail", "list_add", "list_del_init", "list_del",
           "list_move_tail", "list_move")
LOCK_NOOPS = (
    "spin_lock", "spin_unlock", "spin_lock_irqsave", "spin_unlock_irqrestore",
    "spin_lock_irq", "spin_unlock_irq", "spin_lock_bh", "spin_unlock_bh",
    "raw_spin_lock", "raw_spin_unlock", "raw_spin_lock_irqsave",
    "raw_spin_unlock_irqrestore", "mutex_lock", "mutex_unlock",
    "lockdep_assert_held", "assert_spin_locked",
)

NN, NTOK, W, SEQCAP = 8, 3, 24, 64


class Unsupported(Exception):
    pass


def _lcg(seed=12345):
    s = seed
    while True:
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
        yield s


# ---------------------------------------------------------------------------
# host cadt.h — faithful list.h subset, instrumented; sites < line 1000 ignored
# ---------------------------------------------------------------------------

CADT_H = r"""
#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <sys/types.h>
/* kernel scalar typedefs (LP64), matching mirror.SCALAR's vocabulary */
typedef uint8_t  u8;  typedef int8_t  s8;  typedef uint8_t  __u8;  typedef int8_t  __s8;
typedef uint16_t u16; typedef int16_t s16; typedef uint16_t __u16; typedef int16_t __s16;
typedef uint32_t u32; typedef int32_t s32; typedef uint32_t __u32; typedef int32_t __s32;
typedef uint64_t u64; typedef int64_t s64; typedef uint64_t __u64; typedef int64_t __s64;
typedef uint16_t __le16; typedef uint16_t __be16;
typedef uint32_t __le32; typedef uint32_t __be32;
typedef uint64_t __le64; typedef uint64_t __be64;
typedef uint64_t phys_addr_t; typedef uint64_t dma_addr_t; typedef uint64_t resource_size_t;
/* asm-generic errno-base values, so `return -EINVAL` in the fn text and a
   numeric return in the candidate compare equal */
#define EPERM 1
#define ENOENT 2
#define EIO 5
#define EAGAIN 11
#define ENOMEM 12
#define EFAULT 14
#define EBUSY 16
#define EEXIST 17
#define ENODEV 19
#define EINVAL 22
#define ENOSPC 28
struct list_head { struct list_head *next, *prev; };
extern void cadt_site(int line);
static void __cadt_init(struct list_head *h){ h->next=h; h->prev=h; }
static void __cadt_add(struct list_head *n, struct list_head *p, struct list_head *x){
    x->prev=n; n->next=x; n->prev=p; p->next=n; }
static void __cadt_list_add(struct list_head *n, struct list_head *h){ __cadt_add(n,h,h->next); }
static void __cadt_list_add_tail(struct list_head *n, struct list_head *h){ __cadt_add(n,h->prev,h); }
static void __cadt_list_del(struct list_head *e){ e->prev->next=e->next; e->next->prev=e->prev; e->next=e->prev=0; }
static void __cadt_list_del_init(struct list_head *e){ e->prev->next=e->next; e->next->prev=e->prev; __cadt_init(e); }
static void __cadt_list_move(struct list_head *e, struct list_head *h){ __cadt_list_del(e); __cadt_list_add(e,h); }
static void __cadt_list_move_tail(struct list_head *e, struct list_head *h){ __cadt_list_del(e); __cadt_list_add_tail(e,h); }
static int  __cadt_list_empty(const struct list_head *h){ return h->next == h; }
#define INIT_LIST_HEAD(h) __cadt_init(h)
#define list_add(n,h)       (cadt_site(__LINE__), __cadt_list_add((n),(h)))
#define list_add_tail(n,h)  (cadt_site(__LINE__), __cadt_list_add_tail((n),(h)))
#define list_del(e)         (cadt_site(__LINE__), __cadt_list_del((e)))
#define list_del_init(e)    (cadt_site(__LINE__), __cadt_list_del_init((e)))
#define list_move(e,h)      (cadt_site(__LINE__), __cadt_list_move((e),(h)))
#define list_move_tail(e,h) (cadt_site(__LINE__), __cadt_list_move_tail((e),(h)))
#define list_empty(h)       __cadt_list_empty(h)
#define LIST_HEAD(name) struct list_head name = { &(name), &(name) }
#define cadt_container_of(p, T, m) ((T*)((char*)(p) - offsetof(T, m)))
#define list_entry(p, T, m) cadt_container_of(p, T, m)
#define list_for_each_entry(pos, head, member) \
    for (pos = list_entry((head)->next, __typeof__(*pos), member); \
         &pos->member != (head); \
         pos = list_entry(pos->member.next, __typeof__(*pos), member))
#define list_for_each_entry_reverse(pos, head, member) \
    for (pos = list_entry((head)->prev, __typeof__(*pos), member); \
         &pos->member != (head); \
         pos = list_entry(pos->member.prev, __typeof__(*pos), member))
#define list_for_each_entry_safe(pos, n, head, member) \
    for (pos = list_entry((head)->next, __typeof__(*pos), member), \
         n = list_entry(pos->member.next, __typeof__(*pos), member); \
         &pos->member != (head); \
         pos = n, n = list_entry(n->member.next, __typeof__(*pos), member))
#define likely(x) (x)
#define unlikely(x) (x)
#define __init
#define __exit
#define __maybe_unused
extern void cadt_retire(void *p);
#define kfree(p) cadt_retire((void*)(p))
""" + "\n".join(f"#define {lk}(...) (void)0" for lk in LOCK_NOOPS) + "\n"


def _fn_text(rec):
    src = open(os.path.join(KSRC, rec["file"]), errors="ignore").read()
    return cluster.functions(src)[rec["fn"]]["text"]


def _mut_sites(fn_text):
    """1000-based __LINE__ values of mutation-op call sites in the fn text
    (emitted right after `#line 1000`, so text line i => __LINE__ 999+i)."""
    sites = []
    for i, line in enumerate(fn_text.split("\n"), start=1):
        if any(re.search(rf"\b{op}\s*\(", line) for op in MUT_OPS):
            sites.append(999 + i)
    return sites


def prepare(rec):
    """Build the full harness context from a reach.py accepted record."""
    # ---- v2 harness restrictions (tallied by the measurement, not hidden) ----
    node_types = {}
    for p in rec["params"]:
        if p["kind"] == "node":
            node_types[p["struct"]] = (p["lh_fields"], p["scalar_fields"],
                                       p.get("token_fields", []))
    for c in rec["cursors"].values():
        node_types[c["struct"]] = (c["lh_fields"], c["scalar_fields"],
                                   c.get("token_fields", []))
    if len(node_types) != 1:
        raise Unsupported(f"node types: {len(node_types)}")
    (ntype, (lhf, scalars, tokf)), = node_types.items()
    if rec["local_lists"]:
        raise Unsupported("local LIST_HEAD")
    tok_types = {p["struct"] for p in rec["params"] if p["kind"] == "token"}
    if len(tok_types) > 1:
        raise Unsupported("multiple token param types")
    tok_type = next(iter(tok_types), None)
    tok_reads = sorted({f for fs in rec["token_reads"].values() for f in fs})
    for it in rec["iters"]:
        if it["anchor"][0] == "field":
            raise Unsupported("node-field head anchor")
        if it["member"] not in lhf:
            raise Unsupported(f"iter member {it['member']} not an lh field")

    fn_text = _fn_text(rec)
    sites = _mut_sites(fn_text)
    if not sites:
        raise Unsupported("no static mutation site")

    # ---- lh MEMBERS: each is a membership universe ------------------------
    # entry-use = node linked THROUGH that member; head-only INIT = a per-node
    # sub-anchor (no-op here, flagged); list_empty on a sub-anchor is state we
    # do not model -> refuse.
    members = list(lhf)
    m_ix = {f: i for i, f in enumerate(members)}
    node_vars = ({p["name"] for p in rec["params"] if p["kind"] == "node"}
                 | set(rec["cursors"]))
    entry_used, init_used = set(), set()
    anchor_member = {}          # list name -> member field

    def _assoc(anchor_name, mem):
        if anchor_member.setdefault(anchor_name, mem) != mem:
            raise Unsupported(f"anchor {anchor_name}: conflicting members")

    for op in MUT_OPS:
        for m in re.finditer(
                rf"\b{op}\s*\(\s*&\s*(\w+)\s*->\s*(\w+)\s*(?:,\s*([^()]+?))?\s*\)",
                fn_text):
            var, fld, headx = m.group(1), m.group(2), m.group(3)
            if var not in node_vars or fld not in m_ix:
                continue
            entry_used.add(fld)
            if headx:
                gm = re.match(r"&\s*(\w+)$", headx.strip())
                if gm and gm.group(1) in rec["globals"]:
                    _assoc(gm.group(1), fld)
                elif re.match(r"^\w+$", headx.strip()):
                    _assoc(headx.strip(), fld)
    for it in rec["iters"]:
        entry_used.add(it["member"])            # iteration walks via the member
        _assoc(it["anchor"][1], it["member"])
    for m in re.finditer(r"\bINIT_LIST_HEAD\s*\(\s*&\s*(\w+)\s*->\s*(\w+)\s*\)", fn_text):
        if m.group(1) in node_vars and m.group(2) in m_ix:
            init_used.add(m.group(2))
    for m in re.finditer(r"\blist_empty\s*\(\s*&\s*(\w+)\s*->\s*(\w+)\s*\)", fn_text):
        if m.group(1) in node_vars and m.group(2) in m_ix and m.group(2) not in entry_used:
            raise Unsupported(f"list_empty on sub-anchor {m.group(2)}")
    subanchors = sorted(init_used - entry_used)
    if not entry_used:
        raise Unsupported("no membership member used")

    # ---- lists inventory: named (member-associated) + synthetic ------------
    # a used member with no named anchor gets a synthetic "elsewhere" list, so
    # anchor-less dels (list_del(&p->m) under an unnamed caller-side list) are
    # workload-linkable and their chains extracted/compared like any other.
    lists = [("global", g) for g in rec["globals"]]
    for p in rec["params"]:
        if p["kind"] == "lh" and p.get("role") != "entry":
            lists.append(("param", p["name"]))
    only_m = next(iter(entry_used)) if len(entry_used) == 1 else None
    list_member = []
    for kind, name in lists:
        mem = anchor_member.get(name, only_m)
        if mem is None:
            raise Unsupported(f"anchor {name}: member unknown")
        list_member.append(mem)
    for mem in sorted(entry_used):
        if mem not in list_member:
            lists.append(("synth", f"__elsewhere_{mem}"))
            list_member.append(mem)
    if len(lists) > 5:
        raise Unsupported("too many lists")
    n_named = sum(1 for k, _ in lists if k != "synth")

    # node params: fresh (add-only) vs linked (del/move present)
    fresh = set()
    for p in rec["params"]:
        if p["kind"] != "node":
            continue
        uses = [op for op in MUT_OPS
                if re.search(rf"\b{op}\s*\(\s*&\s*{p['name']}\s*->", fn_text)]
        if uses and all(u.startswith("list_add") for u in uses):
            fresh.add(p["name"])

    fields = list(scalars)              # scalar field order
    sf_ix = {f: i for i, f in enumerate(fields)}
    tf_ix = {f: i for i, f in enumerate(tokf)}
    tp_ix = {f: i for i, f in enumerate(tok_reads)}

    # ---- C reference TU ----------------------------------------------------
    c = [CADT_H]
    c.append(f"struct {ntype} {{")
    for f in members:
        c.append(f"    struct list_head {f};")
    for f, t in scalars.items():
        c.append(f"    {t} {f};")
    for f in tokf:
        c.append(f"    void *{f};")
    c.append("};")
    if tok_type:
        c.append(f"struct {tok_type} {{ long "
                 + (", ".join(tok_reads) if tok_reads else "__cadt_pad") + "; };")
        c.append(f"static struct {tok_type} CADT_TOKS[{NTOK}];")
    for _, g in [x for x in lists if x[0] == "global"]:
        c.append(f"static struct list_head {g};")
    npl = sum(1 for k, _ in lists if k == "param")
    if npl:
        c.append(f"static struct list_head CADT_PL[{npl}];")
    nsyn = sum(1 for k, _ in lists if k == "synth")
    if nsyn:
        c.append(f"static struct list_head CADT_SL[{nsyn}];")
    pl_seen, sl_seen = [0], [0]
    anchors = []
    for kind, name in lists:
        if kind == "global":
            anchors.append(f"&{name}")
        elif kind == "param":
            anchors.append(f"&CADT_PL[{pl_seen[0]}]")
            pl_seen[0] += 1
        else:
            anchors.append(f"&CADT_SL[{sl_seen[0]}]")
            sl_seen[0] += 1
    c.append(f"static struct list_head *CADT_LISTS[{len(lists)}] = "
             "{ " + ", ".join(anchors) + " };")
    # per-list membership-member offset: attach/extract walk THROUGH the member
    c.append(f"static const size_t CADT_MOFF[{len(lists)}] = {{ "
             + ", ".join(f"offsetof(struct {ntype}, {m})" for m in list_member)
             + " };")
    c.append(f"static struct {ntype} CADT_ARENA[{NN}];")
    c.append(f"""
static int CADT_SITES[256]; static int CADT_NS;
void cadt_site(int line){{
    if (line < 1000) return;
    for (int i = 0; i < CADT_NS; i++) if (CADT_SITES[i] == line) return;
    if (CADT_NS < 256) CADT_SITES[CADT_NS++] = line;
}}
static int CADT_RETLOG[256]; static int CADT_NRET;
void cadt_retire(void *p){{
    int id = (int)((struct {ntype}*)p - CADT_ARENA);
    if (CADT_NRET < 256) CADT_RETLOG[CADT_NRET++] = id;
}}
void cadt_reset(void){{
    CADT_NS = 0; CADT_NRET = 0;
    for (int i = 0; i < {len(lists)}; i++) __cadt_init(CADT_LISTS[i]);
    for (int i = 0; i < {NN}; i++) {{
        __builtin_memset(&CADT_ARENA[i], 0, sizeof(CADT_ARENA[i]));
        {" ".join(f"__cadt_init(&CADT_ARENA[i].{m});" for m in members)}
    }}
}}
static struct list_head *__cadt_link(int id, int l){{
    return (struct list_head *)((char *)&CADT_ARENA[id] + CADT_MOFF[l]);
}}
void cadt_attach(int id, int l){{ __cadt_list_add_tail(__cadt_link(id, l), CADT_LISTS[l]); }}
int cadt_seq(int l, int *buf, int cap){{
    int n = 0; struct list_head *p;
    for (p = CADT_LISTS[l]->next; p != CADT_LISTS[l] && n < cap; p = p->next)
        buf[n++] = (int)((struct {ntype} *)((char *)p - CADT_MOFF[l]) - CADT_ARENA);
    return n;
}}
int cadt_sites(int *buf, int cap){{
    int n = CADT_NS < cap ? CADT_NS : cap;
    for (int i = 0; i < n; i++) buf[i] = CADT_SITES[i];
    return n;
}}
int cadt_retlog(int *buf, int cap){{
    int n = CADT_NRET < cap ? CADT_NRET : cap;
    for (int i = 0; i < n; i++) buf[i] = CADT_RETLOG[i];
    return n;
}}""")
    if fields:
        c.append("void cadt_setf(int id, int f, long v){ switch (f) {")
        for f, i in sf_ix.items():
            c.append(f"    case {i}: CADT_ARENA[id].{f} = ({scalars[f]})v; break;")
        c.append("} }")
    if tokf:
        c.append("void cadt_settok(int id, int t, long h){ void *p = "
                 + ("h ? (void*)&CADT_TOKS[h-1] : 0" if tok_type else "(void*)(long)h")
                 + "; switch (t) {")
        for f, i in tf_ix.items():
            c.append(f"    case {i}: CADT_ARENA[id].{f} = p; break;")
        c.append("} }")
    if tok_type and tok_reads:
        c.append("void cadt_tokset(int h, int f, long v){ switch (f) {")
        for f, i in tp_ix.items():
            c.append(f"    case {i}: CADT_TOKS[h-1].{f} = v; break;")
        c.append("} }")

    c.append('#line 1000 "fnsrc"')
    c.append(fn_text)

    # trampoline: map abstract args -> real args
    tramp_args, call_args = [], []
    for i, p in enumerate(rec["params"]):
        a = f"a{i}"
        tramp_args.append(f"long {a}")     # uniform ABI with probe's externs
        if p["kind"] == "node":
            call_args.append(f"&CADT_ARENA[{a}]")
        elif p["kind"] == "lh":
            if p.get("role") == "entry":
                if len(members) > 1:
                    raise Unsupported("entry-role lh param on multi-member node")
                call_args.append(f"&CADT_ARENA[{a}].{members[0]}")
            else:
                call_args.append(f"CADT_LISTS[{a}]")
        elif p["kind"] == "token":
            call_args.append(f"({a} ? &CADT_TOKS[{a}-1] : 0)")
        else:
            call_args.append(a)
    callexpr = f"{rec['fn']}({', '.join(call_args)})"
    if rec["ret"] == "void":
        c.append(f"long cadt_call({', '.join(tramp_args) or 'void'})"
                 f"{{ {callexpr}; return 0; }}")
    else:
        c.append(f"long cadt_call({', '.join(tramp_args) or 'void'})"
                 f"{{ return (long){callexpr}; }}")
    csrc = "\n".join(c) + "\n"

    # ---- Rust ADT surface --------------------------------------------------
    nf, nt, ntf, nl = len(fields), len(tokf), max(len(tok_reads), 1), len(lists)
    consts = []
    for f, i in sf_ix.items():
        consts.append(f"const F_{f.upper()}: usize = {i};")
    for f, i in tf_ix.items():
        consts.append(f"const T_{f.upper()}: usize = {i};")
    for f, i in tp_ix.items():
        consts.append(f"const P_{f.upper()}: usize = {i};")
    for i, (kind, name) in enumerate(lists):
        if kind != "synth":
            consts.append(f"const L_{name.upper()}: usize = {i};")
    multi = len(members) > 1
    if multi:
        for f, i in m_ix.items():
            consts.append(f"const M_{f.upper()}: usize = {i};")
    lm_row = ", ".join(str(m_ix[m]) for m in list_member)
    del_fns = (f"""const LM: [usize; NL] = [{lm_row}];   // list -> membership member
fn del_m(m: usize, id: u32) {{ unsafe {{
    for (l, v) in LISTS.iter_mut().enumerate() {{
        if LM[l] == m {{ if let Some(p) = v.iter().position(|&x| x == id) {{ v.remove(p); }} }}
    }}
}}}}""" if multi else
               "fn del(id: u32) { unsafe { for v in LISTS.iter_mut() { if let Some(p) = v.iter().position(|&x| x == id) { v.remove(p); } } } }")
    del_call = "del_m(LM[l], id)" if multi else "del(id)"
    surface = f"""#![allow(non_snake_case, dead_code, static_mut_refs, unused_unsafe, unused_imports, unused_variables)]
// generated ADT surface — lists are id-sequences; nodes are field tables.
const NL: usize = {nl};
const NF: usize = {max(nf, 1)};
const NT: usize = {max(nt, 1)};
const NTF: usize = {ntf};
const NN: usize = {NN};
{chr(10).join(consts)}
static mut LISTS: Vec<Vec<u32>> = Vec::new();
static mut SF: Vec<[i64; NF]> = Vec::new();
static mut TF: Vec<[i64; NT]> = Vec::new();
static mut TOKS: Vec<[i64; NTF]> = Vec::new();
static mut RETIRED: Vec<u32> = Vec::new();
#[no_mangle] pub extern "C" fn rs_reset() {{ unsafe {{
    LISTS = (0..NL).map(|_| Vec::new()).collect();
    SF = vec![[0; NF]; NN]; TF = vec![[0; NT]; NN];
    TOKS = vec![[0; NTF]; {NTOK}]; RETIRED = Vec::new();
}}}}
#[no_mangle] pub extern "C" fn rs_attach(id: i32, l: i32) {{ unsafe {{ LISTS[l as usize].push(id as u32); }}}}
#[no_mangle] pub extern "C" fn rs_setf(id: i32, f: i32, v: i64) {{ unsafe {{ SF[id as usize][f as usize] = v; }}}}
#[no_mangle] pub extern "C" fn rs_settok(id: i32, t: i32, h: i64) {{ unsafe {{ TF[id as usize][t as usize] = h; }}}}
#[no_mangle] pub extern "C" fn rs_tokset(h: i32, f: i32, v: i64) {{ unsafe {{ TOKS[(h - 1) as usize][f as usize] = v; }}}}
#[no_mangle] pub extern "C" fn rs_seq(l: i32, buf: *mut i32, cap: i32) -> i32 {{ unsafe {{
    let v = &LISTS[l as usize];
    let n = core::cmp::min(v.len(), cap as usize);
    for i in 0..n {{ *buf.add(i) = v[i] as i32; }}
    n as i32
}}}}
#[no_mangle] pub extern "C" fn rs_retlog(buf: *mut i32, cap: i32) -> i32 {{ unsafe {{
    let n = core::cmp::min(RETIRED.len(), cap as usize);
    for i in 0..n {{ *buf.add(i) = RETIRED[i] as i32; }}
    n as i32
}}}}
// ---- candidate-facing helpers (the RfL List surface, ADT-modeled) ----
fn iter(l: usize) -> Vec<u32> {{ unsafe {{ LISTS[l].clone() }} }}      // snapshot == _safe semantics
fn empty(l: usize) -> bool {{ unsafe {{ LISTS[l].is_empty() }} }}
{del_fns}
fn push_back(l: usize, id: u32) {{ unsafe {{ LISTS[l].push(id); }} }}
fn push_front(l: usize, id: u32) {{ unsafe {{ LISTS[l].insert(0, id); }} }}
fn move_tail(l: usize, id: u32) {{ {del_call}; push_back(l, id); }}
fn move_front(l: usize, id: u32) {{ {del_call}; push_front(l, id); }}
fn field(id: u32, f: usize) -> i64 {{ unsafe {{ SF[id as usize][f] }} }}
fn set_field(id: u32, f: usize, v: i64) {{ unsafe {{ SF[id as usize][f] = v; }} }}
fn tokf(id: u32, t: usize) -> i64 {{ unsafe {{ TF[id as usize][t] }} }}
fn tok_field(h: i64, f: usize) -> i64 {{ unsafe {{ if h == 0 {{ 0 }} else {{ TOKS[(h - 1) as usize][f] }} }} }}
fn retire(id: u32) {{ unsafe {{ RETIRED.push(id); }} }}
"""
    rs_args = [f"a{i}: i64" for i in range(len(rec["params"]))]
    rs_sig = f'#[no_mangle] pub extern "C" fn rs_call({", ".join(rs_args)}) -> i64'

    # ---- deterministic workload tables ------------------------------------
    g = _lcg()
    fresh_ids = list(range(NN - 4, NN)) if fresh else []
    linked_ids = [i for i in range(NN) if i not in fresh_ids]
    # one attach per node per membership universe (a node sits on <=1 list of
    # each member); pool draw == old `% nl` stream when there is one member.
    pools = {}
    for l, mem in enumerate(list_member):
        pools.setdefault(mem, []).append(l)
    setup = {
        "attach": [(i, pools[mem][next(g) % len(pools[mem])])
                   for i in linked_ids for mem in sorted(pools)],
        "setf": [(i, fi, [0, 1, 2, 7, -1, 3][next(g) % 6])
                 for i in range(NN) for fi in range(nf)],
        # even ids: all token fields non-null (guards that require a fully-
        # populated node can pass -> their mutation sites get exercised);
        # odd ids: may carry nulls (the guard's reject path gets exercised too)
        "settok": [(i, ti, (1 + next(g) % NTOK) if i % 2 == 0
                   else next(g) % (NTOK + 1))
                   for i in range(NN) for ti in range(nt)],
        "tokset": [(h, fi, next(g) % 4)
                   for h in range(1, NTOK + 1) for fi in range(len(tok_reads))],
    }
    # node params the fn UNLINKS (plain list_del / move): passing an already-
    # deleted node violates the caller contract (NULL-deref in the C ref, a
    # crash — not a differential), so their args draw WITHOUT replacement.
    # list_del_init re-inits (re-del safe) but distinct args cover it uniformly.
    consuming = set()
    for p in rec["params"]:
        if p["kind"] == "node" and p["name"] not in fresh:
            uses = [op for op in MUT_OPS
                    if re.search(rf"\b{op}\s*\(\s*&\s*{p['name']}\s*->", fn_text)]
            if any(not u.startswith("list_add") for u in uses):
                consuming.add(p["name"])
        elif p["kind"] == "lh" and p.get("role") == "entry":
            consuming.add(p["name"])
    ncalls = min(W, len(fresh_ids)) if fresh else W
    if consuming:
        ncalls = min(ncalls, len(linked_ids) // len(consuming))
    fresh_pool = iter(fresh_ids)
    consume_pool = list(linked_ids)
    calls = []
    for _ in range(ncalls):
        row = []
        for p in rec["params"]:
            if p["kind"] == "node":
                row.append(next(fresh_pool) if p["name"] in fresh
                           else consume_pool.pop(next(g) % len(consume_pool))
                           if p["name"] in consuming
                           else linked_ids[next(g) % len(linked_ids)])
            elif p["kind"] == "lh":
                if p.get("role") == "entry":
                    row.append(consume_pool.pop(next(g) % len(consume_pool))
                               if p["name"] in consuming
                               else linked_ids[next(g) % len(linked_ids)])
                else:
                    # only lists of the SAME membership universe as this param
                    # (a wrong-member chain walk is UB, not a differential)
                    pmem = anchor_member.get(p["name"], only_m)
                    pool = [l for l, mm in enumerate(list_member) if mm == pmem]
                    row.append(pool[next(g) % len(pool)])
            elif p["kind"] == "token":
                row.append(1 + next(g) % NTOK)
            else:
                row.append([0, 1, 2, 7, -1, 3, 8, 5][next(g) % 8])
        calls.append(row)

    member_doc = ""
    if multi:
        member_doc = ("\n// MULTI-MEMBERSHIP node: fields "
                      + ", ".join(f"{m} (M_{m.upper()})" for m in members
                                  if m in entry_used)
                      + " are separate list memberships."
                      + "\n// del_m(M_X, id) == list_del(&node->X); plain del() does NOT exist here."
                      + "\n// List membership members: "
                      + ", ".join(f"L index {l} via {m}"
                                  for l, m in enumerate(list_member)))
    if subanchors:
        member_doc += ("\n// Sub-anchor fields "
                       + ", ".join(subanchors)
                       + ": per-node list heads never populated here — treat"
                         " INIT_LIST_HEAD(&node->X) on them as a no-op.")
    doc = (f"// C function under translation (from {rec['file']}):\n"
           + "\n".join("// " + ln for ln in fn_text.split("\n"))
           + "\n// Available constants:\n"
           + "\n".join("//   " + c for c in consts)
           + member_doc
           + "\n// Node ids are abstract; lists are ordered id-sequences."
           + "\n// Nodes may start linked on an unnamed caller-side list; "
           + ("del_m removes from wherever the member is linked."
              if multi else "del(id) removes from wherever the node is linked.")
           + "\n// Helpers: iter(l)->Vec<u32> (snapshot), empty(l), "
           + ("del_m(M_*,id)," if multi else "del(id),")
           + "\n//   push_back/push_front(l,id) [list_add_tail/list_add],"
           + "\n//   move_tail/move_front(l,id), field(id,F_*), set_field,"
           + "\n//   tokf(id,T_*)->i64 token, tok_field(h,P_*)->i64, retire(id) [kfree]."
           + "\n// Args: " + ", ".join(
               f"a{i}={p['name']}({p['kind']})" for i, p in enumerate(rec["params"])))

    return {
        "rec": rec, "csrc": csrc, "surface": surface, "rs_sig": rs_sig,
        "doc": doc, "sites": sites, "lists": lists, "setup": setup,
        "calls": calls, "nparams": len(rec["params"]),
        "flags": rec["flags"], "ntype": ntype,
        "members": members, "list_member": list_member,
        "subanchors": subanchors, "n_named": n_named,
    }


def _probe_c(prep):
    nl = len(prep["lists"])
    su = prep["setup"]
    rows = prep["calls"]
    npar = prep["nparams"]
    argdecl = ", ".join(["long"] * npar) if npar else "void"
    lines = [
        "#include <stdio.h>",
        "extern void cadt_reset(void); extern void rs_reset(void);",
        "extern void cadt_attach(int,int); extern void rs_attach(int,int);",
        "extern int cadt_seq(int,int*,int); extern int rs_seq(int,int*,int);",
        "extern int cadt_sites(int*,int); extern int cadt_retlog(int*,int); extern int rs_retlog(int*,int);",
        f"extern long cadt_call({argdecl}); extern long rs_call({argdecl});",
    ]
    if su["setf"]:
        lines.append("extern void cadt_setf(int,int,long); extern void rs_setf(int,int,long);")
    if su["settok"]:
        lines.append("extern void cadt_settok(int,int,long); extern void rs_settok(int,int,long);")
    if su["tokset"]:
        lines.append("extern void cadt_tokset(int,int,long); extern void rs_tokset(int,int,long);")
    want = prep["sites"]
    lines.append(f"static const int WANT[{len(want)}] = {{ {', '.join(map(str, want))} }};")
    ncalls = len(rows)
    if ncalls:
        for j in range(npar):
            col = ", ".join(str(r[j]) for r in rows)
            lines.append(f"static const long A{j}[{ncalls}] = {{ {col} }};")
    lines.append("""
int main(void) {
    cadt_reset(); rs_reset();""")
    for i, fi, v in su["setf"]:
        lines.append(f"    cadt_setf({i},{fi},{v}); rs_setf({i},{fi},{v});")
    for i, ti, h in su["settok"]:
        lines.append(f"    cadt_settok({i},{ti},{h}); rs_settok({i},{ti},{h});")
    for h, fi, v in su["tokset"]:
        lines.append(f"    cadt_tokset({h},{fi},{v}); rs_tokset({h},{fi},{v});")
    for i, l in su["attach"]:
        lines.append(f"    cadt_attach({i},{l}); rs_attach({i},{l});")
    args = ", ".join(f"A{j}[k]" for j in range(npar))
    lines.append(f"""
    int cb[{SEQCAP}], rb[{SEQCAP}];
    for (int k = 0; k < {ncalls}; k++) {{
        long rc = cadt_call({args});
        long rr = rs_call({args});
        if (rc != rr) {{
            printf("CADT_HARNESS verdict=DIVERGE:ret call=%d c=%ld r=%ld\\n", k, rc, rr);
            return 1;
        }}
        for (int l = 0; l < {nl}; l++) {{
            int cn = cadt_seq(l, cb, {SEQCAP});
            int rn = rs_seq(l, rb, {SEQCAP});
            int eq = (cn == rn);
            for (int j = 0; eq && j < cn; j++) if (cb[j] != rb[j]) eq = 0;
            if (!eq) {{
                printf("CADT_HARNESS verdict=DIVERGE:adt call=%d list=%d cn=%d rn=%d\\n", k, l, cn, rn);
                return 1;
            }}
        }}
    }}
    int cn = cadt_retlog(cb, {SEQCAP}), rn = rs_retlog(rb, {SEQCAP});
    int eq = (cn == rn);
    for (int j = 0; eq && j < cn; j++) if (cb[j] != rb[j]) eq = 0;
    if (!eq) {{ printf("CADT_HARNESS verdict=DIVERGE:retire cn=%d rn=%d\\n", cn, rn); return 1; }}
    int got[256]; int ng = cadt_sites(got, 256);
    for (unsigned i = 0; i < {len(want)}; i++) {{
        int hit = 0;
        for (int j = 0; j < ng; j++) if (got[j] == WANT[i]) hit = 1;
        if (!hit) {{
            printf("CADT_HARNESS verdict=REFUSED_COVERAGE site=%d\\n", WANT[i]);
            return 2;
        }}
    }}
    printf("CADT_HARNESS verdict=MATCH calls={ncalls} retired=%d\\n", cn);
    return 0;
}}""")
    return "\n".join(lines) + "\n"


def _run(cmd, timeout, cwd=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        return None


def close(prep, rust_body, workdir=None):
    """Build + run the differential. Returns {'verdict': ..., 'out': ...}."""
    d = workdir or tempfile.mkdtemp(prefix="cadt_")
    open(os.path.join(d, "ref.c"), "w").write(prep["csrc"])
    open(os.path.join(d, "cand.rs"), "w").write(
        prep["surface"] + "\n" + prep["rs_sig"] + " {\n" + rust_body + "\n}\n")
    open(os.path.join(d, "probe.c"), "w").write(_probe_c(prep))
    r = _run(["rustc", "--edition", "2021", "-O", "--crate-type=staticlib",
              os.path.join(d, "cand.rs"), "-o", os.path.join(d, "libcand.a")], 90)
    if r is None:
        return {"verdict": "TIMEOUT:rustc", "out": "", "dir": d}
    if r.returncode:
        return {"verdict": "BUILD_FAIL_RS", "out": r.stderr[-2000:], "dir": d}
    r = _run(["cc", "-O2", "-w", os.path.join(d, "probe.c"), os.path.join(d, "ref.c"),
              os.path.join(d, "libcand.a"), "-o", os.path.join(d, "run")], 90)
    if r is None:
        return {"verdict": "TIMEOUT:cc", "out": "", "dir": d}
    if r.returncode:
        return {"verdict": "BUILD_FAIL_C", "out": r.stderr[-2000:], "dir": d}
    r = _run([os.path.join(d, "run")], 30)
    if r is None:
        return {"verdict": "TIMEOUT:run", "out": "", "dir": d}
    out = (r.stdout + r.stderr).strip()
    m = re.search(r"verdict=([A-Z_]+(?::[a-z]+)?)", out)
    return {"verdict": m.group(1) if m else f"UNKNOWN(rc={r.returncode})",
            "out": out, "dir": d}


# ---------------------------------------------------------------------------
# self-check: the canonical destroy-pattern fn, hand candidates, zero-false-pass
# ---------------------------------------------------------------------------

_CANON = ("lib/error-inject.c", "module_unload_ei_list")

_CANON_BODIES = {
    # faithful translation -> MATCH
    "correct": """
    if tok_field(a0, P_NUM_EI_FUNCS) == 0 { return 0; }
    for id in iter(L_ERROR_INJECTION_LIST) {
        if tokf(id, T_PRIV) == a0 { del(id); retire(id); }
    }
    0
""",
    # forgets the module filter (frees everything) -> DIVERGE
    "no_filter": """
    if tok_field(a0, P_NUM_EI_FUNCS) == 0 { return 0; }
    for id in iter(L_ERROR_INJECTION_LIST) { del(id); retire(id); }
    0
""",
    # drops the early-exit guard -> DIVERGE (mutates when C returns untouched)
    "no_guard": """
    for id in iter(L_ERROR_INJECTION_LIST) {
        if tokf(id, T_PRIV) == a0 { del(id); retire(id); }
    }
    0
""",
    # deletes but forgets to retire -> DIVERGE:retire (the alloc log catches it)
    "no_retire": """
    if tok_field(a0, P_NUM_EI_FUNCS) == 0 { return 0; }
    for id in iter(L_ERROR_INJECTION_LIST) {
        if tokf(id, T_PRIV) == a0 { del(id); }
    }
    0
""",
}


def main():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "cadt_reach_hn", os.path.join(HERE, "reach.py"))
    reach = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reach)
    rec = reach.gate(*_CANON)
    prep = prepare(rec)
    print(f"=== container-ADT harness self-check: {rec['fn']} ({rec['file']}) ===")
    print(f"  lists={[n for _, n in prep['lists']]} sites={prep['sites']} "
          f"flags={prep['flags']}")
    expect = {"correct": "MATCH", "no_filter": "DIVERGE",
              "no_guard": "DIVERGE", "no_retire": "DIVERGE"}
    ok = True
    for name, body in _CANON_BODIES.items():
        r = close(prep, body)
        good = r["verdict"].startswith(expect[name])
        ok &= good
        mark = "✓" if good else "✗ UNEXPECTED"
        print(f"  {mark}  {name:10s} -> {r['verdict']}   [{r['out'][:72]}]")
        if not good:
            print(f"      dir={r['dir']}")
    # negative control on the COVERAGE gate: a workload that never satisfies the
    # guard (all token fields 0) leaves the del site un-exercised — the correct
    # body must NOT certify: REFUSED_COVERAGE, never MATCH.
    import copy
    starved = copy.deepcopy(prep)
    starved["setup"]["tokset"] = [(h, fi, 0) for h, fi, _ in prep["setup"]["tokset"]]
    r = close(starved, _CANON_BODIES["correct"])
    good = r["verdict"] == "REFUSED_COVERAGE"
    ok &= good
    mark = "✓" if good else "✗ UNEXPECTED"
    print(f"  {mark}  starved-coverage(correct body) -> {r['verdict']}   "
          f"(an un-exercised mutation site can never certify)")
    print("PRODUCTIZED ORACLE:", "PASS — real kernel fn, verbatim C vs ADT-model "
          "Rust, sabotages caught" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
