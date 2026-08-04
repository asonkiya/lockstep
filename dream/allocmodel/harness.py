#!/usr/bin/env python3
"""Allocator-init harness — PRODUCTIZE the fresh-slot oracle over REAL kernel
functions (proof.py proved the mechanism on a synthetic subject; this gates a
function taken verbatim from the tree, reach.py-accepted).

The C reference runs the real function with k[mz]alloc* bump-allocating over a
host arena of the allocated struct; the Rust candidate reproduces the same
fresh-slot sequence against a flat i64 cell model. After EVERY call the probe
compares the returned slot id (NULL -> -1, out-of-arena -> -2) plus EVERY
footprint cell: the arena objects' scalar fields, file-scope globals, and
param-struct fields. This catches exactly the over-credit case the mechanism
proof pinned: a VALID pointer returned with wrong/missing init (no_init /
drop_field -> DIVERGE:state) and allocation-count drift (DIVERGE:ret).

COVERAGE: (a) at least one call must actually allocate (return id >= 0);
(b) every gate-identified written FIELD of the allocated struct must hold a
NONZERO value at some point (post-alloc base state is all-zero, so a written
cell that never leaves zero means the write was never exercised with a
distinguishing value) — else REFUSED_COVERAGE; (c) global/param write targets
use the efftrace CHANGED rule.

Soundness scope (flags from reach): allocation ALWAYS SUCCEEDS (the `if (!p)`
failure path is dead in the model — verdict covers the success-path init
transition); kmalloc is modeled ZEROED on both sides when flagged.

Verdicts: MATCH | DIVERGE:ret | DIVERGE:state | REFUSED_COVERAGE |
BUILD_FAIL_* | TIMEOUT.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("cluster",):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import cluster                            # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "eff_harness_am", os.path.join(HERE, "..", "efftrace", "harness.py"))
EH = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(EH)

KSRC = EH.KSRC
Unsupported = EH.Unsupported

NA, NN, R, W = 12, 4, 4, 8   # arena slots / param-node slots / rounds / calls
_ALLOC_NAMES = ("kzalloc", "kzalloc_obj", "kzalloc_node",
                "kmalloc", "kmalloc_obj", "kmalloc_node",
                "kmem_cache_zalloc", "kmem_cache_alloc")


def _fn_text(rec):
    src = open(os.path.join(KSRC, rec["file"]), errors="ignore").read()
    return cluster.functions(src)[rec["fn"]]["text"]


def prepare(rec):
    """Build the harness context from an allocmodel reach.py accepted record."""
    fn_text = _fn_text(rec)
    gnames = list(rec["globals"])
    node_ps = [p for p in rec["params"] if p["kind"] == "node"]
    if len(node_ps) > 2:
        raise Unsupported("too many struct params")
    afields = rec["alloc_fields"]

    # ---- flat state vector: globals, param-node fields, arena fields --------
    cells = [("g", n) for n in gnames]
    for pi, p in enumerate(node_ps):
        for f in sorted(p["scalar_fields"]):
            for slot in range(NN):
                cells.append(("pf", pi, f, slot))
    af_base = {}
    for f in sorted(afields):
        af_base[f] = len(cells)
        for slot in range(NA):
            cells.append(("af", f, slot))
    nstate = len(cells)
    if nstate > 400:
        raise Unsupported(f"state vector too large ({nstate})")

    # coverage targets
    widx = [i for i, c in enumerate(cells)
            if (c[0] == "g" and c[1] in rec["write_globals"])
            or (c[0] == "pf" and f"{node_ps[c[1]]['name']}->{c[2]}" in rec["write_fields"])]
    af_widx = [af_base[f] + s for f in rec["write_afields"] for s in range(NA)]

    # ---- C reference TU ------------------------------------------------------
    T = rec["alloc_struct"]
    c = [EH.EFF_H]
    for n, v in rec["defines"].items():
        c.append(f"#define {n} {v}")
    for n, g in rec["globals"].items():
        c.append(f"static {g['ctype']} {n};")
    emitted = set()
    for pi, p in enumerate(node_ps):
        if p["struct"] not in emitted:
            emitted.add(p["struct"])
            c.append(f"struct {p['struct']} {{")
            for f, t in sorted(p["scalar_fields"].items()):
                c.append(f"    {t} {f};")
            c.append("};")
        c.append(f"static struct {p['struct']} EP{pi}[{NN}];")
    if T not in emitted:
        c.append(f"struct {T} {{")
        for f, t in sorted(afields.items()):
            c.append(f"    {t} {f};")
        c.append("};")
    c.append(f"static struct {T} AM_ARENA[{NA}];")
    c.append("static int AM_NEXT;")
    c.append(f"""static void *am_alloc(void){{
    if (AM_NEXT >= {NA}) return 0;
    struct {T} *p = &AM_ARENA[AM_NEXT++];
    __builtin_memset(p, 0, sizeof *p);      /* kzalloc zeroes; kmalloc modeled zeroed (flagged) */
    return p;
}}""")
    for an in _ALLOC_NAMES:
        c.append(f"#define {an}(...) am_alloc()")
    c.append("#define kfree(p) ((void)0)")
    c.append("#define kmem_cache_free(...) ((void)0)")
    c.append("#define ERR_PTR(x) ((void *)(long)(x))")
    c.append("#define INIT_LIST_HEAD(...) ((void)0)   /* stripped+flagged: container-oracle composition */")
    c.append("typedef unsigned gfp_t;")
    c.append("#define GFP_KERNEL 0")
    c.append("#define GFP_ATOMIC 0")
    c.append("#define GFP_NOWAIT 0")
    c.append('#line 1000 "fnsrc"')
    c.append(fn_text)
    c.append("void am_reset(void){ AM_NEXT = 0; "
             "__builtin_memset(AM_ARENA, 0, sizeof AM_ARENA);")
    for n, g in rec["globals"].items():
        c.append(f"    {n} = {g['init']};")
    for pi, _ in enumerate(node_ps):
        c.append(f"    __builtin_memset(EP{pi}, 0, sizeof EP{pi});")
    c.append("}")
    c.append("void am_set(int ix, long v){ switch (ix) {")
    for i, cell in enumerate(cells):
        if cell[0] == "g":
            c.append(f"    case {i}: {cell[1]} = ({rec['globals'][cell[1]]['ctype']})v; break;")
        elif cell[0] == "pf":
            _, pi, f, slot = cell
            t = node_ps[pi]["scalar_fields"][f]
            c.append(f"    case {i}: EP{pi}[{slot}].{f} = ({t})v; break;")
        else:
            _, f, slot = cell
            c.append(f"    case {i}: AM_ARENA[{slot}].{f} = ({afields[f]})v; break;")
    c.append("} }")
    c.append("void am_state(long *buf){")
    for i, cell in enumerate(cells):
        if cell[0] == "g":
            c.append(f"    buf[{i}] = (long){cell[1]};")
        elif cell[0] == "pf":
            _, pi, f, slot = cell
            c.append(f"    buf[{i}] = (long)EP{pi}[{slot}].{f};")
        else:
            _, f, slot = cell
            c.append(f"    buf[{i}] = (long)AM_ARENA[{slot}].{f};")
    c.append("}")
    tramp_args, call_args = [], []
    node_seen = 0
    for i, p in enumerate(rec["params"]):
        a = f"a{i}"
        tramp_args.append(f"long {a}")
        if p["kind"] == "node":
            call_args.append(f"&EP{node_seen}[{a}]")
            node_seen += 1
        else:
            call_args.append(a)
    c.append(f"""long am_call({', '.join(tramp_args) or 'void'}){{
    struct {T} *r = {rec['fn']}({', '.join(call_args)});
    if (!r) return -1;
    if ((unsigned long)r >= (unsigned long)-4095L) return (long)r;   /* ERR_PTR */
    return (r >= AM_ARENA && r < AM_ARENA + {NA}) ? r - AM_ARENA : -2;
}}""")
    csrc = "\n".join(c) + "\n"

    # ---- Rust state-model surface -------------------------------------------
    consts = [f"const {n}: i64 = {v};" for n, v in rec["defines"].items()]
    for i, cell in enumerate(cells):
        if cell[0] == "g":
            consts.append(f"const G_{cell[1].upper()}: usize = {i};")
    for pi, p in enumerate(node_ps):
        for f in sorted(p["scalar_fields"]):
            base = next(i for i, cc in enumerate(cells)
                        if cc[0] == "pf" and cc[1] == pi and cc[2] == f)
            consts.append(f"const F{pi}_{f.upper()}: usize = {base};  // + slot")
    for f, base in af_base.items():
        consts.append(f"const A_{f.upper()}: usize = {base};  // + slot id")

    def _cw(cell):
        if cell[0] == "g":
            t = rec["globals"][cell[1]]["ctype"]
        elif cell[0] == "pf":
            t = node_ps[cell[1]]["scalar_fields"][cell[2]]
        else:
            t = afields[cell[1]]
        return EH._cell_width(t)
    cw_rows = ", ".join(f"({b}, {1 if s else 0})" for b, s in
                        (_cw(cell) for cell in cells))
    zero_slot = "\n".join(f"        S[{base} + id as usize] = 0;"
                          for base in af_base.values())
    surface = f"""#![allow(non_snake_case, dead_code, static_mut_refs, unused_unsafe, unused_imports, unused_variables, non_upper_case_globals)]
// generated fresh-slot state model — flat cell vector, index map identical to C.
const NSTATE: usize = {nstate};
const NA: i64 = {NA};
{chr(10).join(consts)}
const CW: [(u32, u32); NSTATE] = [{cw_rows}];   // (bits, signed) per cell
static mut S: [i64; NSTATE] = [0; NSTATE];
static mut RS_NEXT: i64 = 0;
fn norm(ix: usize, v: i64) -> i64 {{
    let (bits, signed) = CW[ix];
    match bits {{
        64 => v,
        1 => (v != 0) as i64,
        _ => {{
            let m = (1i64 << bits) - 1;
            let x = v & m;
            if signed == 1 && (x >> (bits - 1)) & 1 == 1 {{ x | !m }} else {{ x }}
        }}
    }}
}}
#[no_mangle] pub extern "C" fn rs_reset() {{ unsafe {{
    S = [0; NSTATE];
    RS_NEXT = 0;
{chr(10).join(f'    S[{i}] = {EH._pynorm(rec["globals"][cell[1]]["ctype"], rec["globals"][cell[1]]["init"])};'
              for i, cell in enumerate(cells)
              if cell[0] == "g" and rec["globals"][cell[1]]["init"])}
}}}}
#[no_mangle] pub extern "C" fn rs_set(ix: i32, v: i64) {{ unsafe {{ S[ix as usize] = norm(ix as usize, v); }}}}
#[no_mangle] pub extern "C" fn rs_state(buf: *mut i64) {{ unsafe {{
    for i in 0..NSTATE {{ *buf.add(i) = S[i]; }}
}}}}
// ---- candidate-facing helpers ---------------------------------------------
// alloc(): the fresh zeroed slot, mirroring the C's bump allocator exactly.
fn alloc() -> i64 {{ unsafe {{
    if RS_NEXT >= NA {{ return -1; }}
    let id = RS_NEXT; RS_NEXT += 1;
{zero_slot}
    id
}}}}
fn af(base: usize, id: i64) -> i64 {{ unsafe {{ S[base + id as usize] }} }}
fn set_af(base: usize, id: i64, v: i64) {{ unsafe {{ let ix = base + id as usize; S[ix] = norm(ix, v); }} }}
fn g(ix: usize) -> i64 {{ unsafe {{ S[ix] }} }}
fn set_g(ix: usize, v: i64) {{ unsafe {{ S[ix] = norm(ix, v); }} }}
fn field(base: usize, slot: i64) -> i64 {{ unsafe {{ S[base + slot as usize] }} }}
fn set_field(base: usize, slot: i64, v: i64) {{ unsafe {{ let ix = base + slot as usize; S[ix] = norm(ix, v); }} }}
"""
    rs_args = [f"a{i}: i64" for i in range(len(rec["params"]))]
    rs_sig = f'#[no_mangle] pub extern "C" fn rs_call({", ".join(rs_args)}) -> i64'

    # ---- workload ------------------------------------------------------------
    pw = EH._param_widths(fn_text, rec["params"])
    g_ = EH._lcg()
    vals = [0, 1, 2, 7, -1, 3, 5, 100]
    rounds = []
    for r in range(R):
        if r == 0:
            seeds = []
        elif r == 1:
            seeds = [(i, 1000 + i) for i in range(nstate)]
        else:
            seeds = [(i, vals[next(g_) % 8]) for i in range(nstate)]
        calls = []
        for k in range(W):
            row = []
            pi_seen = 0
            for pidx, p in enumerate(rec["params"]):
                if p["kind"] == "node":
                    row.append((k + pi_seen) % NN if r == 1 else next(g_) % NN)
                    pi_seen += 1
                else:
                    if r == 2:
                        v = EH._BSWEEP[(k + pidx) % len(EH._BSWEEP)]
                    else:
                        v = vals[next(g_) % 8]
                    row.append(EH._norm_bits(*pw[len(row)], v) if pw[len(row)] else v)
            calls.append(row)
        rounds.append({"seeds": seeds, "calls": calls})

    # ---- doc for the model ---------------------------------------------------
    argdoc = []
    node_seen = 0
    for i, p in enumerate(rec["params"]):
        if p["kind"] == "node":
            fl = ", ".join(f"F{node_seen}_{f.upper()}" for f in sorted(p["scalar_fields"]))
            argdoc.append(f"a{i}={p['name']} (struct slot 0..{NN-1}: "
                          f"field(BASE, a{i}) with BASE in {{{fl}}}; always a "
                          f"valid non-null pointer)")
            node_seen += 1
        else:
            argdoc.append(f"a{i}={p['name']} (scalar; gfp flags are irrelevant "
                          f"— the model allocator ignores them)"
                          if "gfp" in p["name"] or "flag" in p["name"]
                          else f"a{i}={p['name']} (scalar)")
    doc = (f"// C function under translation (from {rec['file']}):\n"
           + "\n".join("// " + ln for ln in fn_text.split("\n"))
           + "\n// Available constants:\n"
           + "\n".join("//   " + cc for cc in consts)
           + "\n// ALLOCATION MODEL: call alloc() exactly where the C calls"
           + f"\n// k[mz]alloc* — it returns the fresh ZEROED slot id for the"
           + f"\n// allocated `struct {T}`. It never fails in this model (the C's"
           + "\n// `if (!p)` failure branch is dead — you may omit it entirely)."
           + "\n// Field access on the allocated object: af(A_FIELD, id) /"
           + "\n// set_af(A_FIELD, id, v). kfree is a state no-op."
           + "\n// RETURN: the slot id for `return p`; -1 for `return NULL`;"
           + "\n// `return ERR_PTR(-E)` -> return the negative errno (-ENOMEM = -12)."
           + "\n// Other helpers: g(G_*), set_g(G_*, v) for globals;"
           + "\n//   field(F*_X, slot), set_field(F*_X, slot, v) for struct params."
           + "\n// STORES are automatically truncated to the C field's declared"
           + "\n// width — replicate the C's VALUE logic only."
           + "\n// Args: " + "; ".join(argdoc))

    return {"rec": rec, "csrc": csrc, "surface": surface, "rs_sig": rs_sig,
            "doc": doc, "cells": cells, "nstate": nstate, "widx": widx,
            "af_widx": af_widx, "rounds": rounds,
            "nparams": len(rec["params"]), "flags": rec["flags"], "pw": pw}


def _probe_c(prep):
    npar = prep["nparams"]
    ns = prep["nstate"]
    argdecl = ", ".join(["long"] * npar) if npar else "void"
    nafw = len(prep["af_widx"])
    lines = [
        "#include <stdio.h>",
        "extern void am_reset(void); extern void rs_reset(void);",
        "extern void am_set(int, long); extern void rs_set(int, long);",
        "extern void am_state(long*); extern void rs_state(long*);",
        f"extern long am_call({argdecl}); extern long rs_call({argdecl});",
        f"static const int WIDX[{max(len(prep['widx']), 1)}] = {{ "
        + (", ".join(map(str, prep["widx"])) or "0") + " };",
        f"static const int AFW[{max(nafw, 1)}] = {{ "
        + (", ".join(map(str, prep["af_widx"])) or "0") + " };",
        f"static long CB[{ns}], RB[{ns}], PREV[{ns}];",
        f"static int CHANGED[{ns}], NONZERO[{ns}];",
        "static int NALLOC;",
        "int main(void){",
    ]
    k = 0
    for rnd in prep["rounds"]:
        lines.append("    am_reset(); rs_reset();")
        for ix, v in rnd["seeds"]:
            lines.append(f"    am_set({ix},{v}); rs_set({ix},{v});")
        for row in rnd["calls"]:
            args = ", ".join(str(v) for v in row)
            lines.append(f"""    am_state(PREV);
    {{ long rc = am_call({args}); long rr = rs_call({args});
      if (rc != rr) {{ printf("ALLOCM verdict=DIVERGE:ret call={k} c=%ld r=%ld\\n", rc, rr); return 1; }}
      if (rc >= 0) NALLOC++;
      am_state(CB); rs_state(RB);
      for (int i = 0; i < {ns}; i++) {{
        if (CB[i] != PREV[i]) CHANGED[i] = 1;
        if (CB[i] != 0) NONZERO[i] = 1;
        if (CB[i] != RB[i]) {{
          printf("ALLOCM verdict=DIVERGE:state call={k} cell=%d c=%ld r=%ld\\n", i, CB[i], RB[i]);
          return 1;
        }}
      }}
    }}""")
            k += 1
    lines.append(f"""    if (!NALLOC) {{
        printf("ALLOCM verdict=REFUSED_COVERAGE cell=-1\\n");   /* nothing allocated */
        return 2;
    }}
    for (unsigned j = 0; j < {len(prep['widx'])}; j++)
        if (!CHANGED[WIDX[j]]) {{
            printf("ALLOCM verdict=REFUSED_COVERAGE cell=%d\\n", WIDX[j]);
            return 2;
        }}
    {{ /* every written alloc-field must go nonzero on SOME slot */
      int off = 0; (void)off;
    }}""")
    # af coverage: field is covered if ANY of its NA slot cells went nonzero
    if nafw:
        lines.append(f"""    for (unsigned f = 0; f < {nafw}; f += {NA}) {{
        int any = 0;
        for (unsigned s = 0; s < {NA}; s++) if (NONZERO[AFW[f + s]]) any = 1;
        if (!any) {{
            printf("ALLOCM verdict=REFUSED_COVERAGE cell=%d\\n", AFW[f]);
            return 2;
        }}
    }}""")
    lines.append(f"""    printf("ALLOCM verdict=MATCH calls={k}\\n");
    return 0;
}}""")
    return "\n".join(lines) + "\n"


def close(prep, rust_body, workdir=None):
    d = workdir or tempfile.mkdtemp(prefix="allocm_")
    open(os.path.join(d, "ref.c"), "w").write(prep["csrc"])
    open(os.path.join(d, "cand.rs"), "w").write(
        prep["surface"] + "\n" + prep["rs_sig"] + " {\n" + rust_body + "\n}\n")
    open(os.path.join(d, "probe.c"), "w").write(_probe_c(prep))
    r = EH._run(["rustc", "--edition", "2021", "-O", "--crate-type=staticlib",
                 os.path.join(d, "cand.rs"), "-o", os.path.join(d, "libcand.a")], 90)
    if r is None:
        return {"verdict": "TIMEOUT:rustc", "out": "", "dir": d}
    if r.returncode:
        return {"verdict": "BUILD_FAIL_RS", "out": r.stderr[-2000:], "dir": d}
    r = EH._run(["cc", "-O2", "-w", os.path.join(d, "probe.c"), os.path.join(d, "ref.c"),
                 os.path.join(d, "libcand.a"), "-o", os.path.join(d, "run")], 90)
    if r is None:
        return {"verdict": "TIMEOUT:cc", "out": "", "dir": d}
    if r.returncode:
        return {"verdict": "BUILD_FAIL_C", "out": r.stderr[-2000:], "dir": d}
    r = EH._run([os.path.join(d, "run")], 30)
    if r is None:
        return {"verdict": "TIMEOUT:run", "out": "", "dir": d}
    out = (r.stdout + r.stderr).strip()
    m = re.search(r"verdict=([A-Z_]+(?::[a-z]+)?)", out)
    return {"verdict": m.group(1) if m else f"UNKNOWN(rc={r.returncode})",
            "out": out, "dir": d}


# ---------------------------------------------------------------------------
# self-check on a REAL accepted function — set by main() from reach_accepted.
# ---------------------------------------------------------------------------

def main():
    import json
    spec = importlib.util.spec_from_file_location(
        "alloc_reach_hn", os.path.join(HERE, "reach.py"))
    reach = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reach)
    rj = os.path.join(HERE, "reach_accepted.json")
    items = json.load(open(rj))
    print(f"=== allocmodel harness self-check over {len(items)} accepted ===")
    ok_prep, refused = 0, {}
    for it in items:
        try:
            prepare(it)
            ok_prep += 1
        except Exception as e:
            refused.setdefault(str(e)[:40], []).append(it["fn"])
    print(f"  harness-preparable: {ok_prep}/{len(items)}")
    for kk, v in sorted(refused.items(), key=lambda kv: -len(kv[1])):
        print(f"  prepare-refuse {len(v):3d}  {kk}  e.g. {v[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
