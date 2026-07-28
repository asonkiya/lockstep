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


class CompOff:
    """A COMPUTED register offset: a pure-arithmetic expression of the harness pin
    `p` and resolvable #define constants (the xlp `regset = (gpio/REGSZ)*4` bank
    pattern). Unlike a constant offset it carries no #define — it is materialised
    directly in the emitted ref/candidate as an expression over `p`, and the RAM
    record engine records the actual per-pin runtime offset, so the trace stays
    faithful. `.c`/`.rs` are the already-translated (pin-substituted,
    const-resolved) offset expressions; `.disp` is a stable display name; `.mutant`
    is a genuinely-diverging perturbation for the negative control."""

    __slots__ = ("c", "rs", "disp")

    def __init__(self, c: str, rs: str, disp: str):
        self.c, self.rs, self.disp = c, rs, disp

    def __str__(self) -> str:
        return self.disp

    def __bool__(self) -> bool:
        return True


# Tokens legal inside a computed-offset / return expression once the pin variable
# has been substituted to `p` and #define constants resolved: the pin, integer
# literals, and the arithmetic/bitwise operators the spec permits. Anything else
# (a deref, an opaque call, an unresolved identifier) makes the expression
# unmodellable and MUST refuse — we never guess an offset or a value.
_OFF_OK_OPS = set("+-*/%<>&|^~()")
_OFF_TOKEN = re.compile(r"\s*(?:(0x[0-9a-fA-F]+|\d+)|([A-Za-z_]\w*)|(<<|>>|[-+*/%&|^~()])|(.))")


def _pure_arith_tokens(expr: str, pin: str, defs: dict[str, int]) -> list[str]:
    """Tokenise `expr` and verify EVERY token is the pin (-> `p`), a resolvable
    #define constant (-> its int), an integer literal, or a permitted operator.
    Returns the token list (with pin/const substituted). Any other token REFUSES —
    this is the whole soundness gate for computed offsets and return values."""
    toks: list[str] = []
    i = 0
    while i < len(expr):
        m = _OFF_TOKEN.match(expr, i)
        if not m:
            raise Unsupported(f"untokenisable expr fragment: {expr[i:]!r}")
        i = m.end()
        num, ident, op, other = m.group(1), m.group(2), m.group(3), m.group(4)
        if num is not None:
            toks.append(str(int(num, 0)))
        elif ident is not None:
            if ident == pin:
                toks.append("p")
            elif ident in defs:
                toks.append(str(defs[ident]))
            else:
                # not the pin, not a resolvable constant -> unmodellable identifier
                raise Unsupported(f"unresolvable identifier {ident!r} in expr {expr!r}")
        elif op is not None:
            toks.append(op)
        elif other is not None and other.strip():
            raise Unsupported(f"illegal token {other!r} in expr {expr!r}")
    if not toks:
        raise Unsupported(f"empty expr: {expr!r}")
    bad = [t for t in toks if not (t.isdigit() or t == "p" or t in _OFF_OK_OPS or t in ("<<", ">>"))]
    if bad:
        raise Unsupported(f"unmodellable tokens {bad} in expr {expr!r}")
    return toks


def _off_expr_c(toks: list[str]) -> str:
    # C is unsigned u32 arithmetic; wrap so it composes safely when embedded.
    return "(" + "".join(f" {t} " if t in _OFF_OK_OPS or t in ("<<", ">>") else t
                         for t in toks).strip() + ")"


def _off_expr_rs(toks: list[str]) -> str:
    # Rust: same operators; `~` -> `!`, literals get no suffix (they infer u32 in
    # context). Wrap for precedence, mirroring the C form exactly.
    out = []
    for t in toks:
        out.append("!" if t == "~" else t)
    return "(" + "".join(f" {t} " if (t in _OFF_OK_OPS or t in ("<<", ">>")) else t
                         for t in out).strip() + ")"


def resolve_defines(src: str) -> dict[str, int]:
    out = {}
    for m in re.finditer(r"#define\s+(\w+)\s+(0x[0-9a-fA-F]+|\d+)", src):
        try:
            out[m.group(1)] = int(m.group(2), 0)
        except ValueError:
            pass
    return out


def func_sig(src: str, fn: str) -> tuple[str, str]:
    """Return (return_type, param_list_text) for `fn`."""
    m = re.search(rf"((?:static\s+)?[\w \t\*]+?)\b{re.escape(fn)}\s*\(([^;{{]*)\)\s*\{{", src)
    if not m:
        raise Unsupported(f"function {fn} not found")
    return m.group(1).strip(), m.group(2).strip()


# a parameter declared `void __iomem *name` (in either order) — a bare iomem base.
_IOMEM_PARAM = re.compile(r"void\s+__iomem\s*\*\s*(\w+)|__iomem\s+void\s*\*\s*(\w+)")


def parse_params(param_text: str) -> tuple[list[str], str | None]:
    """Split a C parameter list into (iomem_base_names, pin_name). The pin is the
    FIRST integer-scalar parameter (gpio/offset/hwirq). Pointers other than the
    iomem base and struct params are ignored — only names we can reason about are
    surfaced; anything used later that isn't tracked will refuse downstream."""
    if param_text in ("", "void"):
        return [], None
    parts, depth, cur = [], 0, ""
    for ch in param_text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    iomem, pin = [], None
    for p in parts:
        p = p.strip()
        im = _IOMEM_PARAM.search(p)
        if im:
            iomem.append(im.group(1) or im.group(2))
            continue
        nm = _pin_name(p)
        if nm and pin is None:
            pin = nm
    return iomem, pin


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

# the pin/input parameter: an integer-typed scalar param (`unsigned gpio`,
# `int offset`, `u32 pin`, `irq_hw_number_t hwirq`, ...). The type is one or more
# scalar-type words; the trailing identifier is the parameter name.
_SCALAR_WORD = re.compile(
    r"^(?:unsigned|signed|long|short|int|char|bool|"
    r"u8|u16|u32|u64|s8|s16|s32|s64|"
    r"irq_hw_number_t|size_t|__le32|__be32|uint\d+_t|int\d+_t)$"
)


def _pin_name(param: str) -> str | None:
    """Return the parameter name iff `param` is a plain integer-scalar parameter
    (`unsigned gpio`), else None. Pointers/structs/qualified aggregates -> None."""
    p = param.replace("const", " ").strip()
    if "*" in p or "[" in p:
        return None
    words = p.split()
    if len(words) < 2:
        return None
    name = words[-1]
    if not re.fullmatch(r"\w+", name):
        return None
    if not all(_SCALAR_WORD.match(w) for w in words[:-1]):
        return None
    return name

# base-alias local: `void __iomem *base = <base-expr>[ + <offset-expr>];`  (also
# a bare re-alias `base = other_base;`). We track <name> so a later
# `readl(<name> + OFF)` resolves against the real base. The added offset must be
# a resolvable #define constant (folded into the recorded offset) or absent; an
# opaque/computed added offset REFUSES rather than record a wrong offset.
IOMEM_ALIAS = re.compile(
    r"^(?:void\s+__iomem\s*\*|__iomem\s+void\s*\*)?\s*\**\s*(\w+)\s*=\s*(.+)$"
)


def _sym_tokens(expr: str, pin: str | None, defs: dict[str, int],
                symbols: dict[str, list[str]]) -> list[str]:
    """Tokenise a pure-arith expr allowing, additionally, references to previously
    bound symbolic locals in `symbols` (their token stream is inlined, wrapped).
    Refuses on any identifier that is neither the pin, a resolvable #define, nor a
    tracked symbol."""
    toks: list[str] = []
    i = 0
    while i < len(expr):
        m = _OFF_TOKEN.match(expr, i)
        if not m:
            raise Unsupported(f"untokenisable expr fragment: {expr[i:]!r}")
        i = m.end()
        num, ident, op, other = m.group(1), m.group(2), m.group(3), m.group(4)
        if num is not None:
            toks.append(str(int(num, 0)))
        elif ident is not None:
            if pin is not None and ident == pin:
                toks.append("p")
            elif ident in defs:
                toks.append(str(defs[ident]))
            elif ident in symbols:
                toks += ["("] + symbols[ident] + [")"]
            else:
                raise Unsupported(f"unresolvable identifier {ident!r} in expr {expr!r}")
        elif op is not None:
            toks.append(op)
        elif other is not None and other.strip():
            raise Unsupported(f"illegal token {other!r} in expr {expr!r}")
    if not toks:
        raise Unsupported(f"empty expr: {expr!r}")
    return toks


def _mk_comp_off(toks: list[str], disp: str) -> CompOff:
    return CompOff(_off_expr_c(toks), _off_expr_rs(toks), disp)


def extract(src: str, fn: str, defs: dict[str, int]) -> dict:
    ret_type, param_text = func_sig(src, fn)
    iomem_bases, pin = parse_params(param_text)
    returns_value = bool(ret_type) and "void" not in ret_type.split()
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
    # <name> -> token stream: a COMPUTED-OFFSET (or pin-derived value) local, e.g.
    # `regset = (gpio / XLP_GPIO_REGSZ) * 4`. Stored symbolically so an access
    # `readl(addr + regset)` materialises the offset as an expression over `p`.
    symbols: dict[str, list[str]] = {}
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
        # A computed-offset / pin-derived binding: `name = <pure arith of pin +
        # #defines (+ earlier symbols)>`, with NO register read on the RHS (a read
        # would make it a value-compute, handled in the program loop). We track it
        # symbolically; if the RHS isn't purely reducible, refuse rather than guess.
        bm = re.match(r"^(\w+)\s*=\s*(.+)$", s)
        if (bm and pin is not None and "readl" not in s and "writel" not in s
                and bm.group(1) not in ("val", "value")
                and not SCALAR_DECL.match(s) and _match_base_alias(s, defs) is None
                and re.search(r"\b" + re.escape(pin) + r"\b", bm.group(2))):
            symbols[bm.group(1)] = _sym_tokens(bm.group(2).strip(), pin, defs, symbols)
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
        # `return <expr>` — a value-returning function whose result is derived from
        # the reads. The read(s) inside must be lifted to `val`; the returned
        # expression is then pure over val/pin/#defines/symbols or we refuse.
        rm = re.match(r"^return\b\s*(.*)$", s)
        if rm:
            _handle_return(rm.group(1).strip(), prog, defs, pin, symbols,
                           iomem_bases, aliases)
            continue
        if "readl" in s:
            off_name, off_val = _resolve_access(s, "readl", defs, aliases,
                                                iomem_bases, symbols)
            lhs = s.split("=")[0].strip() if "=" in s else "val"
            prog.append(("R", off_val, off_name, lhs))
        elif "writel" in s:
            off_name, off_val = _resolve_access(s, "writel", defs, aliases,
                                                iomem_bases, symbols)
            wm = WR_ALIAS.search(s)
            prog.append(("W", off_val, off_name, wm.group(1).strip()))
        elif re.match(r"\w+\s*(&=|\|=|\^=|=)", s):
            prog.append(("C", None, None, s))   # a compute on val
        else:
            raise Unsupported(f"unmodellable statement: {s!r}")
    if not any(p[0] in ("R", "W") for p in prog):
        raise Unsupported("no MMIO accesses found")
    has_ret = any(p[0] == "RET" for p in prog)
    if returns_value and not has_ret:
        # a value-returning function whose return we couldn't model faithfully:
        # comparing only the trace would UNDER-check (miss a wrong return). Refuse.
        raise Unsupported(f"value-returning fn {fn} with no modellable return")
    return {"fn": fn, "program": prog, "out_of_trace": out_of_trace,
            "returns_value": has_ret,
            "regs": {p[2]: p[1] for p in prog if isinstance(p[1], int)}}


def _balanced_call(s: str, open_paren: int) -> int:
    depth = 0
    for i in range(open_paren, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise Unsupported(f"unbalanced call in {s!r}")


def _handle_return(expr: str, prog: list, defs: dict[str, int], pin: str | None,
                   symbols: dict[str, list[str]], iomem_bases: list[str],
                   aliases: dict[str, int]) -> None:
    """Model `return <expr>`. Any readl inside is lifted to a fresh R access into
    `val`; the remaining return expression is then translated to BOTH C and Rust
    over val/pin/#defines/symbols, refusing if any residue can't be faithfully
    translated (an opaque call, an unresolved identifier)."""
    if not expr:  # bare `return;`
        return
    # lift each readl(...) to an R row, replacing it in-expr with the token `val`.
    reads = list(re.finditer(r"\breadl\s*\(", expr))
    if len(reads) > 1:
        # multiple reads feeding one return: their ordering/aliasing to `val` isn't
        # modellable with a single accumulator — refuse rather than mis-sequence.
        raise Unsupported("multiple reads in a single return expression")
    if reads:
        start = reads[0].start()
        call = _balanced_call(expr, expr.index("(", start))
        access = expr[start: call + 1]
        off_name, off_val = _resolve_access(access, "readl", defs, aliases,
                                            iomem_bases, symbols)
        prog.append(("R", off_val, off_name, "val"))
        expr = expr[:start] + " val " + expr[call + 1:]
    c = _ret_translate(expr, pin, defs, symbols, "c")
    rs = _ret_translate(expr, pin, defs, symbols, "rs")
    prog.append(("RET", None, None, (c, rs)))


def _ret_translate(expr: str, pin: str | None, defs: dict[str, int],
                   symbols: dict[str, list[str]], lang: str) -> str:
    """Translate a lifted return expression (readl already -> `val`) to a faithful
    C or Rust u32 expression. Recognises `BIT(x)` and the `!!`/`!` bool operators,
    then validates every remaining identifier is `val`, the pin, a #define, or a
    tracked symbol — anything else REFUSES. Whitespace-token round-trip guarantees
    no un-translated residue survives silently."""
    # BIT(x) -> (1 << (x)), balanced. The literal `1` gets its language suffix in
    # the final tokenising pass, so nothing here is language-specific yet.
    e = expr
    while True:
        m = re.search(r"\bBIT\s*\(", e)
        if not m:
            break
        close = _balanced_call(e, e.index("(", m.start()))
        inner = e[m.end(): close]
        e = e[:m.start()] + f"((1) << ({inner}))" + e[close + 1:]
    # `!!X` -> ((X) != 0); `!X` -> ((X) == 0). Process innermost-first is overkill
    # for the get() pattern; a single grab of the trailing operand suffices and is
    # validated below. We handle a leading `!!(...)`/`!(...)` wrapping the value.
    e = e.strip()
    mnn = re.match(r"^!!\s*(.+)$", e)
    mn = re.match(r"^!\s*(.+)$", e) if not mnn else None
    if mnn:
        inner = _ret_translate(mnn.group(1), pin, defs, symbols, lang)
        return f"(({inner}) != 0)" if lang == "c" else f"((({inner}) != 0) as u32)"
    if mn:
        inner = _ret_translate(mn.group(1), pin, defs, symbols, lang)
        return f"(({inner}) == 0)" if lang == "c" else f"((({inner}) == 0) as u32)"
    # Now a plain arithmetic/bitwise expression. Tokenise & validate every atom.
    out, i = [], 0
    while i < len(e):
        m = _OFF_TOKEN.match(e, i)
        if not m:
            raise Unsupported(f"untranslatable return fragment: {e[i:]!r}")
        i = m.end()
        num, ident, op, other = m.group(1), m.group(2), m.group(3), m.group(4)
        if num is not None:
            out.append(str(int(num, 0)) + ("u32" if lang == "rs" else "u"))
        elif ident is not None:
            if ident == "val":
                out.append("val")
            elif pin is not None and ident == pin:
                out.append("p")
            elif ident in defs:
                out.append(f"{defs[ident]}" + ("u32" if lang == "rs" else "u"))
            elif ident in symbols:
                sub = symbols[ident]
                out.append("(" + (_off_expr_rs(sub) if lang == "rs" else _off_expr_c(sub)) + ")")
            else:
                raise Unsupported(f"unresolvable identifier {ident!r} in return {expr!r}")
        elif op is not None:
            out.append("!" if (op == "~" and lang == "rs") else op)
        elif other is not None and other.strip():
            raise Unsupported(f"untranslatable token {other!r} in return {expr!r}")
    return "(" + "".join(f" {t} " if t in ("<<", ">>") or (len(t) == 1 and t in _OFF_OK_OPS)
                         else t for t in out).strip() + ")"


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


def _resolve_access(s: str, op: str, defs: dict[str, int], aliases: dict[str, int],
                    iomem_bases: list[str] | None = None,
                    symbols: dict[str, list[str]] | None = None):
    """Resolve a `readl/writel(<target> + OFF)` to (offset_name, offset_value).
    <target> is either a register base (`x->base`), a bare iomem PARAMETER (`addr`),
    or a tracked base-alias. OFF is either a resolvable #define constant (an int
    offset, folded through any base-alias prologue) or a tracked COMPUTED-OFFSET
    local (a `CompOff`, materialised as an expression over `p`; off_val is then
    None and no #define is emitted)."""
    iomem_bases = iomem_bases or []
    symbols = symbols or {}
    rx = RD_ALIAS if op == "readl" else WR_ALIAS
    m = rx.search(s)
    if not m:
        raise Unsupported(f"non-clean {op}: {s!r}")
    target = m.group(1) if op == "readl" else m.group(2)
    off_name = m.group(2) if op == "readl" else m.group(3)
    # the base must be a known register base, a bare iomem param, or a base-alias
    is_base = bool(re.fullmatch(BASE, target)) or target in iomem_bases
    is_alias = target in aliases
    if not (is_base or is_alias):
        raise Unsupported(f"non-clean {op} (unknown base/alias {target!r}): {s!r}")
    # COMPUTED offset: a tracked symbolic local (the xlp `regset` bank offset).
    if off_name in symbols:
        if is_alias and aliases[target] != 0:
            # a computed offset ON TOP of a folded base-alias prologue: summing a
            # symbolic expr with a folded const is doable, but no real pattern needs
            # it and getting the recorded name/faithfulness right is subtle — refuse.
            raise Unsupported(f"computed offset over folded base-alias: {s!r}")
        toks = symbols[off_name]
        return _mk_comp_off(toks, off_name), None
    # CONSTANT offset: a resolvable #define.
    if off_name not in defs:
        raise Unsupported(f"non-clean {op}: {s!r}")
    off_val = defs[off_name]
    if is_base:
        return off_name, off_val
    # tracked base-alias local
    base_off = aliases[target]
    if base_off == 0:
        return off_name, off_val
    # fold the prologue offset into the recorded offset; synthesise a valid-C name
    return f"{off_name}_PLUS_{base_off:x}", base_off + off_val


def _expr_c(e: str) -> str:
    """value expr -> C over the pin input `p` (BIT(hwirq)->(1u<<p))."""
    e = re.sub(r"BIT\s*\(\s*irqd_to_hwirq\([^)]*\)\s*\)", "(1u<<p)", e)
    e = re.sub(r"irqd_to_hwirq\s*\([^)]*\)", "p", e)
    e = re.sub(r"BIT\s*\(\s*(\w+)\s*\)", r"(1u<<(\1))", e)
    return e


def _off_c(name) -> str:
    """The C text of an access offset: a #define name (constant) or a materialised
    computed-offset expression over `p`."""
    return name.c if isinstance(name, CompOff) else str(name)


def _off_rs(name) -> str:
    return name.rs if isinstance(name, CompOff) else str(name)


def _returns(ex: dict) -> bool:
    return ex.get("returns_value", False)


def emit_ref_c(ex: dict) -> str:
    # the engine (state + reg_read/reg_write) lives ONLY in the probe TU so the
    # trace is shared; the ref just declares the seam extern
    lines = ["#include <stdint.h>",
             "extern uint32_t reg_read(uint32_t off);",
             "extern void reg_write(uint32_t off, uint32_t val);"]
    for n, v in sorted(ex["regs"].items(), key=lambda x: x[1]):
        lines.append(f"#define {n} 0x{v:x}u")
    rty = "uint32_t" if _returns(ex) else "void"
    lines += ["", f"{rty} {ex['fn']}_ref(uint32_t p)", "{", "\tuint32_t val = 0; (void)val;",
              "\t(void)p;"]
    for kind, off, name, expr in ex["program"]:
        if kind == "R":
            lines.append(f"\t{_expr_c(expr)} = reg_read({_off_c(name)});")
        elif kind == "W":
            lines.append(f"\treg_write({_off_c(name)}, {_expr_c(expr)});")
        elif kind == "RET":
            lines.append(f"\treturn {expr[0]};")   # expr = (c_text, rs_text)
        else:  # compute
            lines.append(f"\t{_expr_c(expr)};")
    lines.append("}")
    return "\n".join(lines)


def _reg_consts_rs(ex: dict) -> str:
    return "\n".join(f"const {n}: u32 = 0x{v:x};" for n, v in sorted(ex["regs"].items(), key=lambda x: x[1]))


def _mutant_surface(ex: dict) -> str:
    """Pick the ONE program row the negative control perturbs so it genuinely
    DIVERGES. Prefer a write offset, else a read offset, else the return value —
    every closable function has at least one of these observable surfaces."""
    for kind, *_ in ex["program"]:
        if kind == "W":
            return "W"
    for kind, *_ in ex["program"]:
        if kind == "R":
            return "R"
    return "RET"


def emit_cand_rs(ex: dict, mutate: bool = False) -> str:
    """The register program in Rust (the candidate), or a mutated negative control
    (one offset bumped, or the return value corrupted) that runs the SAME shape but
    produces a WRONG trace/return — proving the oracle is non-vacuous."""
    surf = _mutant_surface(ex) if mutate else None
    rty = " -> u32" if _returns(ex) else ""
    L = ['extern "C" { fn reg_read(off: u32) -> u32; fn reg_write(off: u32, val: u32); }',
         _reg_consts_rs(ex), "",
         f'#[no_mangle]\npub extern "C" fn cgir_{ex["fn"]}(p: u32){rty} {{',
         "    let mut val: u32 = 0; let _ = val; let _ = p;"]
    w_seen = r_seen = 0
    for kind, off, name, expr in ex["program"]:
        if kind == "R":
            tgt = _off_rs(name)
            if mutate and surf == "R" and r_seen == 0:
                tgt = f"({tgt} ^ 0x4)"   # BUG: read the wrong register offset
            L.append(f"    unsafe {{ val = reg_read({tgt}); }}")
            r_seen += 1
        elif kind == "W":
            tgt = _off_rs(name)
            if mutate and surf == "W" and w_seen == 0:
                tgt = f"({tgt} ^ 0x4)"   # BUG: write the wrong register offset
            L.append(f"    unsafe {{ reg_write({tgt}, {_expr_rs(expr)}); }}")
            w_seen += 1
        elif kind == "RET":
            rv = expr[1]  # rs text
            if mutate and surf == "RET":
                rv = f"(({rv}) ^ 1)"     # BUG: corrupt the returned value
            L.append(f"    return {rv};")
        else:
            L.append(f"    {_expr_rs(expr)};")
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
    """Drive RECORD (C ref) then REPLAY (Rust candidate) across the pin sweep and
    accept iff the candidate issues the identical register trace. For a
    value-returning function the probe ALSO compares the returned value each pin —
    a get() whose return depends on a read is exactly where the return strengthens
    the oracle (a wrong offset OR a wrong return-fold both get caught)."""
    fn = ex["fn"]
    if _returns(ex):
        ref_decl, cand_decl = \
            f"uint32_t {fn}_ref(uint32_t p);", f'extern uint32_t cgir_{fn}(uint32_t p);'
        run_ref = f"uint32_t rref = {fn}_ref(p); int rl=tn;"
        run_cand = f"uint32_t rcand = cgir_{fn}(p);"
        retcmp = "|| rref != rcand"
    else:
        ref_decl, cand_decl = f"void {fn}_ref(uint32_t p);", f"extern void cgir_{fn}(uint32_t p);"
        run_ref = f"{fn}_ref(p); int rl=tn;"
        run_cand = f"cgir_{fn}(p);"
        retcmp = ""
    return f"""#include <stdio.h>
#include "record_engine.h"
{ref_decl}
{cand_decl}
int main(void) {{
    unsigned cases=0, bad=0; long fb=-1; int dp=-1;
    for (uint32_t p=0; p<32; p++) {{
        model_seed();
        mode=RECORD; tn=0; {run_ref}
        mode=REPLAY; tpos=0; diverged=0; div_pos=-1; {run_cand}
        int consumed=(tpos==rl); cases++;
        if (diverged || !consumed {retcmp}) {{ bad++; if(fb<0){{fb=p; dp=diverged?div_pos:tpos;}} }}
    }}
    printf("MMIOGEN: {fn} cases=%u bad=%u firstbad=%ld div_at=%d verdict=%s\\n",
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
