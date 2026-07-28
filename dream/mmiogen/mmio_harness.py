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


# base pointer as `<var>->base`, `<var>->regs`, `<var>->gpio_io`, ...
BASE = r"\w+->(?:base|regs|reg_base|membase|gpio_io)"
RD = re.compile(rf"readl\s*\(\s*{BASE}\s*\+\s*(\w+)\s*\)")
WR = re.compile(rf"writel\s*\(\s*(.+?)\s*,\s*{BASE}\s*\+\s*(\w+)\s*\)")
INPUT_CALL = re.compile(r"irqd_to_hwirq\s*\([^)]*\)|\bhwirq\b|\boffset\b")
OUT_OF_TRACE = re.compile(r"\b(gpiochip_(?:enable|disable)_irq|gpiochip_(?:lock|unlock)_as_irq|"
                          r"raw_spin_(?:lock|unlock)\w*|spin_(?:lock|unlock)\w*|irq_chip_\w+)\s*\(")


CONTROL = re.compile(r"\b(if|else|for|while|switch|goto|do)\b")

# Scalar kernel local types (SSA temps). A declaration of one or more of these,
# with no register-touching initializer, is plumbing and is dropped. This is a
# strict allow-list: anything not matching stays an "unmodellable statement" and
# refuses, so we never silently drop something with an effect.
_SCALAR_TYPE = (r"(?:unsigned\s+(?:long|int|char|short)?|signed\s+(?:long|int|char|short)?|"
                r"long(?:\s+long)?|short|int|char|bool|"
                r"u8|u16|u32|u64|s8|s16|s32|s64|"
                r"irq_hw_number_t|size_t|__le32|__be32|uint\d+_t|int\d+_t)")
# `u32 a;`  |  `u32 a, b, c;`  |  `int off = <plain expr, no readl/writel>;`
SCALAR_DECL = re.compile(
    rf"^{_SCALAR_TYPE}\s+\**\s*\w+(?:\s*,\s*\**\s*\w+)*"
    r"(?:\s*=\s*(?![^;]*\b(?:readl|writel|ioread|iowrite)\w*\s*\()[^;]*)?$"
)

# base-alias local: `void __iomem *base = <base-expr>[ + <offset-expr>];`  (also
# a bare re-alias `base = other_base;`). We track <name> so a later
# `readl(<name> + OFF)` resolves against the real base. The added offset must be
# a resolvable #define constant (folded into the recorded offset) or absent; an
# opaque/computed added offset REFUSES rather than record a wrong offset.
IOMEM_ALIAS = re.compile(
    r"^(?:void\s+__iomem\s*\*|__iomem\s+void\s*\*)?\s*\**\s*(\w+)\s*=\s*(.+)$"
)


def extract(src: str, fn: str, defs: dict[str, int]) -> dict:
    body = func_body(src, fn)
    inner = body[body.index("{") + 1: body.rindex("}")]
    # Splitting on ';' has no statement grammar: `if (cond) writel(...);` would
    # extract as an UNCONDITIONAL write — and since both the C ref and the Rust
    # candidate are emitted from the same lossy program, a wrong extraction
    # replays identically against its own oracle and reports CLOSED. Any
    # control flow => refuse, never guess.
    masked = re.sub(r"/\*.*?\*/", " ", re.sub(r"//[^\n]*", "", inner), flags=re.DOTALL)
    cm = CONTROL.search(masked)
    if cm:
        raise Unsupported(f"control flow `{cm.group(1)}` in body — ';'-split would erase it")
    # A nested brace `{...}` is a scope (block-guard like scoped_guard(...) {...},
    # a compound literal, an inline init) with NO control-flow keyword. The
    # ';'-split flattens it — the scope's opener glues onto the next statement and
    # the program becomes lossy. Keyword-less scopes are just as unmodellable as
    # control flow, so refuse rather than flatten.
    if "{" in masked or "}" in masked:
        raise Unsupported("nested brace scope in body — ';'-split would flatten it")
    # the statements after the struct prologue (drop lines that only plumb structs)
    stmts, out_of_trace = [], []
    # <alias-name> -> (base_regex_match_str, folded_offset:int, offset_name:str)
    # A base-alias local `x = g->base + OFF` records that `readl(x + OFF2)`
    # touches `g->base + (OFF + OFF2)`; only resolvable-constant folds are kept.
    aliases: dict[str, int] = {}
    for raw in inner.split(";"):
        s = re.sub(r"//.*", "", raw).strip()
        if not s:
            continue
        # struct plumbing declarations — dropped (as before)
        if re.match(r"struct\s+\w+\s*\*?\s*\w+\s*=", s) or "gpiochip_get_data" in s or \
           "irq_data_get" in s:
            continue
        # A base-alias local: recognise `<name> = <base-expr>[ + <offset>]` where
        # the RHS is a known register base (optionally + a resolvable const). We
        # must test this BEFORE the scalar-decl drop so `void __iomem *base = ...`
        # is tracked, not silently discarded.
        am = _match_base_alias(s, defs)
        if am is not None:
            name, folded = am
            aliases[name] = folded
            continue
        # A scalar-local declaration with no register-touching initializer is an
        # SSA temp — drop it. Multiple comma-separated names are allowed.
        if SCALAR_DECL.match(s):
            continue
        om = OUT_OF_TRACE.search(s)
        if om:
            out_of_trace.append(om.group(1))
            continue
        stmts.append(s)

    # every access must be the clean base+OFFSET shape; collect the program
    prog = []
    for s in stmts:
        if "readl" in s:
            off_name, off_val = _resolve_access(s, "readl", defs, aliases)
            lhs = s.split("=")[0].strip() if "=" in s else "val"
            prog.append(("R", off_val, off_name, lhs))
        elif "writel" in s:
            off_name, off_val = _resolve_access(s, "writel", defs, aliases)
            wm = WR_ALIAS.search(s)
            prog.append(("W", off_val, off_name, wm.group(1).strip()))
        elif re.match(r"\w+\s*(&=|\|=|\^=|=)", s):
            prog.append(("C", None, None, s))   # a compute on val
        else:
            raise Unsupported(f"unmodellable statement: {s!r}")
    if not any(p[0] in ("R", "W") for p in prog):
        raise Unsupported("no MMIO accesses found")
    return {"fn": fn, "program": prog, "out_of_trace": out_of_trace,
            "regs": {p[2]: p[1] for p in prog if p[1] is not None}}


# base-or-alias access: `readl(<base|alias> + OFF)`; `<alias>` need not be `x->y`.
RD_ALIAS = re.compile(r"readl\s*\(\s*([\w>-]+)\s*\+\s*(\w+)\s*\)")
WR_ALIAS = re.compile(r"writel\s*\(\s*(.+?)\s*,\s*([\w>-]+)\s*\+\s*(\w+)\s*\)")


def _match_base_alias(s: str, defs: dict[str, int]):
    """If `s` declares/assigns a base-alias local, return (name, folded_offset).
    `<name> = <base-expr>` folds 0; `<name> = <base-expr> + CONST` folds CONST
    (CONST must be a resolvable #define). An opaque added offset REFUSES; a RHS
    that isn't a known register base is not an alias (returns None -> other paths
    handle it)."""
    m = IOMEM_ALIAS.match(s)
    if not m:
        return None
    name, rhs = m.group(1), m.group(2).strip()
    # RHS must START with a register base (`x->base` etc.), else it's not a base
    # alias we can reason about (e.g. `val = readl(...)` — handled elsewhere).
    bm = re.match(rf"^({BASE})\s*(?:\+\s*(.+))?$", rhs)
    if not bm:
        return None
    added = bm.group(2)
    if added is None:
        return name, 0
    added = added.strip()
    if added in defs:
        return name, defs[added]
    # An opaque/computed added offset (a helper call, arithmetic on the pin, an
    # unresolved macro): recording base+0 would be a WRONG trace — refuse.
    raise Unsupported(f"base-alias with unresolvable offset: {s!r}")


def _resolve_access(s: str, op: str, defs: dict[str, int], aliases: dict[str, int]):
    """Resolve a `readl/writel(<target> + OFF)` to (offset_name, offset_value).
    <target> is either a register base (`x->base`) or a tracked base-alias. For
    an alias with a folded prologue offset, the recorded offset is the SUM, and
    the recorded name is synthesised so the emitted #define is faithful."""
    rx = RD_ALIAS if op == "readl" else WR_ALIAS
    m = rx.search(s)
    if not m:
        raise Unsupported(f"non-clean {op}: {s!r}")
    target = m.group(1) if op == "readl" else m.group(2)
    off_name = m.group(2) if op == "readl" else m.group(3)
    if off_name not in defs:
        raise Unsupported(f"non-clean {op}: {s!r}")
    off_val = defs[off_name]
    # direct base access: target must be a real register base
    if re.fullmatch(BASE, target):
        return off_name, off_val
    # else it must be a tracked base-alias local
    if target in aliases:
        base_off = aliases[target]
        if base_off == 0:
            return off_name, off_val
        # fold the prologue offset into the recorded offset; synthesise a valid-C
        # name so the emitted #define reflects the true absolute offset
        return f"{off_name}_PLUS_{base_off:x}", base_off + off_val
    raise Unsupported(f"non-clean {op} (unknown base/alias {target!r}): {s!r}")


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
