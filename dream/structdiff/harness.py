#!/usr/bin/env python3
"""Real struct-driven branch harness: close a pure struct-reader function by a
mirror differential with path coverage, boot-free (host cc + rustc).

Pipeline (front gate in reach.py must already accept the function):
  1. Mirror the struct-pointer parameter(s) -> #[repr(C)] Rust + a host C struct
     of identical layout (emitted from the mirror rows, so C-ref and Rust
     candidate observe the SAME bytes).
  2. Re-emit the C REFERENCE from cfg.py's block tree with branch-coverage
     instrumentation: straight-line statements verbatim (their own source text),
     each `if` wrapped so BOTH arms set a coverage flag (an implicit else is
     added when the source has none). The logic is the real kernel function's.
  3. Emit a differential probe: sweep the struct's scalar fields + scalar params,
     call C-ref and candidate on SEPARATE copies of each case, and compare the
     return value, any out-param, AND the (possibly mutated) struct bytes.
  4. PATH-COVERAGE gate: REFUSE unless every branch arm was exercised (a shared
     blind spot is not a sound close). Compile + run; report MATCH/DIVERGE/REFUSE.

A wrong candidate reads the same bytes but computes a different result -> DIVERGE.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "cluster"))
sys.path.insert(0, os.path.join(HERE, "..", "mmiogen"))
sys.path.insert(0, os.path.join(HERE, "..", "mirror"))
import cluster   # noqa: E402
import cfg       # noqa: E402
import mirror    # noqa: E402
import reach     # noqa: E402

KSRC = reach.KSRC

# host C type of identical size/align for each mirror rust field type
_RS2C = {"i8": "signed char", "u8": "unsigned char", "bool": "unsigned char",
         "i16": "short", "u16": "unsigned short", "i32": "int", "u32": "unsigned",
         "i64": "long long", "u64": "unsigned long long",
         "isize": "long long", "usize": "unsigned long long"}

# minimal host prelude so an extracted kernel function compiles standalone.
PRELUDE = r"""
#include <stdint.h>
#include <string.h>
#include <stdbool.h>
typedef unsigned char u8; typedef signed char s8;
typedef unsigned short u16; typedef short s16;
typedef unsigned u32; typedef int s32;
typedef unsigned long long u64; typedef long long s64;
typedef unsigned long long resource_size_t, phys_addr_t, dma_addr_t;
typedef unsigned long long size_t_k;
#define EINVAL 22
#define ENOSPC 28
#define EBUSY 16
#define ENOMEM 12
#define EExx 0
"""

_SCALAR_RS = set(_RS2C)


class Refused(Exception):
    pass


def _c_field(rty: str, name: str) -> str:
    """One C struct member of the same size/align as a mirror field type."""
    if rty in _RS2C:
        return f"{_RS2C[rty]} {name};"
    if rty.startswith("*"):                        # pointer
        return f"void *{name};"
    m = re.match(r"\[(\w+); (\d+)\]$", rty)         # array [elem; N]
    if m and m.group(1) in _RS2C:
        return f"{_RS2C[m.group(1)]} {name}[{m.group(2)}];"
    raise Refused(f"field {name}: unmappable mirror type {rty!r}")


def emit_struct_c(name: str, m: dict) -> str:
    body = "\n    ".join(_c_field(r, f) for r, f, _ in m["fields"])
    return f"struct {name} {{\n    {body}\n}};"


# ---- signature / parameter classification ----------------------------------

def _split_params(sig_params: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in sig_params:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            out.append(cur.strip()); cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def classify_params(sig_params: str) -> list[dict]:
    """Each param -> {name, kind: struct|scalar|outptr, ctype/struct, const}."""
    params = []
    for p in _split_params(sig_params):
        if p in ("void", ""):
            continue
        sm = re.match(r"(const\s+)?struct\s+(\w+)\s*\*+\s*(\w+)$", p)
        if sm:
            params.append({"name": sm.group(3), "kind": "struct",
                           "struct": sm.group(2), "const": bool(sm.group(1))})
            continue
        pm = re.match(r"(?:const\s+)?(.+?)\s*\*+\s*(\w+)$", p)  # scalar pointer -> out-param
        if pm:
            params.append({"name": pm.group(2), "kind": "outptr",
                           "ctype": mirror.norm(pm.group(1))})
            continue
        vm = re.match(r"(?:const\s+)?(.+?\b)(\w+)$", p)         # scalar input
        if vm:
            params.append({"name": vm.group(2), "kind": "scalar",
                           "ctype": mirror.norm(vm.group(1))})
            continue
        raise Refused(f"unclassifiable param {p!r}")
    return params


# ---- C reference re-emission with branch-coverage instrumentation ----------

def _emit_block(blk: "cfg.Block", ctr: list[int], out: list[str]) -> None:
    for it in blk.items:
        if isinstance(it, cfg.Branch):
            k = ctr[0]; ctr[0] += 1
            out.append(f"if ({it.cond}) {{ __cov[{2*k}] = 1;")
            _emit_block(it.then_blk, ctr, out)
            out.append(f"}} else {{ __cov[{2*k+1}] = 1;")
            if it.else_blk is not None:
                _emit_block(it.else_blk, ctr, out)
            out.append("}")
        else:
            out.append(it.text.decode())


def instrument(func_text: str) -> tuple[str, int, str, str]:
    """Return (instrumented_C_function, n_cov_flags, sig_params, fn_name)."""
    fn = cfg.parse_function(func_text)
    body = fn.child_by_field_name("body")
    # signature = everything before the body's opening brace, with storage-class
    # / inline markers stripped so the re-emitted C ref is an EXTERN symbol the
    # probe can link (kernel helpers are often `static inline`).
    sig = func_text[:body.start_byte].strip()
    sig = re.sub(r"\b(static|inline|__always_inline|__maybe_unused|__init|__exit|"
                 r"__cold|notrace|noinline|__flatten)\b", " ", sig).strip()
    sig = re.sub(r"\s+", " ", sig)
    mname = re.search(r"(\w+)\s*\(", sig)
    fn_name = mname.group(1)
    op = sig.find("(")
    depth, i = 0, op
    while i < len(sig):
        depth += (sig[i] == "(") - (sig[i] == ")")
        if depth == 0:
            break
        i += 1
    sig_params = sig[op + 1:i]
    blk = cfg.build(func_text)
    ctr = [0]
    lines: list[str] = []
    _emit_block(blk, ctr, lines)
    ncov = 2 * ctr[0]
    instr = sig + " {\n    " + "\n    ".join(lines) + "\n}"
    return instr, ncov, sig_params, fn_name


# ---- probe generation ------------------------------------------------------

_SWEEP_BASE = [0, 1, 2, 5, -1]           # values probed per scalar field/param


def sweep_values(func_text: str) -> list[int]:
    """Base sweep plus every integer literal appearing in the function's branch
    conditions (and lit±1) — so a condition like `x != -1` or `mode > 2` is
    actually driven to BOTH sides; otherwise the path-coverage gate refuses."""
    vals = set(_SWEEP_BASE)
    for lit in re.findall(r"-?\b0x[0-9a-fA-F]+\b|-?\b\d+\b", func_text):
        try:
            n = int(lit, 0)
        except ValueError:
            continue
        if abs(n) <= (1 << 40):
            vals.update((n, n + 1, n - 1))
    return sorted(vals)


def _scalar_struct_fields(m: dict) -> list[tuple[str, str]]:
    return [(f, r) for r, f, _ in m["fields"] if r in _SCALAR_RS]


def build_probe(fn_name, sig_params, params, structs, ncov, sweep) -> str:
    """structs: {param_name: (struct_name, mirror_dict)}."""
    L = [PRELUDE, "#include <stdio.h>",
         f"unsigned char __cov[{ncov}];",
         "/* struct defs (host layout == mirror) */"]
    emitted = set()
    for pn, (sn, m) in structs.items():
        if sn not in emitted:
            L.append(emit_struct_c(sn, m)); emitted.add(sn)
    # decls of the two functions
    proto = ", ".join(_param_ctype(p, structs) for p in params)
    L.append(f"extern {_ret_ctype(sig_params, fn_name)} {fn_name}({proto});")
    L.append(f"extern {_ret_ctype(sig_params, fn_name)} {fn_name}_rs({proto});")
    # sweep loops
    L.append("int main(void){ unsigned long long cases=0,bad=0; int fb=0;")
    loops, callargs_c, callargs_g, post = [], [], [], []
    idx = 0
    for p in params:
        n = p["name"]
        if p["kind"] == "struct":
            sn, m = structs[n]
            L.append(f"  struct {sn} {n}_c, {n}_g;")
            for fld, rty in _scalar_struct_fields(m):
                v = f"{n}__{fld}"
                loops.append((v, sweep))
            callargs_c.append(f"&{n}_c"); callargs_g.append(f"&{n}_g")
        elif p["kind"] == "scalar":
            loops.append((n, sweep))
            callargs_c.append(n); callargs_g.append(n)
        elif p["kind"] == "outptr":
            L.append(f"  {p['ctype']} {n}_c, {n}_g;")
            callargs_c.append(f"&{n}_c"); callargs_g.append(f"&{n}_g")
            post.append((f"{n}_c", f"{n}_g"))
    # emit nested loops (signed long long so negatives like -1 sweep correctly)
    for v, vals in loops:
        arr = ", ".join(f"{x}ll" for x in vals)
        L.append(f"  static const long long {v}_S[] = {{{arr}}};")
    open_for = ""
    for v, vals in loops:
        open_for += f"  for (unsigned {v}_i=0; {v}_i<sizeof({v}_S)/sizeof({v}_S[0]); {v}_i++)\n"
    L.append(open_for + "  {")
    # bind scalars/struct fields for this case
    for p in params:
        n = p["name"]
        if p["kind"] == "struct":
            sn, m = structs[n]
            L.append(f"    memset(&{n}_c,0,sizeof {n}_c);")
            for fld, rty in _scalar_struct_fields(m):
                v = f"{n}__{fld}"
                L.append(f"    {n}_c.{fld} = ({_RS2C[rty]}){v}_S[{v}_i];")
            L.append(f"    {n}_g = {n}_c;")
        elif p["kind"] == "scalar":
            ct = p["ctype"]
            L.append(f"    {ct} {n} = ({ct}){n}_S[{n}_i];")
    rc = _ret_ctype(sig_params, fn_name)
    if rc == "void":  # a mutator: no return to assign/compare — struct-byte +
        L.append(f"    {fn_name}({', '.join(callargs_c)});")   # out-param diffs carry it
        L.append(f"    {fn_name}_rs({', '.join(callargs_g)});")
        conds = []
    else:
        L.append(f"    {rc} rc_c = {fn_name}({', '.join(callargs_c)});")
        L.append(f"    {rc} rc_g = {fn_name}_rs({', '.join(callargs_g)});")
        conds = ["rc_c != rc_g"]
    for a, b in post:
        conds.append(f"{a} != {b}")
    for p in params:  # non-const struct may be mutated -> compare bytes
        if p["kind"] == "struct" and not p["const"]:
            n = p["name"]; sn, _ = structs[n]
            conds.append(f"memcmp(&{n}_c,&{n}_g,sizeof(struct {sn}))")
    # empty conds (a void fn with no out-param / no mutated struct) is unobservable
    # -> fail-safe to DIVERGE, never a vacuous MATCH.
    cond_expr = " || ".join(conds) if conds else "1"
    L.append(f"    cases++; if (({cond_expr}) && bad++==0) fb=1;")
    L.append("  }")
    L.append(f"  int uncov=0; for(int i=0;i<{ncov};i++) if(!__cov[i]){{printf(\"  cov[%d] UNCOVERED\\n\",i);uncov++;}}")
    L.append('  if (uncov) { printf("STRUCTDIFF verdict=REFUSE (path coverage)\\n"); return 2; }')
    L.append('  printf("STRUCTDIFF cases=%llu bad=%llu verdict=%s\\n", cases, bad, bad?"DIVERGE":"MATCH");')
    L.append("  return bad?1:0; }")
    return "\n".join(L)


def _param_ctype(p, structs) -> str:
    if p["kind"] == "struct":
        return f"{'const ' if p['const'] else ''}struct {p['struct']} *{p['name']}"
    if p["kind"] == "outptr":
        return f"{p['ctype']} *{p['name']}"
    return f"{p['ctype']} {p['name']}"


_RET_CACHE = {}


def _ret_ctype(sig_params, fn_name) -> str:
    return _RET_CACHE.get(fn_name, "int")


# ---- top-level close -------------------------------------------------------

def _rust_scalar(ctype: str) -> str:
    return mirror.SCALAR.get(mirror.norm(ctype), ("i64", 0, 0))[0]


def prepare(rel: str, fn: str) -> dict:
    """Everything a synthesizer needs to write a candidate the gate will accept:
    the #[repr(C)] mirror struct def(s), the EXACT required Rust signature (the
    model writes only the body), the C source, and the return type. Raises on an
    unmirrorable struct (so the function is skipped, not mis-synthesized)."""
    src = open(os.path.join(KSRC, rel), errors="ignore").read()
    ftext = cluster.functions(src)[fn]["text"]
    ret_c = mirror.norm(re.sub(r"\b(static|inline|__always_inline|__maybe_unused)\b",
                               " ", ftext[:ftext.find(fn)])).strip() or "int"
    _RET_CACHE[fn] = ret_c
    _instr, _ncov, sig_params, _fn = instrument(ftext)
    params = classify_params(sig_params)
    structs, mdefs = {}, []
    for p in params:
        if p["kind"] == "struct":
            near = os.path.join(KSRC, rel)
            ssrc = mirror.resolve_struct_source(p["struct"], near_file=near) or src
            m = mirror.mirror(ssrc, p["struct"], near_file=near)
            structs[p["name"]] = (p["struct"], m)
            mdefs.append(m["rust"])
    rp = []
    for p in params:
        if p["kind"] == "struct":
            # *mut when the C param is non-const (a mutator writes fields); *const
            # for a read-only param. Must match the C ABI the probe passes.
            ptr = "*const" if p.get("const") else "*mut"
            rp.append(f'{p["name"]}: {ptr} {structs[p["name"]][1]["rust_type"]}')
        elif p["kind"] == "outptr":
            rp.append(f'{p["name"]}: *mut {_rust_scalar(p["ctype"])}')
        else:
            rp.append(f'{p["name"]}: {_rust_scalar(p["ctype"])}')
    rret = "" if ret_c == "void" else f" -> {_rust_scalar(ret_c)}"
    sig = f'#[no_mangle] pub extern "C" fn {fn}_rs({", ".join(rp)}){rret}'
    _all_struct_defs(structs)  # dry emittability check — raises on nested-struct /
    # nested-array fields the host C emitter can't map, so the caller skips the
    # function BEFORE spending any synth budget on a candidate that can never gate.
    return {"mirror_rust": "\n".join(mdefs), "sig": sig, "csrc": ftext, "ret": ret_c}


def close(rel: str, fn: str, candidate_rs: str, workdir: str) -> tuple[str, str]:
    src = open(os.path.join(KSRC, rel), errors="ignore").read()
    ftext = cluster.functions(src)[fn]["text"]
    # return type (text before the function name in the signature)
    sig_head = ftext[:ftext.find(fn)]
    _RET_CACHE[fn] = mirror.norm(re.sub(r"\b(static|inline|__always_inline|__maybe_unused)\b",
                                        " ", sig_head)).strip() or "int"
    instr, ncov, sig_params, fn_name = instrument(ftext)
    params = classify_params(sig_params)
    structs = {}
    for p in params:
        if p["kind"] == "struct":
            near = os.path.join(KSRC, rel)
            ssrc = mirror.resolve_struct_source(p["struct"], near_file=near) or src
            structs[p["name"]] = (p["struct"], mirror.mirror(ssrc, p["struct"], near_file=near))
    os.makedirs(workdir, exist_ok=True)
    ref = PRELUDE + "extern unsigned char __cov[];\n" + _all_struct_defs(structs) + "\n" + instr
    open(os.path.join(workdir, "ref.c"), "w").write(ref)
    open(os.path.join(workdir, "cand.rs"), "w").write(candidate_rs)
    sweep = sweep_values(ftext)
    open(os.path.join(workdir, "probe.c"), "w").write(
        build_probe(fn_name, sig_params, params, structs, ncov, sweep))
    try:
        r = subprocess.run(["rustc", "--crate-type=staticlib", "-O", "-C", "overflow-checks=off",
                            os.path.join(workdir, "cand.rs"), "-o", os.path.join(workdir, "libcand.a")],
                           capture_output=True, text=True, cwd=workdir, timeout=90)
        if r.returncode:
            return "BUILD_FAIL(rust)", r.stderr
        r = subprocess.run(["cc", "-O2", os.path.join(workdir, "probe.c"), os.path.join(workdir, "ref.c"),
                            os.path.join(workdir, "libcand.a"), "-o", os.path.join(workdir, "run")],
                           capture_output=True, text=True, timeout=90)
        if r.returncode:
            return "BUILD_FAIL(c)", r.stderr
        r = subprocess.run([os.path.join(workdir, "run")], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "TIMEOUT", "compile/run exceeded timeout"
    out = r.stdout + r.stderr
    v = ("MATCH" if "verdict=MATCH" in out else "DIVERGE" if "verdict=DIVERGE" in out
         else "REFUSE" if "verdict=REFUSE" in out else "UNKNOWN")
    return v, out.strip()


def _all_struct_defs(structs) -> str:
    seen, defs = set(), []
    for pn, (sn, m) in structs.items():
        if sn not in seen:
            defs.append(emit_struct_c(sn, m)); seen.add(sn)
    return "\n".join(defs)


def main() -> int:
    import argparse
    import tempfile
    ap = argparse.ArgumentParser(description="close a pure struct-reader function")
    ap.add_argument("file", help="kernel source path relative to $KSRC")
    ap.add_argument("fn", help="function name")
    ap.add_argument("candidate", help="path to the Rust candidate (.rs)")
    ap.add_argument("--keep", metavar="DIR", help="keep generated files here")
    a = ap.parse_args()
    cand = open(a.candidate).read()
    wd = a.keep or tempfile.mkdtemp()
    v, out = close(a.file, a.fn, cand, wd)
    print(out)
    print(f"VERDICT: {v}")
    return 0 if v == "MATCH" else 1


if __name__ == "__main__":
    raise SystemExit(main())
