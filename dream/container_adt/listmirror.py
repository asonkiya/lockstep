#!/usr/bin/env python3
"""The `list_head` mirror + faithful intrusive-list ops + a CHAIN-WALKING gate.

Step 2 of CONTAINERS-FEASIBILITY.md. The container ADT oracle compares abstract
*sequences*; a realized container function manipulates a REAL intrusive list, so
its oracle must compare the real pointer structure. This module supplies the
three pieces that need:

  1. **The mirror** — `#[repr(C)] ListHead { next, prev }` whose pointer fields
     are LOAD-BEARING (the existing mirror generator treats pointers as opaque
     blobs; here they are the thing under test), with layout probed in-kernel.
  2. **Faithful ops** — `__list_add`, `list_add`, `list_add_tail`, `__list_del`,
     `list_del` (WITH poisoning), `list_del_init`, `list_empty`, transcribed
     from the real `include/linux/list.h` write-for-write and in the SAME ORDER
     (the kernel writes `next->prev`, `new->next`, `new->prev`, then
     `prev->next`).
  3. **The structural differential** — the real C inlines and the Rust mirror
     ops each drive their own arena through an identical op script; after EVERY
     op we compare the FORWARD chain, the BACKWARD chain, and every node's raw
     next/prev.

MEASURED (not assumed) — `run_diff(..., adt_only=True)` restricts the oracle to
the forward chain, i.e. exactly what a membership/order (ADT) oracle sees:

     variant          ADT-only     structural
     correct          MATCH        MATCH
     forward_only     DIVERGE      DIVERGE
     no_poison        MATCH        DIVERGE   <-- ADT oracle is BLIND
     add_wrong_side   DIVERGE      DIVERGE

So the structural oracle is **strictly stronger**, and the class it uniquely
catches is *unlink-without-poison* — a translation whose list membership and
order are perfect but whose removed nodes carry the wrong state. Note the
honest correction to the original expectation: `forward_only` (prev-pointers
not fixed) is ALSO caught by the ADT view in this op script, because a later
`list_add_tail` reads `head->prev`, so backward-chain corruption propagates
into forward order. Chain corruption tends to become observable once any op
reads `prev`; the poison class does not.

Pointer values are normalized to arena indices (head = -1, LIST_POISON1/2 =
-100/-101, anything else = -999), so the comparison is address-independent but
structure-faithful.

Config-dependence is real and probed, not assumed: `POISON_POINTER_DELTA` comes
from the volume's `.config` (arm64 defconfig sets
`CONFIG_ILLEGAL_POINTER_VALUE=0xdead000000000000`, so LIST_POISON1 is
`0xdead000000000100`, NOT `0x100`).

  listmirror.py probe     # print the probed layout + poison values
  listmirror.py emit      # print the Rust mirror + ops
  listmirror.py prove     # run the chain-walking differential + negative controls
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
VOL = os.environ.get("WEAVE_VOL", "cgir-kbuild-defconfig")
IMG = "cgir-kernel-gate"

NN = 8              # arena nodes


# ---------------------------------------------------------------------------
# layout probe (in-kernel: the kernel's own compiler reports its own layout)
# ---------------------------------------------------------------------------

def probe_layout():
    """sizeof/offsetof for struct list_head + the config's poison delta."""
    probe = r"""
#include <linux/list.h>
#include <linux/poison.h>
char cgir_lh_size[sizeof(struct list_head)];
char cgir_lh_next[__builtin_offsetof(struct list_head, next) + 1];
char cgir_lh_prev[__builtin_offsetof(struct list_head, prev) + 1];
struct cgir_probe_node { int id; struct list_head lh; long payload; };
char cgir_node_size[sizeof(struct cgir_probe_node)];
char cgir_node_lh[__builtin_offsetof(struct cgir_probe_node, lh) + 1];
"""
    d = tempfile.mkdtemp(prefix="lhprobe_")
    open(os.path.join(d, "cgir_lh_probe.c"), "w").write(probe)
    r = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{VOL}:/build", "-v", f"{d}:/w:ro", IMG, "bash", "-c",
         "cp /w/cgir_lh_probe.c /build/linux/lib/ && cd /build/linux && "
         "rm -f lib/cgir_lh_probe.o && make -s lib/cgir_lh_probe.o 2>&1 | tail -3 ; "
         "nm -S lib/cgir_lh_probe.o 2>/dev/null | grep cgir_ ; "
         "grep -E '^CONFIG_ILLEGAL_POINTER_VALUE' .config || echo 'CONFIG_ILLEGAL_POINTER_VALUE=0' ; "
         "rm -f lib/cgir_lh_probe.c lib/cgir_lh_probe.o"],
        capture_output=True, text=True)
    out = {}
    for m in re.finditer(r"([0-9a-f]+)\s+([0-9a-f]+)\s+\w\s+(cgir_\w+)", r.stdout):
        out[m.group(3)] = int(m.group(2), 16)
    dm = re.search(r"CONFIG_ILLEGAL_POINTER_VALUE=(0x[0-9a-fA-F]+|\d+)", r.stdout)
    delta = int(dm.group(1), 0) if dm else 0
    if "cgir_lh_size" not in out:
        raise SystemExit(f"list_head probe failed:\n{r.stdout[-800:]}")
    return {
        "list_head_size": out["cgir_lh_size"],
        "next_off": out["cgir_lh_next"] - 1,
        "prev_off": out["cgir_lh_prev"] - 1,
        "node_size": out["cgir_node_size"],
        "node_lh_off": out["cgir_node_lh"] - 1,
        "poison_delta": delta,
        "poison1": (0x100 + delta) & 0xFFFFFFFFFFFFFFFF,
        "poison2": (0x122 + delta) & 0xFFFFFFFFFFFFFFFF,
    }


# ---------------------------------------------------------------------------
# the Rust mirror + faithful ops
# ---------------------------------------------------------------------------

def emit_mirror(L, variant="correct"):
    """Rust ListHead mirror + ops. `variant` selects a deliberate defect for the
    negative controls (each must be caught by the structural differential)."""
    p1, p2 = L["poison1"], L["poison2"]
    # __list_del: kernel writes next->prev THEN prev->next
    del_body = {
        "correct": "        (*next).prev = prev;\n        (*prev).next = next;",
        # backward chain corrupted. NOTE (measured): this is caught by the ADT
        # view too, because list_add_tail reads head->prev, so the corruption
        # propagates into forward order. Kept as a control for the chain check.
        "forward_only": "        (*prev).next = next;",
    }.get(variant, "        (*next).prev = prev;\n        (*prev).next = next;")
    poison = {
        "correct": f"    (*entry).next = {p1:#x}usize as *mut ListHead;\n"
                   f"    (*entry).prev = {p2:#x}usize as *mut ListHead;",
        # unlink without poisoning: the genuinely ADT-INVISIBLE class (measured
        # MATCH under the forward-chain-only oracle, DIVERGE under structural)
        "no_poison": "    /* poisoning omitted (negative control) */",
    }.get(variant, f"    (*entry).next = {p1:#x}usize as *mut ListHead;\n"
                   f"    (*entry).prev = {p2:#x}usize as *mut ListHead;")
    # list_add inserting at the wrong end
    add_body = {
        "correct": "    __list_add(new, head, (*head).next);",
        "add_wrong_side": "    __list_add(new, (*head).prev, head);",
    }.get(variant, "    __list_add(new, head, (*head).next);")

    return f"""#![allow(non_snake_case, dead_code, unused_unsafe)]
// list_head mirror — pointer fields are LOAD-BEARING (not opaque blobs).
// Layout probed in-kernel: size={L['list_head_size']}, next@{L['next_off']}, prev@{L['prev_off']}.
#[repr(C)]
pub struct ListHead {{
    pub next: *mut ListHead,
    pub prev: *mut ListHead,
}}
const _: () = assert!(core::mem::size_of::<ListHead>() == {L['list_head_size']});
const _: () = assert!(core::mem::offset_of!(ListHead, next) == {L['next_off']});
const _: () = assert!(core::mem::offset_of!(ListHead, prev) == {L['prev_off']});

// ---- faithful transcription of include/linux/list.h (write order preserved) --

#[inline]
pub unsafe fn INIT_LIST_HEAD(list: *mut ListHead) {{
    (*list).next = list;
    (*list).prev = list;
}}

#[inline]
pub unsafe fn __list_add(new: *mut ListHead, prev: *mut ListHead, next: *mut ListHead) {{
    (*next).prev = new;
    (*new).next = next;
    (*new).prev = prev;
    (*prev).next = new;
}}

#[inline]
pub unsafe fn list_add(new: *mut ListHead, head: *mut ListHead) {{
{add_body}
}}

#[inline]
pub unsafe fn list_add_tail(new: *mut ListHead, head: *mut ListHead) {{
    __list_add(new, (*head).prev, head);
}}

#[inline]
pub unsafe fn __list_del(prev: *mut ListHead, next: *mut ListHead) {{
{del_body}
}}

#[inline]
pub unsafe fn list_del(entry: *mut ListHead) {{
    __list_del((*entry).prev, (*entry).next);
{poison}
}}

#[inline]
pub unsafe fn list_del_init(entry: *mut ListHead) {{
    __list_del((*entry).prev, (*entry).next);
    INIT_LIST_HEAD(entry);
}}

#[inline]
pub unsafe fn list_empty(head: *const ListHead) -> bool {{
    (*head).next == head as *mut ListHead
}}

// container_of: node base from an embedded list_head (offset probed in-kernel)
#[inline]
pub unsafe fn node_of(lh: *mut ListHead) -> *mut u8 {{
    (lh as *mut u8).offset(-({L['node_lh_off']} as isize))
}}
"""


# ---------------------------------------------------------------------------
# the structural (chain-walking) differential
# ---------------------------------------------------------------------------

_OPS = [                       # (op, a, b) — deterministic script
    ("add", 0, -1), ("add", 1, -1), ("add_tail", 2, -1), ("add_tail", 3, -1),
    ("del", 1, -1), ("add", 4, -1), ("del_init", 2, -1), ("add_tail", 5, -1),
    ("del", 0, -1), ("del", 3, -1), ("add", 6, -1), ("add_tail", 7, -1),
    ("del_init", 4, -1), ("del", 5, -1),
]


def _ref_c(L):
    """C reference: the REAL list.h inlines over an arena of nodes."""
    return f"""
#include <stddef.h>
#include <stdint.h>
#define WRITE_ONCE(x, v) (*(volatile typeof(x) *)&(x) = (v))
#define READ_ONCE(x) (*(volatile typeof(x) *)&(x))
#define LIST_POISON1 ((void *) {L['poison1']:#x}UL)
#define LIST_POISON2 ((void *) {L['poison2']:#x}UL)
struct list_head {{ struct list_head *next, *prev; }};

/* verbatim semantics from include/linux/list.h (DEBUG_LIST off => no valid()) */
static inline void INIT_LIST_HEAD(struct list_head *list) {{
    WRITE_ONCE(list->next, list); WRITE_ONCE(list->prev, list);
}}
static inline void __list_add(struct list_head *new, struct list_head *prev,
                              struct list_head *next) {{
    next->prev = new; new->next = next; new->prev = prev;
    WRITE_ONCE(prev->next, new);
}}
static inline void c_list_add(struct list_head *new, struct list_head *head) {{
    __list_add(new, head, head->next);
}}
static inline void c_list_add_tail(struct list_head *new, struct list_head *head) {{
    __list_add(new, head->prev, head);
}}
static inline void __list_del(struct list_head *prev, struct list_head *next) {{
    next->prev = prev; WRITE_ONCE(prev->next, next);
}}
static inline void c_list_del(struct list_head *entry) {{
    __list_del(entry->prev, entry->next);
    entry->next = LIST_POISON1; entry->prev = LIST_POISON2;
}}
static inline void c_list_del_init(struct list_head *entry) {{
    __list_del(entry->prev, entry->next); INIT_LIST_HEAD(entry);
}}

struct cgir_node {{ int id; struct list_head lh; long payload; }};
static struct cgir_node C_ARENA[{NN}];
static struct list_head C_HEAD;

void c_reset(void) {{
    INIT_LIST_HEAD(&C_HEAD);
    for (int i = 0; i < {NN}; i++) {{
        C_ARENA[i].id = i; C_ARENA[i].payload = 0;
        INIT_LIST_HEAD(&C_ARENA[i].lh);
    }}
}}
void c_op(int op, int a) {{
    struct list_head *e = &C_ARENA[a].lh;
    switch (op) {{
        case 0: c_list_add(e, &C_HEAD); break;
        case 1: c_list_add_tail(e, &C_HEAD); break;
        case 2: c_list_del(e); break;
        case 3: c_list_del_init(e); break;
    }}
}}
/* address-independent normalization: head=-1, node i=i, poison=-100/-101 */
static long normp(void *p) {{
    if (p == (void *)&C_HEAD) return -1;
    if (p == LIST_POISON1) return -100;
    if (p == LIST_POISON2) return -101;
    for (int i = 0; i < {NN}; i++) if (p == (void *)&C_ARENA[i].lh) return i;
    return -999;
}}
/* snapshot: forward chain, backward chain, then every node's raw next/prev */
int c_snapshot(long *buf) {{
    int k = 0;
    struct list_head *w = C_HEAD.next; int guard = 0;
    while (w != &C_HEAD && guard++ < {NN} + 2) {{
        long id = normp(w); buf[k++] = id;
        if (id < 0) break;          /* poison/unknown: record, never deref */
        w = w->next;
    }}
    buf[k++] = -7;                                  /* separator */
    w = C_HEAD.prev; guard = 0;
    while (w != &C_HEAD && guard++ < {NN} + 2) {{
        long id = normp(w); buf[k++] = id;
        if (id < 0) break;
        w = w->prev;
    }}
    buf[k++] = -8;
    for (int i = 0; i < {NN}; i++) {{
        buf[k++] = normp(C_ARENA[i].lh.next);
        buf[k++] = normp(C_ARENA[i].lh.prev);
    }}
    buf[k++] = normp(C_HEAD.next); buf[k++] = normp(C_HEAD.prev);
    return k;
}}
"""


def _cand_rs(L, variant):
    return emit_mirror(L, variant) + f"""
#[repr(C)]
pub struct Node {{ pub id: i32, pub lh: ListHead, pub payload: i64 }}
const _: () = assert!(core::mem::size_of::<Node>() == {L['node_size']});
const _: () = assert!(core::mem::offset_of!(Node, lh) == {L['node_lh_off']});

static mut R_ARENA: [Node; {NN}] = [const {{ Node {{ id: 0,
    lh: ListHead {{ next: core::ptr::null_mut(), prev: core::ptr::null_mut() }},
    payload: 0 }} }}; {NN}];
static mut R_HEAD: ListHead = ListHead {{ next: core::ptr::null_mut(),
                                         prev: core::ptr::null_mut() }};

#[no_mangle] pub extern "C" fn r_reset() {{ unsafe {{
    INIT_LIST_HEAD(&raw mut R_HEAD);
    for i in 0..{NN} {{
        R_ARENA[i].id = i as i32; R_ARENA[i].payload = 0;
        INIT_LIST_HEAD(&raw mut R_ARENA[i].lh);
    }}
}}}}

#[no_mangle] pub extern "C" fn r_op(op: i32, a: i32) {{ unsafe {{
    let e = &raw mut R_ARENA[a as usize].lh;
    match op {{
        0 => list_add(e, &raw mut R_HEAD),
        1 => list_add_tail(e, &raw mut R_HEAD),
        2 => list_del(e),
        3 => list_del_init(e),
        _ => {{}}
    }}
}}}}

unsafe fn normp(p: *mut ListHead) -> i64 {{
    if p == &raw mut R_HEAD {{ return -1; }}
    if p as usize == {L['poison1']:#x} {{ return -100; }}
    if p as usize == {L['poison2']:#x} {{ return -101; }}
    for i in 0..{NN} {{ if p == &raw mut R_ARENA[i].lh {{ return i as i64; }} }}
    -999
}}

#[no_mangle] pub extern "C" fn r_snapshot(buf: *mut i64) -> i32 {{ unsafe {{
    let mut k = 0usize;
    let mut w = R_HEAD.next; let mut guard = 0;
    while w != &raw mut R_HEAD && guard < {NN} + 2 {{
        let id = normp(w); *buf.add(k) = id; k += 1;
        if id < 0 {{ break; }}      // poison/unknown: record, never deref
        w = (*w).next; guard += 1;
    }}
    *buf.add(k) = -7; k += 1;
    w = R_HEAD.prev; guard = 0;
    while w != &raw mut R_HEAD && guard < {NN} + 2 {{
        let id = normp(w); *buf.add(k) = id; k += 1;
        if id < 0 {{ break; }}
        w = (*w).prev; guard += 1;
    }}
    *buf.add(k) = -8; k += 1;
    for i in 0..{NN} {{
        *buf.add(k) = normp(R_ARENA[i].lh.next); k += 1;
        *buf.add(k) = normp(R_ARENA[i].lh.prev); k += 1;
    }}
    *buf.add(k) = normp(R_HEAD.next); k += 1;
    *buf.add(k) = normp(R_HEAD.prev); k += 1;
    k as i32
}}}}
"""


def _probe_c(adt_only=False):
    ops = "\n".join(
        f'    {{ int op={ {"add":0,"add_tail":1,"del":2,"del_init":3}[o] }, a={a};'
        f' c_op(op,a); r_op(op,a);\n'
        f'      int n1=c_snapshot(CB), n2=r_snapshot(RB);\n'
        f'      if (ADT_ONLY) {{ n1 = adt_len(CB, n1); n2 = adt_len(RB, n2); }}\n'
        f'      if (n1!=n2) {{ printf("LISTDIFF verdict=DIVERGE step={i} reason=len %d!=%d\\n",n1,n2); return 1; }}\n'
        f'      for (int j=0;j<n1;j++) if (CB[j]!=RB[j]) {{\n'
        f'        printf("LISTDIFF verdict=DIVERGE step={i} slot=%d c=%ld r=%ld\\n",j,CB[j],RB[j]); return 1; }}\n'
        f'    }}'
        for i, (o, a, _b) in enumerate(_OPS))
    return f"""#include <stdio.h>
#define ADT_ONLY {1 if adt_only else 0}
/* the ADT view: only the forward chain (the abstract sequence), i.e. exactly
   what a membership/order oracle sees — nothing about prev pointers or poison */
static int adt_len(long *b, int n) {{ for (int i=0;i<n;i++) if (b[i]==-7) return i; return n; }}
extern void c_reset(void); extern void c_op(int,int); extern int c_snapshot(long*);
extern void r_reset(void); extern void r_op(int,int); extern int r_snapshot(long*);
static long CB[512], RB[512];
int main(void) {{
    c_reset(); r_reset();
    int n1=c_snapshot(CB), n2=r_snapshot(RB);
    if (n1!=n2) {{ printf("LISTDIFF verdict=DIVERGE step=init reason=len\\n"); return 1; }}
    for (int j=0;j<n1;j++) if (CB[j]!=RB[j]) {{
        printf("LISTDIFF verdict=DIVERGE step=init slot=%d c=%ld r=%ld\\n",j,CB[j],RB[j]); return 1; }}
{ops}
    printf("LISTDIFF verdict=MATCH ops={len(_OPS)}\\n");
    return 0;
}}
"""


def run_diff(L, variant, workdir=None, adt_only=False):
    d = workdir or tempfile.mkdtemp(prefix="listdiff_")
    open(os.path.join(d, "ref.c"), "w").write(_ref_c(L))
    open(os.path.join(d, "cand.rs"), "w").write(_cand_rs(L, variant))
    open(os.path.join(d, "probe.c"), "w").write(_probe_c(adt_only))
    r = subprocess.run(["rustc", "--edition", "2021", "-O", "--crate-type=staticlib",
                        os.path.join(d, "cand.rs"), "-o", os.path.join(d, "libcand.a")],
                       capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL_RS", r.stderr[-1200:], d
    r = subprocess.run(["cc", "-O2", "-w", os.path.join(d, "probe.c"),
                        os.path.join(d, "ref.c"), os.path.join(d, "libcand.a"),
                        "-o", os.path.join(d, "run")], capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL_C", r.stderr[-1200:], d
    r = subprocess.run([os.path.join(d, "run")], capture_output=True, text=True, timeout=60)
    out = (r.stdout + r.stderr).strip()
    m = re.search(r"verdict=(\w+)", out)
    return (m.group(1) if m else "UNKNOWN"), out, d


def prove():
    L = probe_layout()
    print("=== probed list_head layout (in-kernel, config-dependent) ===")
    for k, v in L.items():
        print(f"  {k:16s} {v if not isinstance(v, int) or v < 4096 else hex(v)}")
    print("\n=== chain-walking differential (forward + backward + raw next/prev) ===")
    expect = {"correct": "MATCH", "forward_only": "DIVERGE",
              "no_poison": "DIVERGE", "add_wrong_side": "DIVERGE"}
    why = {"correct": "faithful transcription of list.h",
           "forward_only": "prev-pointers not fixed (also ADT-visible via add_tail)",
           "no_poison": "unlinked without poisoning — ADT-INVISIBLE, structural-only",
           "add_wrong_side": "list_add behaving as list_add_tail: order differs"}
    ok = True
    for variant, want in expect.items():
        v, out, d = run_diff(L, variant)
        good = (v == want)
        ok &= good
        detail = out.splitlines()[-1][:78] if out else ""
        print(f"  {'✓' if good else '✗ UNEXPECTED'} {variant:16s} -> {v:8s} "
              f"(want {want})  [{why[variant]}]")
        if not good:
            print(f"      {detail}\n      dir={d}")
    # prove strictly-stronger: the ADT view must MISS at least one control
    adt = {v: run_diff(L, v, adt_only=True)[0] for v in expect}
    blind = [v for v in expect if adt[v] == "MATCH" and expect[v] == "DIVERGE"]
    print("\n  ADT-only view (forward chain only):",
          ", ".join(f"{v}={adt[v]}" for v in expect))
    print(f"  strictly stronger than an ADT oracle: {bool(blind)} "
          f"(ADT-blind: {blind or 'none'})")
    ok &= bool(blind)
    print("\nLIST-MIRROR GATE:", "PASS — the mirror + ops reproduce the real "
          "list.h structure exactly; the chain-walking oracle rejects a corrupted "
          "chain, a missing poison and a wrong insertion side, and is MEASURED "
          "strictly stronger than an ADT-sequence oracle (which misses "
          f"{blind})" if ok else "FAIL")
    return 0 if ok else 1


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "prove"
    if cmd == "probe":
        for k, v in probe_layout().items():
            print(f"{k}: {v}")
        return 0
    if cmd == "emit":
        print(emit_mirror(probe_layout()))
        return 0
    return prove()


if __name__ == "__main__":
    raise SystemExit(main())
