#!/usr/bin/env python3
"""Effect-trace harness — PRODUCTIZE the bounded-state oracle over REAL kernel
functions (proof.py proved the ordered record/replay mechanism on a synthetic
subject; this gates a function taken verbatim from the tree).

The productized verdict is a per-call FULL-FOOTPRINT state differential:
run the real C verbatim against a host state model, and after EVERY call
compare the return value plus EVERY footprint cell (file-scope globals,
out-params, param-struct scalar fields) against the Rust state-model
candidate. The reach gate guarantees footprint completeness (every write
target resolved or the fn was refused), so full-state-per-call equality
catches exactly the over-credit case the mechanism proof was built around:
right RETURN, wrong STATE. Intra-call effect ORDER is unobservable to a
single-threaded caller — ordering claims belong to concgate's composition,
which is also where the stripped-and-flagged lock brackets point.

Workload = R rounds x W calls: round 0 starts from the DECLARED initializers
(kernel-boot state), later rounds perturb every cell (any state is a
legitimate input to the state-transition function; divergence anywhere is a
real difference). COVERAGE: every gate-identified write target must actually
CHANGE at least once across the run, else REFUSED_COVERAGE — an un-exercised
write can never certify. (A write of an identical value is invisible to this
check — v1 limitation, documented.)

Verdicts: MATCH | DIVERGE:ret | DIVERGE:state | REFUSED_COVERAGE |
BUILD_FAIL_* | TIMEOUT.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("cluster",):
    sys.path.insert(0, os.path.join(HERE, "..", p))
import cluster                            # noqa: E402

KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")

LOCK_NOOPS = (
    "spin_lock", "spin_unlock", "spin_lock_irqsave", "spin_unlock_irqrestore",
    "spin_lock_irq", "spin_unlock_irq", "spin_lock_bh", "spin_unlock_bh",
    "raw_spin_lock", "raw_spin_unlock", "raw_spin_lock_irqsave",
    "raw_spin_unlock_irqrestore", "mutex_lock", "mutex_unlock",
    "lockdep_assert_held", "assert_spin_locked",
)

NN, R, W = 4, 4, 12          # arena slots per struct param / rounds / calls

EFF_H = r"""
#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include <sys/types.h>
typedef uint8_t  u8;  typedef int8_t  s8;  typedef uint8_t  __u8;  typedef int8_t  __s8;
typedef uint16_t u16; typedef int16_t s16; typedef uint16_t __u16; typedef int16_t __s16;
typedef uint32_t u32; typedef int32_t s32; typedef uint32_t __u32; typedef int32_t __s32;
typedef uint64_t u64; typedef int64_t s64; typedef uint64_t __u64; typedef int64_t __s64;
typedef uint64_t phys_addr_t; typedef uint64_t dma_addr_t; typedef uint64_t resource_size_t;
typedef uint16_t __le16; typedef uint16_t __be16;
typedef uint32_t __le32; typedef uint32_t __be32;
typedef uint64_t __le64; typedef uint64_t __be64;
#define cpu_to_le16(x) ((__le16)(x))
#define cpu_to_le32(x) ((__le32)(x))
#define cpu_to_le64(x) ((__le64)(x))
#define le16_to_cpu(x) ((uint16_t)(x))
#define le32_to_cpu(x) ((uint32_t)(x))
#define le64_to_cpu(x) ((uint64_t)(x))
#define min(a,b) ((a) < (b) ? (a) : (b))
#define max(a,b) ((a) > (b) ? (a) : (b))
#define min_t(t,a,b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define max_t(t,a,b) ((t)(a) > (t)(b) ? (t)(a) : (t)(b))
#define clamp(v,lo,hi) min(max(v,lo),hi)
#define clamp_t(t,v,lo,hi) min_t(t,max_t(t,v,lo),hi)
#define __ffs(x) ((unsigned long)__builtin_ctzl((unsigned long)(x)))
#define __fls(x) ((unsigned long)(63 - __builtin_clzl((unsigned long)(x))))
#define ffs(x) __builtin_ffs(x)
#define fls(x) ((x) ? 32 - __builtin_clz((unsigned int)(x)) : 0)
#define fls64(x) ((x) ? 64 - __builtin_clzll((unsigned long long)(x)) : 0)
#define hweight8(x) __builtin_popcount((unsigned char)(x))
#define hweight16(x) __builtin_popcount((unsigned short)(x))
#define hweight32(x) __builtin_popcount((unsigned int)(x))
#define hweight64(x) __builtin_popcountll((unsigned long long)(x))
#define noinline
#define __cold
#define __hot
#define __weak
#define __sched
#define __pure
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
#define likely(x) (x)
#define unlikely(x) (x)
#define __init
#define __exit
#define __maybe_unused
""" + "\n".join(f"#define {lk}(...) (void)0" for lk in LOCK_NOOPS) + "\n"


class Unsupported(Exception):
    pass


# ctype -> (bits, signed) on the HOST (LP64, Apple clang: plain char signed).
# Everything not listed (long, u64, size_t, ...) is 64-bit: no-op.
_WIDTH = {
    "bool": (1, False),
    "char": (8, True), "signed char": (8, True), "unsigned char": (8, False),
    "u8": (8, False), "__u8": (8, False), "s8": (8, True), "__s8": (8, True),
    "uint8_t": (8, False), "int8_t": (8, True),
    "short": (16, True), "short int": (16, True), "unsigned short": (16, False),
    "u16": (16, False), "__u16": (16, False), "s16": (16, True), "__s16": (16, True),
    "__le16": (16, False), "__be16": (16, False),
    "uint16_t": (16, False), "int16_t": (16, True),
    "int": (32, True), "signed int": (32, True),
    "unsigned": (32, False), "unsigned int": (32, False),
    "u32": (32, False), "__u32": (32, False), "s32": (32, True), "__s32": (32, True),
    "__le32": (32, False), "__be32": (32, False),
    "uint32_t": (32, False), "int32_t": (32, True),
}


def _cell_width(ctype):
    return _WIDTH.get(" ".join(ctype.replace("const", " ").split()), (64, True))


def _pynorm(ctype, v):
    bits, signed = _cell_width(ctype)
    if bits == 64:
        return v
    if bits == 1:
        return 1 if v else 0
    m = (1 << bits) - 1
    x = v & m
    return x - (1 << bits) if signed and (x >> (bits - 1)) & 1 else x


def _lcg(seed=99991):
    s = seed
    while True:
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
        yield s


def _fn_text(rec):
    src = open(os.path.join(KSRC, rec["file"]), errors="ignore").read()
    return cluster.functions(src)[rec["fn"]]["text"]


def prepare(rec):
    """Build the harness context from an efftrace reach.py accepted record."""
    fn_text = _fn_text(rec)
    gnames = list(rec["globals"])                      # sorted by reach
    outs = [p for p in rec["params"] if p["kind"] == "outp"]
    node_ps = [p for p in rec["params"] if p["kind"] == "node"]
    if len(node_ps) > 2:
        raise Unsupported("too many struct params")

    # ---- the flat state vector: globals, out-params, node fields ----------
    # index map is generated identically on both sides.
    cells = [("g", n) for n in gnames] + [("out", p["name"]) for p in outs]
    for pi, p in enumerate(node_ps):
        for f in sorted(p["scalar_fields"]):
            for slot in range(NN):
                cells.append(("pf", pi, f, slot))
    nstate = len(cells)
    if nstate > 200:
        raise Unsupported(f"state vector too large ({nstate})")

    # write targets -> state-vector indices (for the coverage gate)
    widx = []
    for i, c in enumerate(cells):
        if c[0] == "g" and c[1] in rec["write_globals"]:
            widx.append(i)
        elif c[0] == "out" and c[1] in rec.get("write_outp", []):
            widx.append(i)
        elif c[0] == "pf":
            _, pi, f, _ = c
            if f"{node_ps[pi]['name']}->{f}" in rec["write_fields"]:
                widx.append(i)
    if not widx:
        raise Unsupported("no write-target cells")

    # ---- C reference TU ----------------------------------------------------
    c = [EFF_H]
    for n, v in rec["defines"].items():
        c.append(f"#define {n} {v}")
    for n, g in rec["globals"].items():
        c.append(f"static {g['ctype']} {n};")
    emitted = set()
    for pi, p in enumerate(node_ps):
        if p["struct"] not in emitted:      # two params of one type: def ONCE
            emitted.add(p["struct"])
            c.append(f"struct {p['struct']} {{")
            for f, t in sorted(p["scalar_fields"].items()):
                c.append(f"    {t} {f};")
            c.append("};")
        c.append(f"static struct {p['struct']} EP{pi}[{NN}];")
    for oi, p in enumerate(outs):
        c.append(f"static {p['ctype']} EOP{oi};")
    c.append('#line 1000 "fnsrc"')
    c.append(fn_text)

    # setters / state extraction / trampoline
    c.append("void eff_reset(void){")
    for n, g in rec["globals"].items():
        c.append(f"    {n} = {g['init']};")
    for oi, _ in enumerate(outs):
        c.append(f"    EOP{oi} = 0;")
    for pi, p in enumerate(node_ps):
        c.append(f"    for (int i = 0; i < {NN}; i++) "
                 f"__builtin_memset(&EP{pi}[i], 0, sizeof(EP{pi}[i]));")
    c.append("}")
    c.append("void eff_set(int ix, long v){ switch (ix) {")
    for i, cell in enumerate(cells):
        if cell[0] == "g":
            c.append(f"    case {i}: {cell[1]} = ({rec['globals'][cell[1]]['ctype']})v; break;")
        elif cell[0] == "out":
            oi = [p["name"] for p in outs].index(cell[1])
            c.append(f"    case {i}: EOP{oi} = ({outs[oi]['ctype']})v; break;")
        else:
            _, pi, f, slot = cell
            t = node_ps[pi]["scalar_fields"][f]
            c.append(f"    case {i}: EP{pi}[{slot}].{f} = ({t})v; break;")
    c.append("} }")
    c.append("void eff_state(long *buf){")
    for i, cell in enumerate(cells):
        if cell[0] == "g":
            c.append(f"    buf[{i}] = (long){cell[1]};")
        elif cell[0] == "out":
            oi = [p["name"] for p in outs].index(cell[1])
            c.append(f"    buf[{i}] = (long)EOP{oi};")
        else:
            _, pi, f, slot = cell
            c.append(f"    buf[{i}] = (long)EP{pi}[{slot}].{f};")
    c.append("}")
    tramp_args, call_args = [], []
    node_seen, out_seen = 0, 0
    for i, p in enumerate(rec["params"]):
        a = f"a{i}"
        tramp_args.append(f"long {a}")
        if p["kind"] == "node":
            call_args.append(f"&EP{node_seen}[{a}]")
            node_seen += 1
        elif p["kind"] == "outp":
            call_args.append(f"&EOP{out_seen}")
            out_seen += 1
        else:
            call_args.append(a)
    callexpr = f"{rec['fn']}({', '.join(call_args)})"
    body = (f"{{ {callexpr}; return 0; }}" if rec["ret"] == "void"
            else f"{{ return (long){callexpr}; }}")
    c.append(f"long eff_call({', '.join(tramp_args) or 'void'}){body}")
    csrc = "\n".join(c) + "\n"

    # ---- Rust state-model surface -----------------------------------------
    consts = [f"const {n}: i64 = {v};" for n, v in rec["defines"].items()]
    for i, cell in enumerate(cells):
        if cell[0] == "g":
            consts.append(f"const G_{cell[1].upper()}: usize = {i};")
        elif cell[0] == "out":
            consts.append(f"const OUT_{cell[1].upper()}: usize = {i};")
    pf_base = {}
    for pi, p in enumerate(node_ps):
        for f in sorted(p["scalar_fields"]):
            base = next(i for i, cc in enumerate(cells)
                        if cc[0] == "pf" and cc[1] == pi and cc[2] == f)
            pf_base[(pi, f)] = base
            consts.append(f"const F{pi}_{f.upper()}: usize = {base};  // + slot")
    # per-cell (bits, signed): every store — seeds AND candidate writes — is
    # truncated exactly like the C's typed field store, so a u8 cell seeded
    # 1008 holds 240 on BOTH sides and a u32 cell holding -1 reads 4294967295.
    # (The census showed 47/69 misses were exactly this width mismatch.)
    def _cw(cell):
        if cell[0] == "g":
            t = rec["globals"][cell[1]]["ctype"]
        elif cell[0] == "out":
            t = outs[[p["name"] for p in outs].index(cell[1])]["ctype"]
        else:
            t = node_ps[cell[1]]["scalar_fields"][cell[2]]
        return _cell_width(t)
    cw_rows = ", ".join(f"({b}, {1 if s else 0})" for b, s in
                        (_cw(cell) for cell in cells))
    surface = f"""#![allow(non_snake_case, dead_code, static_mut_refs, unused_unsafe, unused_imports, unused_variables, non_upper_case_globals)]
// generated state model — one flat cell vector, index map identical to the C.
const NSTATE: usize = {nstate};
{chr(10).join(consts)}
const CW: [(u32, u32); NSTATE] = [{cw_rows}];   // (bits, signed) per cell
static mut S: [i64; NSTATE] = [0; NSTATE];
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
{chr(10).join(f'    S[{i}] = {_pynorm(rec["globals"][cell[1]]["ctype"], rec["globals"][cell[1]]["init"])};'
              for i, cell in enumerate(cells)
              if cell[0] == "g" and rec["globals"][cell[1]]["init"])}
}}}}
#[no_mangle] pub extern "C" fn rs_set(ix: i32, v: i64) {{ unsafe {{ S[ix as usize] = norm(ix as usize, v); }}}}
#[no_mangle] pub extern "C" fn rs_state(buf: *mut i64) {{ unsafe {{
    for i in 0..NSTATE {{ *buf.add(i) = S[i]; }}
}}}}
// ---- candidate-facing helpers (stores are width-normalized like the C) ----
fn g(ix: usize) -> i64 {{ unsafe {{ S[ix] }} }}
fn set_g(ix: usize, v: i64) {{ unsafe {{ S[ix] = norm(ix, v); }} }}
fn out(ix: usize) -> i64 {{ unsafe {{ S[ix] }} }}
fn set_out(ix: usize, v: i64) {{ unsafe {{ S[ix] = norm(ix, v); }} }}
fn field(base: usize, slot: i64) -> i64 {{ unsafe {{ S[base + slot as usize] }} }}
fn set_field(base: usize, slot: i64, v: i64) {{ unsafe {{ let ix = base + slot as usize; S[ix] = norm(ix, v); }} }}
"""
    rs_args = [f"a{i}: i64" for i in range(len(rec["params"]))]
    rs_sig = f'#[no_mangle] pub extern "C" fn rs_call({", ".join(rs_args)}) -> i64'

    # ---- workload ----------------------------------------------------------
    g_ = _lcg()
    vals = [0, 1, 2, 7, -1, 3, 5, 100]
    rounds = []
    for r in range(R):
        # round 0: declared-init boot state. round 1: every cell seeded with a
        # UNIQUE nonzero value (1000+ix) and slots cycled deterministically, so
        # every unconditional write is VISIBLE to the coverage check even when
        # it writes 0 or a repeated value (the `x = 0` reset idiom starved the
        # change-based gate under random seeds). later rounds: varied values.
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
            for p in rec["params"]:
                if p["kind"] == "node":
                    row.append((k + pi_seen) % NN if r == 1 else next(g_) % NN)
                    pi_seen += 1
                elif p["kind"] == "outp":
                    row.append(0)
                else:
                    row.append(vals[next(g_) % 8])
            calls.append(row)
        rounds.append({"seeds": seeds, "calls": calls})

    # ---- doc for the model -------------------------------------------------
    argdoc = []
    node_seen = 0
    for i, p in enumerate(rec["params"]):
        if p["kind"] == "node":
            fl = ", ".join(f"F{node_seen}_{f.upper()}" for f in sorted(p["scalar_fields"]))
            argdoc.append(f"a{i}={p['name']} (struct slot 0..{NN-1}: "
                          f"field(BASE, a{i}) with BASE in {{{fl}}})")
            node_seen += 1
        elif p["kind"] == "outp":
            argdoc.append(f"a{i}={p['name']} (OUT-param: out(OUT_{p['name'].upper()})"
                          f" / set_out(OUT_{p['name'].upper()}, v); the a{i} value"
                          f" itself is an opaque non-null handle — ignore it)")
        else:
            argdoc.append(f"a{i}={p['name']} (scalar)")
    doc = (f"// C function under translation (from {rec['file']}):\n"
           + "\n".join("// " + ln for ln in fn_text.split("\n"))
           + "\n// Available constants:\n"
           + "\n".join("//   " + cc for cc in consts)
           + "\n// State cells are i64. Helpers: g(G_*), set_g(G_*, v),"
           + "\n//   out(OUT_*), set_out(OUT_*, v), field(F*_X, slot),"
           + "\n//   set_field(F*_X, slot, v)."
           + "\n// STORES are automatically truncated to the C field's declared"
           + "\n// width (a u8 cell stores 1008 as 240; a u32 cell holding -1"
           + "\n// reads back 4294967295) — replicate the C's VALUE logic only."
           + "\n// The C return is converted through its declared return type:"
           + "\n// for an unsigned return, normalize (e.g. `(v as u32) as i64`)."
           + "\n// Args: " + "; ".join(argdoc))

    return {"rec": rec, "csrc": csrc, "surface": surface, "rs_sig": rs_sig,
            "doc": doc, "cells": cells, "nstate": nstate, "widx": widx,
            "rounds": rounds, "nparams": len(rec["params"]),
            "flags": rec["flags"]}


def _probe_c(prep):
    npar = prep["nparams"]
    ns = prep["nstate"]
    argdecl = ", ".join(["long"] * npar) if npar else "void"
    lines = [
        "#include <stdio.h>",
        "extern void eff_reset(void); extern void rs_reset(void);",
        "extern void eff_set(int, long); extern void rs_set(int, long);",
        "extern void eff_state(long*); extern void rs_state(long*);",
        f"extern long eff_call({argdecl}); extern long rs_call({argdecl});",
        f"static const int WIDX[{len(prep['widx'])}] = {{ "
        + ", ".join(map(str, prep["widx"])) + " };",
        f"static long CB[{ns}], RB[{ns}], PREV[{ns}];",
        f"static int CHANGED[{ns}];",
        "int main(void){",
    ]
    k = 0
    for rnd in prep["rounds"]:
        lines.append("    eff_reset(); rs_reset();")
        for ix, v in rnd["seeds"]:
            lines.append(f"    eff_set({ix},{v}); rs_set({ix},{v});")
        for row in rnd["calls"]:
            args = ", ".join(str(v) for v in row)
            lines.append(f"""    eff_state(PREV);
    {{ long rc = eff_call({args}); long rr = rs_call({args});
      if (rc != rr) {{ printf("EFFTRACE verdict=DIVERGE:ret call={k} c=%ld r=%ld\\n", rc, rr); return 1; }}
      eff_state(CB); rs_state(RB);
      for (int i = 0; i < {ns}; i++) {{
        if (CB[i] != PREV[i]) CHANGED[i] = 1;
        if (CB[i] != RB[i]) {{
          printf("EFFTRACE verdict=DIVERGE:state call={k} cell=%d c=%ld r=%ld\\n", i, CB[i], RB[i]);
          return 1;
        }}
      }}
    }}""")
            k += 1
    lines.append(f"""    for (unsigned j = 0; j < {len(prep['widx'])}; j++)
        if (!CHANGED[WIDX[j]]) {{
            printf("EFFTRACE verdict=REFUSED_COVERAGE cell=%d\\n", WIDX[j]);
            return 2;
        }}
    printf("EFFTRACE verdict=MATCH calls={k}\\n");
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
    d = workdir or tempfile.mkdtemp(prefix="eff_")
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
# self-check: rb_set_black (lib/rbtree.c — REAL rbtree internals), hand bodies
# ---------------------------------------------------------------------------

_CANON = ("lib/rbtree.c", "rb_set_black")

_CANON_BODIES = {
    # rb->__rb_parent_color += RB_BLACK  (NOT |= — the differential caught the
    # plausible OR-translation live: in-tree they agree because rb_set_black is
    # only called on red nodes, but as state-transition functions they differ
    # on the second application to the same node. That catch is the oracle.)
    "correct": """
    set_field(F0___RB_PARENT_COLOR, a0, field(F0___RB_PARENT_COLOR, a0) + RB_BLACK);
    0
""",
    # THE over-credit case: same (void) return, state untouched -> a return-only
    # oracle says MATCH; the state differential must say DIVERGE:state.
    "over_credit": """
    0
""",
    # the plausible-but-wrong idiom (|=): agrees on first touch, diverges after
    "or_not_add": """
    set_field(F0___RB_PARENT_COLOR, a0, field(F0___RB_PARENT_COLOR, a0) | RB_BLACK);
    0
""",
}


def main():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "eff_reach_hn", os.path.join(HERE, "reach.py"))
    reach = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reach)
    rec = reach.gate(*_CANON)
    prep = prepare(rec)
    print(f"=== effect-trace harness self-check: {rec['fn']} ({rec['file']}) ===")
    print(f"  cells={prep['nstate']} write-targets={prep['widx']} flags={prep['flags']}")
    expect = {"correct": "MATCH", "over_credit": "DIVERGE:state",
              "or_not_add": "DIVERGE:state"}
    ok = True
    for name, body in _CANON_BODIES.items():
        r = close(prep, body)
        good = r["verdict"] == expect[name]
        ok &= good
        mark = "✓" if good else "✗ UNEXPECTED"
        print(f"  {mark}  {name:11s} -> {r['verdict']}   [{r['out'][:70]}]")
        if not good:
            print(f"      dir={r['dir']}")
    # coverage negative control: a workload that never calls the fn leaves the
    # write targets un-exercised — even the CORRECT body must not certify.
    import copy
    starved = copy.deepcopy(prep)
    starved["rounds"] = [{"seeds": [], "calls": []}]
    r = close(starved, _CANON_BODIES["correct"])
    good = r["verdict"] == "REFUSED_COVERAGE"
    ok &= good
    print(f"  {'✓' if good else '✗ UNEXPECTED'}  unwritten-target(correct body) -> "
          f"{r['verdict']}   (an un-exercised write target can never certify)")
    print("PRODUCTIZED ORACLE:", "PASS — real kernel fn, per-call full-footprint "
          "state differential, over-credit caught" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
