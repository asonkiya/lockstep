#!/usr/bin/env python3
"""The per-driver MMIO-record harness generator — close the T3_TRACE bucket.

The router routes driver register functions to T3_TRACE (the recorder). The
recorder's *mechanism* was proven; what was missing is the per-driver harness:
the seam-adapted C reference, the register model, and the probe. This generator
produces all of it AUTOMATICALLY from a real in-tree driver function, so a
T3_TRACE routee goes from "owes a recording" to "verified against its own C by
its register trace" with no hand-scaffolding.

Pipeline for one function:
  1. EXTRACT the MMIO program: the ordered readl/writel accesses, their register
     offsets (resolved from the file's #defines), and value expressions,
     parameterized by the pin/hwirq input. Non-MMIO effect calls
     (gpiochip_*_irq bookkeeping) are noted as OUT-OF-TRACE, not silently
     dropped — the honest edge of what the recorder covers.
  2. SEAM-ADAPT to a self-contained C ref: struct plumbing removed, input a plain
     u32, readl/writel -> reg_read/reg_write (the Ring 3/4 seam), BIT()->shift.
  3. EMIT a candidate transplant from the same skeleton (the register program in
     Rust) and a NEGATIVE CONTROL (one offset mutated).
  4. RECORD the C ref against the RAM model, REPLAY each candidate: correct ->
     MATCH, mutant -> DIVERGE on the trace. Non-vacuous by construction.

Refuses (honestly) any function whose accesses aren't the clean
`readl/writel(base + OFFSET)` shape — helper-wrapped accessors, computed
offsets, banks — exactly as the mirror generator refuses entangled structs.

Usage: mmio_harness.py <driver.c> <fn> [--out DIR]   (paths under $KSRC)
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")


class Unsupported(Exception):
    pass


def resolve_defines(src: str) -> dict[str, int]:
    out = {}
    for m in re.finditer(r"#define\s+(\w+)\s+(0x[0-9a-fA-F]+|\d+)", src):
        try:
            out[m.group(1)] = int(m.group(2), 0)
        except ValueError:
            pass
    return out


def func_body(src: str, fn: str) -> str:
    m = re.search(rf"(?:static\s+)?[\w \t\*]+\b{re.escape(fn)}\s*\([^;{{]*\)\s*\{{", src)
    if not m:
        raise Unsupported(f"function {fn} not found")
    depth, i = 0, m.end() - 1
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start(): i + 1]
        i += 1
    raise Unsupported("unbalanced body")


# base pointer as `<var>->base` or `<var>->regs`
BASE = r"\w+->(?:base|regs|reg_base|membase)"
RD = re.compile(rf"readl\s*\(\s*{BASE}\s*\+\s*(\w+)\s*\)")
WR = re.compile(rf"writel\s*\(\s*(.+?)\s*,\s*{BASE}\s*\+\s*(\w+)\s*\)")
INPUT_CALL = re.compile(r"irqd_to_hwirq\s*\([^)]*\)|\bhwirq\b|\boffset\b")
OUT_OF_TRACE = re.compile(r"\b(gpiochip_(?:enable|disable)_irq|gpiochip_(?:lock|unlock)_as_irq|"
                          r"raw_spin_(?:lock|unlock)\w*|spin_(?:lock|unlock)\w*|irq_chip_\w+)\s*\(")


def extract(src: str, fn: str, defs: dict[str, int]) -> dict:
    body = func_body(src, fn)
    inner = body[body.index("{") + 1: body.rindex("}")]
    # the statements after the struct prologue (drop lines that only plumb structs)
    stmts, out_of_trace = [], []
    for raw in inner.split(";"):
        s = re.sub(r"//.*", "", raw).strip()
        if not s:
            continue
        if re.match(r"struct\s+\w+\s*\*?\s*\w+\s*=", s) or "gpiochip_get_data" in s or \
           "irq_data_get" in s or re.match(r"u32\s+\w+$", s) or re.match(r"unsigned.*\bval$", s):
            continue  # struct/local declarations — plumbing
        om = OUT_OF_TRACE.search(s)
        if om:
            out_of_trace.append(om.group(1))
            continue
        stmts.append(s)

    # every access must be the clean base+OFFSET shape; collect the program
    prog = []
    for s in stmts:
        if "readl" in s:
            rm = RD.search(s)
            if not rm or rm.group(1) not in defs:
                raise Unsupported(f"non-clean read: {s!r}")
            lhs = s.split("=")[0].strip() if "=" in s else "val"
            prog.append(("R", defs[rm.group(1)], rm.group(1), lhs))
        elif "writel" in s:
            wm = WR.search(s)
            if not wm or wm.group(2) not in defs:
                raise Unsupported(f"non-clean write: {s!r}")
            prog.append(("W", defs[wm.group(2)], wm.group(2), wm.group(1).strip()))
        elif re.match(r"\w+\s*(&=|\|=|\^=|=)", s):
            prog.append(("C", None, None, s))   # a compute on val
        else:
            raise Unsupported(f"unmodellable statement: {s!r}")
    if not any(p[0] in ("R", "W") for p in prog):
        raise Unsupported("no MMIO accesses found")
    return {"fn": fn, "program": prog, "out_of_trace": out_of_trace,
            "regs": {p[2]: p[1] for p in prog if p[1] is not None}}


def _expr_c(e: str) -> str:
    """value expr -> C over the pin input `p` (BIT(hwirq)->(1u<<p))."""
    e = re.sub(r"BIT\s*\(\s*irqd_to_hwirq\([^)]*\)\s*\)", "(1u<<p)", e)
    e = re.sub(r"irqd_to_hwirq\s*\([^)]*\)", "p", e)
    e = re.sub(r"BIT\s*\(\s*(\w+)\s*\)", r"(1u<<(\1))", e)
    return e


def emit_ref_c(ex: dict) -> str:
    # the engine (state + reg_read/reg_write) lives ONLY in the probe TU so the
    # trace is shared; the ref just declares the seam extern
    lines = ["#include <stdint.h>",
             "extern uint32_t reg_read(uint32_t off);",
             "extern void reg_write(uint32_t off, uint32_t val);"]
    for n, v in sorted(ex["regs"].items(), key=lambda x: x[1]):
        lines.append(f"#define {n} 0x{v:x}u")
    lines += ["", f"void {ex['fn']}_ref(uint32_t p)", "{", "\tuint32_t val = 0; (void)val;"]
    for kind, off, name, expr in ex["program"]:
        if kind == "R":
            lines.append(f"\t{_expr_c(expr)} = reg_read({name});")
        elif kind == "W":
            lines.append(f"\treg_write({name}, {_expr_c(expr)});")
        else:  # compute
            lines.append(f"\t{_expr_c(expr)};")
    lines.append("}")
    return "\n".join(lines)


def _reg_consts_rs(ex: dict) -> str:
    return "\n".join(f"const {n}: u32 = 0x{v:x};" for n, v in sorted(ex["regs"].items(), key=lambda x: x[1]))


def emit_cand_rs(ex: dict, mutate: bool = False) -> str:
    """The register program in Rust (the candidate), or a mutated negative control
    (one write offset bumped) that returns the SAME nothing but a WRONG trace."""
    regs = dict(ex["regs"])
    L = ['extern "C" { fn reg_read(off: u32) -> u32; fn reg_write(off: u32, val: u32); }',
         _reg_consts_rs(ex), "",
         f'#[no_mangle]\npub extern "C" fn cgir_{ex["fn"]}(p: u32) {{',
         "    let mut val: u32 = 0; let _ = val;"]
    w_seen = 0
    for kind, off, name, expr in ex["program"]:
        e = _expr_rs(expr)
        if kind == "R":
            L.append(f"    unsafe {{ val = reg_read({name}); }}")
        elif kind == "W":
            tgt = name
            if mutate and w_seen == 0:
                tgt = f"({name} ^ 0x4)"   # BUG: write the wrong register offset
            L.append(f"    unsafe {{ reg_write({tgt}, {e}); }}")
            w_seen += 1
        else:
            L.append(f"    {e};")
    L.append("}")
    return "\n".join(L)


def _expr_rs(e: str) -> str:
    e = re.sub(r"BIT\s*\(\s*irqd_to_hwirq\([^)]*\)\s*\)", "(1u32<<p)", e)
    e = re.sub(r"irqd_to_hwirq\s*\([^)]*\)", "p", e)
    e = re.sub(r"BIT\s*\(\s*(\w+)\s*\)", r"(1u32<<(\1))", e)
    e = e.replace("~", "!")            # C bitwise-not -> Rust !
    e = re.sub(r"\bval\b", "val", e)
    return e


def emit_probe(ex: dict) -> str:
    return f"""#include <stdio.h>
#include "record_engine.h"
void {ex['fn']}_ref(uint32_t p);
extern void cgir_{ex['fn']}(uint32_t p);
int main(void) {{
    unsigned cases=0, bad=0; long fb=-1; int dp=-1;
    for (uint32_t p=0; p<32; p++) {{
        model_seed();
        mode=RECORD; tn=0; {ex['fn']}_ref(p); int rl=tn;
        mode=REPLAY; tpos=0; diverged=0; div_pos=-1; cgir_{ex['fn']}(p);
        int consumed=(tpos==rl); cases++;
        if (diverged || !consumed) {{ bad++; if(fb<0){{fb=p; dp=diverged?div_pos:tpos;}} }}
    }}
    printf("MMIOGEN: {ex['fn']} cases=%u bad=%u firstbad=%ld div_at=%d verdict=%s\\n",
           cases,bad,fb,dp, bad?"DIVERGE":"MATCH");
    return bad?1:0;
}}"""


def generate(driver: str, fn: str, out: str) -> dict:
    src = open(os.path.join(KSRC, driver), errors="ignore").read()
    defs = resolve_defines(src)
    ex = extract(src, fn, defs)
    os.makedirs(out, exist_ok=True)
    for name in ("record_engine.h",):
        subprocess.run(["cp", os.path.join(HERE, name), out], check=True)
    open(f"{out}/{fn}_ref.c", "w").write(emit_ref_c(ex))
    open(f"{out}/{fn}_cand.rs", "w").write(emit_cand_rs(ex, mutate=False))
    open(f"{out}/{fn}_bad.rs", "w").write(emit_cand_rs(ex, mutate=True))
    open(f"{out}/{fn}_probe.c", "w").write(emit_probe(ex))
    return ex


def gate(fn: str, out: str, cand: str) -> tuple[str, str]:
    rlib = f"{out}/lib{fn}.a"
    r = subprocess.run(["rustc", "--edition", "2021", "-O", "--crate-type=staticlib",
                        f"{out}/{cand}", "-o", rlib], capture_output=True, text=True)
    if r.returncode:
        return "RUSTC_FAIL", r.stderr[-300:]
    b = subprocess.run(["cc", "-O2", f"-I{out}", f"{out}/{fn}_probe.c", f"{out}/{fn}_ref.c",
                        rlib, "-o", f"{out}/{fn}_gate"], capture_output=True, text=True)
    if b.returncode:
        return "LINK_FAIL", b.stderr[-300:]
    q = subprocess.run([f"{out}/{fn}_gate"], capture_output=True, text=True)
    line = q.stdout.strip()
    return ("MATCH" if q.returncode == 0 else "DIVERGE"), line


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("driver")
    ap.add_argument("fn")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = a.out or f"/private/tmp/mmiogen_{a.fn}"
    try:
        ex = generate(a.driver, a.fn, out)
    except Unsupported as e:
        print(f"REFUSED {a.fn}: {e}")
        return 2
    prog = " ".join(f"{k}({n})" for k, _, n, _ in ex["program"] if n)
    print(f"[mmiogen] {a.fn}: extracted MMIO program = [{prog}]")
    if ex["out_of_trace"]:
        print(f"[mmiogen]   out-of-trace effects (not covered): {ex['out_of_trace']}")
    v1, l1 = gate(a.fn, out, f"{a.fn}_cand.rs")
    print(f"[mmiogen] correct : {l1}")
    v2, l2 = gate(a.fn, out, f"{a.fn}_bad.rs")
    print(f"[mmiogen] control : {l2}")
    ok = v1 == "MATCH" and v2 == "DIVERGE"
    print(f"MMIOGEN {a.fn}: {'PASS — recorder-verified against its C register trace; wrong-register control rejected' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
