#!/usr/bin/env python3
"""Adversarial + fuzz battery for the MMIO harness generator (dream/mmiogen).

The generator's soundness contract: EXTRACT the register program faithfully or
REFUSE (mmio_harness.Unsupported). It must NEVER silently produce a lossy/wrong
program that then self-matches its own oracle.

The pinned failure mode (already fixed, must stay fixed): splitting the body on
';' has no statement grammar, so `if (cond) writel(...)` collapsed into an
UNCONDITIONAL write in BOTH the C ref and the Rust candidate. Because both are
emitted from the same lossy program, the wrong extraction replayed identically
against its own oracle and reported CLOSED/MATCH — a false pass. The fix: any
control-flow keyword in the (comment-masked) body => refuse, never guess.

These tests pin that fix on REAL driver functions (found via $KSRC) and hunt for
similar holes: helper-wrapped accessors, computed/macro offsets, unresolved
#defines, and malformed bodies. The invariant every adversarial/fuzz case
asserts is: the generator either returns a SANE program (no MMIO access with a
None-but-claimed-resolved offset) or raises Unsupported — it never crashes with
an unexpected exception, and never emits a lossy program that could self-match.

Run:  python3 -m pytest dream/tests/test_mmiogen.py -q
Toolchain-gated tests (generate/gate) skip cleanly if rustc/cc are missing.
"""
from __future__ import annotations

import os
import re
import shutil
import sys

import pytest

# Mirror how dream/router/*.py import sibling modules: put the package dir on the
# path and import the module by name.
HERE = os.path.dirname(os.path.abspath(__file__))
MMIOGEN = os.path.normpath(os.path.join(HERE, "..", "mmiogen"))
sys.path.insert(0, MMIOGEN)
import mmio_harness as mh  # noqa: E402

DRV = "drivers/gpio/gpio-ftgpio010.c"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _driver_src(rel: str) -> str:
    """Read a driver from $KSRC; skip (don't fail) if the tree isn't present."""
    path = os.path.join(mh.KSRC, rel)
    if not os.path.exists(path):
        pytest.skip(f"KSRC driver not present: {path}")
    return open(path, errors="ignore").read()


def _defs_for(rel: str) -> dict:
    return mh.resolve_defines(_driver_src(rel))


def _has_toolchain() -> bool:
    return bool(shutil.which("rustc") and shutil.which("cc"))


toolchain = pytest.mark.skipif(not _has_toolchain(), reason="rustc/cc not available")


def _prog_seq(ex: dict) -> list[tuple[str, str | None]]:
    """The (kind, offset-name) sequence — the observable register program."""
    return [(p[0], p[2]) for p in ex["program"]]


def _assert_no_phantom_resolve(ex: dict) -> None:
    """Core soundness invariant: no R/W access claims a resolved offset while
    carrying a None offset value. If it's an MMIO access it MUST have an int
    offset; only compute (C) rows are allowed a None offset."""
    for kind, off, name, _expr in ex["program"]:
        if kind in ("R", "W"):
            assert off is not None, f"MMIO {kind} with None offset: {ex['fn']}"
            assert isinstance(off, int)
            assert name is not None
        # regs map must agree with the program
    for name, off in ex["regs"].items():
        assert isinstance(off, int)


# --------------------------------------------------------------------------- #
# 1. CORRECT: the three clean ftgpio functions
# --------------------------------------------------------------------------- #
EXPECTED = {
    # ack: single unconditional clear write
    "ftgpio_gpio_ack_irq": [("W", "GPIO_INT_CLR")],
    # mask: read-modify-write on INT_EN (the &= is a compute row)
    "ftgpio_gpio_mask_irq": [("R", "GPIO_INT_EN"), ("C", None), ("W", "GPIO_INT_EN")],
    # unmask: same read-modify-write shape on INT_EN
    "ftgpio_gpio_unmask_irq": [("R", "GPIO_INT_EN"), ("C", None), ("W", "GPIO_INT_EN")],
}


@pytest.mark.parametrize("fn,seq", list(EXPECTED.items()))
def test_clean_functions_extract_expected_program(fn, seq):
    src = _driver_src(DRV)
    defs = mh.resolve_defines(src)
    ex = mh.extract(src, fn, defs)
    assert _prog_seq(ex) == seq
    _assert_no_phantom_resolve(ex)


def test_clean_out_of_trace_noted_not_dropped():
    """mask/unmask call gpiochip_{disable,enable}_irq — these must be recorded as
    out-of-trace effects, not silently swallowed (the honest recorder edge)."""
    src = _driver_src(DRV)
    defs = mh.resolve_defines(src)
    assert mh.extract(src, "ftgpio_gpio_mask_irq", defs)["out_of_trace"] == [
        "gpiochip_disable_irq"
    ]
    assert mh.extract(src, "ftgpio_gpio_unmask_irq", defs)["out_of_trace"] == [
        "gpiochip_enable_irq"
    ]


@toolchain
@pytest.mark.parametrize("fn", list(EXPECTED))
def test_generate_and_gate_match_and_diverge(fn, tmp_path):
    """End-to-end, non-vacuous: correct candidate MATCHes its own C register
    trace; the mutated negative control (one write offset bumped) DIVERGEs.
    A generator that lost the program would make the mutant MATCH too."""
    out = str(tmp_path / fn)
    ex = mh.generate(DRV, fn, out)
    assert _prog_seq(ex) == EXPECTED[fn]

    verdict_ok, line_ok = mh.gate(fn, out, f"{fn}_cand.rs")
    assert verdict_ok == "MATCH", f"correct candidate not MATCH: {line_ok}"

    verdict_bad, line_bad = mh.gate(fn, out, f"{fn}_bad.rs")
    assert verdict_bad == "DIVERGE", f"mutant did NOT diverge (self-match hole!): {line_bad}"


# --------------------------------------------------------------------------- #
# 2. ADVERSARIAL control flow — real driver functions must REFUSE, not CLOSE
# --------------------------------------------------------------------------- #
# Each of these is a REAL drivers/gpio function that mixes readl/writel with
# control flow. A ';'-split extraction would erase the branch and produce a
# lossy-but-self-matching program. The contract is: refuse.
CONTROL_FNS = [
    ("drivers/gpio/gpio-ftgpio010.c", "ftgpio_gpio_set_irq_type", "switch"),
    ("drivers/gpio/gpio-ftgpio010.c", "ftgpio_gpio_set_config", "for/if"),
    ("drivers/gpio/gpio-altera.c", "altera_gpio_irq_edge_handler", "while"),
    ("drivers/gpio/gpio-rockchip.c", "rockchip_irq_set_type", "goto/if"),
    ("drivers/gpio/gpio-amdpt.c", "pt_gpio_request", "if"),
    ("drivers/gpio/gpio-creg-snps.c", "creg_gpio_set", "for/ternary"),
]


@pytest.mark.parametrize("rel,fn,kind", CONTROL_FNS, ids=[f"{k}:{f}" for _, f, k in CONTROL_FNS])
def test_real_control_flow_refuses(rel, fn, kind):
    src = _driver_src(rel)
    defs = mh.resolve_defines(src)
    with pytest.raises(mh.Unsupported):
        mh.extract(src, fn, defs)


def test_synthetic_if_write_refuses():
    """The exact pinned regression: `if (p) writel(...)`. A ';'-split would emit
    an UNCONDITIONAL write in both ref and candidate — self-matching lossiness.
    Must refuse."""
    defs = {"REG": 0x20}
    body = "static void f(struct s *g) { if (p) writel(v, g->base + REG); }"
    with pytest.raises(mh.Unsupported):
        mh.extract(body, "f", defs)


def test_control_masking_ignores_keywords_in_comments():
    """The CONTROL guard masks comments first, so a keyword inside a comment must
    NOT trip a false refusal on an otherwise-clean function."""
    defs = {"REG": 0x20}
    body = (
        "static void f(struct s *g) { /* if we ever add a loop, refuse */ "
        "writel(1, g->base + REG); }"
    )
    ex = mh.extract(body, "f", defs)
    assert _prog_seq(ex) == [("W", "REG")]
    _assert_no_phantom_resolve(ex)


# --------------------------------------------------------------------------- #
# 3. ADVERSARIAL non-clean access — offset not a resolvable #define => REFUSE
# --------------------------------------------------------------------------- #
def test_helper_wrapped_param_offset_refuses():
    """`readl(ctrl->base + reg)` with reg a PARAMETER, not a #define: the offset
    isn't a constant the recorder can pin, so it must refuse."""
    defs = {"REG": 0x20}
    body = "static void f(struct s *ctrl, u32 reg) { u32 val; val = readl(ctrl->base + reg); }"
    with pytest.raises(mh.Unsupported):
        mh.extract(body, "f", defs)


def test_computed_macro_offset_refuses():
    """`readl(base + PDR(gpio))` — a function-like macro offset is not a
    resolvable #define constant => refuse (never guess an offset)."""
    defs = {"REG": 0x20}
    body = "static void f(struct s *g) { u32 val; val = readl(g->base + PDR(gpio)); }"
    with pytest.raises(mh.Unsupported):
        mh.extract(body, "f", defs)


def test_unresolved_define_refuses():
    """A write to a name that never resolves to an int #define must refuse — the
    offset would otherwise be phantom."""
    body = "static void f(struct s *g) { writel(1, g->base + UNKNOWN_REG); }"
    with pytest.raises(mh.Unsupported):
        mh.extract(body, "f", {})


# --------------------------------------------------------------------------- #
# 4. FUZZ — malformed / edge-case bodies: sane program OR Unsupported, never a
#    crash and never a phantom-resolved MMIO access.
# --------------------------------------------------------------------------- #
FUZZ_BODIES = {
    "empty_body": "static void f(void) { }",
    "whitespace_body": "static void f(void) {   \n\t  }",
    "only_declarations": "static void f(struct s *g) { u32 val; struct x *p = g; }",
    # func_body must catch the missing close brace, not run off the end
    "unbalanced_braces": "static void f(struct s *g) { writel(1, g->base + REG); ",
    "write_to_unresolved_define": "static void f(struct s *g) { writel(1, g->base + NOPE); }",
    "deeply_nested_braces": "static void f(struct s *g) { { { { writel(1, g->base + REG); } } } }",
    "garbage_statement": "static void f(struct s *g) { @#$%^ ; }",
    "trailing_semicolons": "static void f(struct s *g) { ;;; writel(1, g->base + REG); ;;; }",
    "comment_only": "static void f(struct s *g) { /* nothing to see */ }",
    "read_no_assign": "static void f(struct s *g) { readl(g->base + REG); }",
}


@pytest.mark.parametrize("name,body", list(FUZZ_BODIES.items()), ids=list(FUZZ_BODIES))
def test_fuzz_extract_never_crashes_or_phantoms(name, body):
    defs = {"REG": 0x20}
    try:
        ex = mh.extract(body, "f", defs)
    except mh.Unsupported:
        return  # refusal is always an acceptable outcome
    except Exception as e:  # noqa: BLE001 — any other exception is a real bug
        pytest.fail(f"{name}: unexpected {type(e).__name__}: {e}")
    # If it DID return a program, it must be sane: no phantom-resolved MMIO.
    _assert_no_phantom_resolve(ex)


def test_func_body_unbalanced_raises_unsupported():
    """func_body itself is the first line of defense: an unclosed body must be
    Unsupported, not an IndexError or a runaway slice."""
    body = "static void f(void) { writel(1, g->base + REG); "  # no close
    with pytest.raises(mh.Unsupported):
        mh.func_body(body, "f")


def test_func_body_missing_function_raises_unsupported():
    with pytest.raises(mh.Unsupported):
        mh.func_body("static void other(void) { }", "f")


def test_no_mmio_access_refuses():
    """A body with reads/writes stripped to nothing modellable must refuse rather
    than emit an empty (vacuously-matching) program."""
    defs = {"REG": 0x20}
    with pytest.raises(mh.Unsupported):
        mh.extract("static void f(struct s *g) { u32 val; }", "f", defs)


# --------------------------------------------------------------------------- #
# 5. BASE-ALIAS LOCALS — `void __iomem *base = g->reg_base + OFF; readl(base+O2)`
# --------------------------------------------------------------------------- #
def test_base_alias_zero_fold_extracts():
    """`base = g->base` (no added offset) then `readl(base + REG)` resolves to REG
    exactly, same as a direct `readl(g->base + REG)`."""
    defs = {"REG": 0x20}
    body = (
        "static void f(struct s *g) { "
        "void __iomem *base = g->base; "
        "u32 val; val = readl(base + REG); writel(val, base + REG); }"
    )
    ex = mh.extract(body, "f", defs)
    assert _prog_seq(ex) == [("R", "REG"), ("W", "REG")]
    assert ex["regs"]["REG"] == 0x20
    _assert_no_phantom_resolve(ex)


def test_base_alias_const_fold_records_absolute_offset():
    """`base = g->base + BANK` folds BANK into every subsequent access offset: the
    recorded offset must be the ABSOLUTE offset the driver pokes (BANK+REG), not
    REG — otherwise the trace would be wrong."""
    defs = {"BANK": 0x800, "REG": 0x40}
    body = (
        "static void f(struct s *g) { "
        "void __iomem *base = g->base + BANK; "
        "u32 val; writel(1, base + REG); }"
    )
    ex = mh.extract(body, "f", defs)
    assert _prog_seq(ex) == [("W", "REG_PLUS_800")]
    # 0x800 + 0x40 = 0x840 — the true absolute offset
    assert ex["regs"]["REG_PLUS_800"] == 0x840
    _assert_no_phantom_resolve(ex)


def test_base_alias_opaque_offset_refuses():
    """`base = g->base + bank_stride(pin)` — the added offset is a runtime function
    of the pin, not a resolvable #define. Recording base+0 would be a WRONG
    trace, so it MUST refuse rather than guess."""
    defs = {"REG": 0x40}
    body = (
        "static void f(struct s *g, int pin) { "
        "void __iomem *base = g->base + bank_stride(pin); "
        "u32 val; writel(1, base + REG); }"
    )
    with pytest.raises(mh.Unsupported):
        mh.extract(body, "f", defs)


@toolchain
def test_base_alias_end_to_end_match_and_diverge(tmp_path):
    """Full record/replay on a base-alias function: correct MATCHes its own C
    register trace, wrong-register control DIVERGEs. Proves the folded absolute
    offset is what actually drives the recorded trace (non-vacuous)."""
    defs = {"BANK": 0x800, "ENABLE_CONFIG": 0x00, "INT_CLR": 0x40}
    body = (
        "static void basealias_demo(struct s *g) { "
        "void __iomem *base = g->base + BANK; "
        "u32 val, tmp; "
        "val = readl(base + ENABLE_CONFIG); val |= 0x2; "
        "writel(val, base + ENABLE_CONFIG); writel(1, base + INT_CLR); }"
    )
    ex = mh.extract(body, "basealias_demo", defs)
    assert _prog_seq(ex) == [
        ("R", "ENABLE_CONFIG_PLUS_800"), ("C", None),
        ("W", "ENABLE_CONFIG_PLUS_800"), ("W", "INT_CLR_PLUS_800"),
    ]
    out = str(tmp_path / "basealias")
    os.makedirs(out, exist_ok=True)
    shutil.copy(os.path.join(mh.HERE, "record_engine.h"), out)
    open(f"{out}/basealias_demo_ref.c", "w").write(mh.emit_ref_c(ex))
    open(f"{out}/basealias_demo_cand.rs", "w").write(mh.emit_cand_rs(ex, False))
    open(f"{out}/basealias_demo_bad.rs", "w").write(mh.emit_cand_rs(ex, True))
    open(f"{out}/basealias_demo_probe.c", "w").write(mh.emit_probe(ex))
    v1, l1 = mh.gate("basealias_demo", out, "basealias_demo_cand.rs")
    assert v1 == "MATCH", f"correct base-alias candidate not MATCH: {l1}"
    v2, l2 = mh.gate("basealias_demo", out, "basealias_demo_bad.rs")
    assert v2 == "DIVERGE", f"base-alias mutant did not diverge (self-match hole!): {l2}"


# --------------------------------------------------------------------------- #
# 6. MULTI-LOCAL DECLARATIONS — several scalar SSA temps, dropped as plumbing
# --------------------------------------------------------------------------- #
def test_multi_local_comma_decl_dropped():
    """`u32 pos, regset, val;` — multiple comma-separated scalar temps must all be
    dropped as plumbing, leaving the clean access program."""
    defs = {"REG": 0x20}
    body = (
        "static void f(struct s *g) { "
        "u32 pos, regset, val; unsigned long flags; int off; "
        "val = readl(g->base + REG); writel(val, g->base + REG); }"
    )
    ex = mh.extract(body, "f", defs)
    assert _prog_seq(ex) == [("R", "REG"), ("W", "REG")]
    _assert_no_phantom_resolve(ex)


def test_multi_local_does_not_swallow_effects():
    """The multi-local drop is a strict scalar-decl allow-list: a non-declaration
    statement (a bare call with side effects) must NOT be swallowed — if it isn't
    a modellable access/compute/out-of-trace effect, refuse."""
    defs = {"REG": 0x20}
    body = (
        "static void f(struct s *g) { "
        "u32 a, b; some_side_effect(g); writel(1, g->base + REG); }"
    )
    with pytest.raises(mh.Unsupported):
        mh.extract(body, "f", defs)


def test_multi_local_computed_offset_now_extracts_faithfully():
    """The xlp bank-accessor pattern `u32 pos, regset; regset = (gpio/REGSZ)*4;
    readl(addr + regset)` is now CLOSED, not refused: `regset` is a computed offset,
    a PURE arithmetic expression of the pin + a resolvable #define, so it is tracked
    symbolically and materialised as an expression over `p`. The record engine
    records the actual per-pin runtime offset, so this is faithful — not a phantom.
    (The old contract refused it; this capability replaces that limitation.)"""
    defs = {"XLP_GPIO_REGSZ": 32}
    body = (
        "static int f(void __iomem *addr, unsigned gpio) { "
        "u32 pos, regset; regset = (gpio / XLP_GPIO_REGSZ) * 4; "
        "return readl(addr + regset); }"
    )
    ex = mh.extract(body, "f", defs)
    # one computed-offset read; no phantom #define emitted for it
    seq = _prog_seq(ex)
    assert ("R", None) not in seq  # the R carries a CompOff name, not None
    r_rows = [p for p in ex["program"] if p[0] == "R"]
    assert len(r_rows) == 1
    assert isinstance(r_rows[0][2], mh.CompOff)
    # the materialised offset is the true bank expression over the pin `p`
    assert "p / 32" in r_rows[0][2].c.replace("  ", " ")
    # a computed offset carries NO int value and emits NO #define
    assert r_rows[0][1] is None
    assert ex["regs"] == {}


def test_multi_local_opaque_offset_local_still_refuses():
    """A local offset whose RHS references something NOT reducible to (pin,
    resolvable-constants, arithmetic) — here an opaque helper call — must REFUSE:
    recording a guessed offset would be an unsound trace."""
    defs = {"XLP_GPIO_REGSZ": 32}
    body = (
        "static int f(void __iomem *addr, unsigned gpio) { "
        "u32 regset; regset = bank_of(gpio) * 4; "
        "return readl(addr + regset); }"
    )
    with pytest.raises(mh.Unsupported):
        mh.extract(body, "f", defs)


# --------------------------------------------------------------------------- #
# 7. NESTED BRACE SCOPE (keyword-less) — must refuse, ';'-split would flatten it
# --------------------------------------------------------------------------- #
def test_scoped_guard_block_refuses():
    """`scoped_guard(...) { ... }` has no control-flow keyword but IS a scope; the
    ';'-split flattens the block opener onto the next statement. This is exactly
    as unmodellable as control flow — must refuse (real mlxbf2 pattern)."""
    defs = {"REG": 0x94}
    body = (
        "static void f(struct s *gs) { u32 val; "
        "scoped_guard(gpio_lock, &gs->chip) { "
        "val = readl(gs->gpio_io + REG); writel(val, gs->gpio_io + REG); } }"
    )
    with pytest.raises(mh.Unsupported):
        mh.extract(body, "f", defs)


def test_resolve_defines_ignores_non_integer():
    """resolve_defines must only surface integer offsets; string/expr macros are
    dropped, so they can never masquerade as a resolvable register offset."""
    src = (
        "#define GPIO_INT_EN 0x20\n"
        "#define GPIO_NAME \"foo\"\n"
        "#define GPIO_EXPR (A + B)\n"
        "#define GPIO_DEC 44\n"
    )
    defs = mh.resolve_defines(src)
    assert defs == {"GPIO_INT_EN": 0x20, "GPIO_DEC": 44}


# --------------------------------------------------------------------------- #
# 8. BANK-REGISTER ACCESSOR — base-as-param + computed offset + return value.
#    This is the xlp gpio pattern: `readl(addr + (gpio/REGSZ)*4)` with `addr` a
#    bare `void __iomem *` param and the offset a pin-derived bank expression.
# --------------------------------------------------------------------------- #
XLP = "drivers/gpio/gpio-xlp.c"


def test_base_as_param_recognised():
    """A bare `void __iomem *addr` PARAMETER is a valid register base (not only
    `x->base`); an integer scalar param is the pin. parse_params surfaces both."""
    iomem, pin = mh.parse_params("void __iomem *addr, unsigned gpio")
    assert iomem == ["addr"]
    assert pin == "gpio"


def test_xlp_get_reg_computed_offset_closes():
    """The real xlp_gpio_get_reg: base-as-param + computed bank offset + a
    read-derived return. Must extract a SINGLE computed-offset read and a return,
    with the offset materialised as the true bank expression over the pin — not a
    constant collapse."""
    src = _driver_src(XLP)
    defs = mh.resolve_defines(src)
    ex = mh.extract(src, "xlp_gpio_get_reg", defs)
    kinds = [p[0] for p in ex["program"]]
    assert kinds == ["R", "RET"], kinds
    assert ex["returns_value"] is True
    r = [p for p in ex["program"] if p[0] == "R"][0]
    # offset is a CompOff carrying the bank expression (p/32)*4, NOT a #define
    assert isinstance(r[2], mh.CompOff)
    assert r[1] is None and ex["regs"] == {}
    c = r[2].c.replace("  ", " ")
    assert "p / 32" in c and "* 4" in c   # faithful bank arithmetic, pin-dependent


@toolchain
def test_xlp_get_reg_end_to_end_match_and_diverge(tmp_path):
    """Full record/replay on the bank accessor: correct candidate MATCHes its own
    C register+return trace; the mutant (perturbed read offset) DIVERGEs. Proves
    the computed offset genuinely drives the recorded trace (non-vacuous)."""
    out = str(tmp_path / "xlp_get")
    ex = mh.generate(XLP, "xlp_gpio_get_reg", out)
    assert ex["returns_value"] is True
    v1, l1 = mh.gate("xlp_gpio_get_reg", out, "xlp_gpio_get_reg_cand.rs")
    assert v1 == "MATCH", f"correct bank-accessor not MATCH: {l1}"
    v2, l2 = mh.gate("xlp_gpio_get_reg", out, "xlp_gpio_get_reg_bad.rs")
    assert v2 == "DIVERGE", f"bank-accessor mutant did not diverge (self-match hole!): {l2}"


def test_xlp_set_reg_control_flow_still_refuses():
    """xlp_gpio_set_reg has `if (state) value |= BIT(pos); else ...` — control
    flow. Even though its base/offset shape is the newly-supported bank pattern,
    the branch is unmodellable by a ';'-split, so it MUST still refuse. Coverage
    must never come at the cost of the control-flow refusal."""
    src = _driver_src(XLP)
    defs = mh.resolve_defines(src)
    with pytest.raises(mh.Unsupported):
        mh.extract(src, "xlp_gpio_set_reg", defs)


@toolchain
def test_computed_offset_with_return_synthetic_diverges(tmp_path):
    """A synthetic bank accessor whose return folds the read: end-to-end the return
    comparison catches a corrupted return even when the register trace is identical
    (the RET mutant path). Strengthens the oracle beyond the trace alone."""
    defs = {"REGSZ": 16}
    body = (
        "static int f(void __iomem *addr, unsigned pin) { "
        "u32 off; off = (pin / REGSZ) * 4; "
        "return readl(addr + off) & 0xff; }"
    )
    ex = mh.extract(body, "f", defs)
    assert [p[0] for p in ex["program"]] == ["R", "RET"]
    out = str(tmp_path / "synth")
    os.makedirs(out, exist_ok=True)
    shutil.copy(os.path.join(mh.HERE, "record_engine.h"), out)
    open(f"{out}/f_ref.c", "w").write(mh.emit_ref_c(ex))
    open(f"{out}/f_cand.rs", "w").write(mh.emit_cand_rs(ex, False))
    open(f"{out}/f_bad.rs", "w").write(mh.emit_cand_rs(ex, True))
    open(f"{out}/f_probe.c", "w").write(mh.emit_probe(ex))
    assert mh.gate("f", out, "f_cand.rs")[0] == "MATCH"
    assert mh.gate("f", out, "f_bad.rs")[0] == "DIVERGE"


def test_computed_offset_opaque_call_refuses():
    """A computed-offset local whose RHS contains an opaque call
    (`off = bank_stride(pin) * 4`) is NOT reducible to (pin, constants, arithmetic)
    — the offset can't be materialised faithfully, so REFUSE (never guess)."""
    defs = {"REGSZ": 16}
    body = (
        "static int f(void __iomem *addr, unsigned pin) { "
        "u32 off; off = bank_stride(pin) * 4; return readl(addr + off); }"
    )
    with pytest.raises(mh.Unsupported):
        mh.extract(body, "f", defs)


def test_computed_offset_unresolved_const_refuses():
    """A computed-offset local referencing an UNRESOLVED identifier (a #define that
    isn't a resolvable integer) can't be folded to a numeric expression — REFUSE
    rather than emit a wrong offset."""
    defs = {}  # STRIDE not resolvable
    body = (
        "static int f(void __iomem *addr, unsigned pin) { "
        "u32 off; off = (pin / STRIDE) * 4; return readl(addr + off); }"
    )
    with pytest.raises(mh.Unsupported):
        mh.extract(body, "f", defs)


def test_non_translatable_return_expr_refuses():
    """A return expression with a residual opaque call
    (`return some_fold(readl(addr + off))`) can't be faithfully translated to Rust
    — REFUSE rather than emit a lossy/self-matching return."""
    defs = {"REGSZ": 16}
    body = (
        "static int f(void __iomem *addr, unsigned pin) { "
        "u32 off; off = (pin / REGSZ) * 4; "
        "return some_fold(readl(addr + off)); }"
    )
    with pytest.raises(mh.Unsupported):
        mh.extract(body, "f", defs)


def test_value_returning_fn_with_unmodellable_return_refuses():
    """A value-returning function whose return we can't model (here the return is a
    bare pin passthrough with no read, but a struct deref appears) must not silently
    fall back to a trace-only oracle that under-checks. If the return can't be
    modelled faithfully, refuse."""
    defs = {"REG": 0x20}
    body = (
        "static int f(struct s *g, unsigned pin) { "
        "u32 val; val = readl(g->base + REG); return g->cache; }"
    )
    with pytest.raises(mh.Unsupported):
        mh.extract(body, "f", defs)


# --------------------------------------------------------------------------- #
# 9. READ-MODIFY-WRITE WITH A NAMED LOCAL — the most common register-write idiom.
#    `stat = readl(base + OFF); stat &= ~(MASK << SHIFT); stat |= in << SHIFT;
#     writel(stat, base + OFF)`. Closing this needs: the read to assign its REAL
#    bare-name target (not a hardcoded `val`), that target DECLARED, the input
#    scalar param mapped to the pin `p`, and the mask/shift #defines used in the
#    compute EMITTED as constants. Faithful or refuse — never a self-matching leak.
# --------------------------------------------------------------------------- #
ORION = "arch/arm/plat-orion/pcie.c"


def test_rmw_named_local_extracts_and_declares(tmp_path):
    """The orion pattern: RMW into the named local `stat` on PCIE_STAT_OFF. It must
    extract R(OFF) [C] [C] W(OFF), record `stat` as a declared local, map the input
    `nr` -> `p`, and collect the mask/shift #defines used in the compute."""
    src = _driver_src(ORION)
    defs = mh.resolve_defines(src)
    ex = mh.extract(src, "orion_pcie_set_local_bus_nr", defs)
    assert _prog_seq(ex) == [
        ("R", "PCIE_STAT_OFF"), ("C", None), ("C", None), ("W", "PCIE_STAT_OFF"),
    ]
    assert ex["input"] == "nr"
    assert ex["locals"] == ["stat"]        # the read/compute target, declared
    # the mask + shift constants the compute references are collected for emission
    assert ex["const_defs"] == {"PCIE_STAT_BUS_MASK": 0xff, "PCIE_STAT_BUS_OFFS": 8}
    _assert_no_phantom_resolve(ex)
    # emitted programs must DECLARE the local (both languages), map input -> p, and
    # emit the compute constants — nothing referenced may be undeclared.
    c = mh.emit_ref_c(ex)
    rs = mh.emit_cand_rs(ex, False)
    assert "uint32_t stat = 0" in c and "let mut stat: u32 = 0" in rs
    assert "#define PCIE_STAT_BUS_MASK" in c and "const PCIE_STAT_BUS_MASK" in rs
    assert re.search(r"\bnr\b", rs) is None      # input fully substituted to p
    assert "p << PCIE_STAT_BUS_OFFS" in rs.replace("(", " ").replace(")", " ")


@toolchain
def test_rmw_named_local_end_to_end_orion(tmp_path):
    """Full record/replay on the real orion RMW: correct MATCHes its own C register
    trace, wrong-register control DIVERGEs (non-vacuous). This is the emit-gap close
    the whole change exists for."""
    out = str(tmp_path / "orion")
    ex = mh.generate(ORION, "orion_pcie_set_local_bus_nr", out)
    v1, l1 = mh.gate("orion_pcie_set_local_bus_nr", out, "orion_pcie_set_local_bus_nr_cand.rs")
    assert v1 == "MATCH", f"correct RMW candidate not MATCH: {l1}"
    v2, l2 = mh.gate("orion_pcie_set_local_bus_nr", out, "orion_pcie_set_local_bus_nr_bad.rs")
    assert v2 == "DIVERGE", f"RMW mutant did not diverge (self-match hole!): {l2}"


def test_read_into_non_val_local_declared():
    """A read into a local NOT named `val` (`tmp = readl(...)`) must record `tmp` as
    a declared local so the subsequent write of `tmp` resolves — the old emitter
    hardcoded `val` and left `tmp` undeclared (E0425)."""
    defs = {"REG": 0x20}
    body = (
        "static void f(void __iomem *base) { "
        "u32 tmp; tmp = readl(base + REG); writel(tmp, base + REG); }"
    )
    ex = mh.extract(body, "f", defs)
    assert _prog_seq(ex) == [("R", "REG"), ("W", "REG")]
    assert ex["locals"] == ["tmp"]
    rs = mh.emit_cand_rs(ex, False)
    assert "let mut tmp: u32 = 0" in rs
    assert "tmp = reg_read(REG)" in rs
    _assert_no_phantom_resolve(ex)


def test_input_param_mapped_to_pin():
    """The single non-iomem scalar param (the driven input) is mapped to `p` in
    every emitted expression; without it the param name would be undeclared."""
    defs = {"REG": 0x20, "SHIFT": 4}
    body = (
        "static void f(void __iomem *base, int nr) { "
        "u32 v; v = readl(base + REG); v |= nr << SHIFT; writel(v, base + REG); }"
    )
    ex = mh.extract(body, "f", defs)
    assert ex["input"] == "nr"
    c, rs = mh.emit_ref_c(ex), mh.emit_cand_rs(ex, False)
    # `nr` never survives — it is the pin `p` in both languages
    assert re.search(r"\bnr\b", c) is None
    assert re.search(r"\bnr\b", rs) is None
    assert "p << SHIFT" in c.replace("(", " ").replace(")", " ")


def test_compute_constant_emitted():
    """A #define used ONLY in a compute/value expression (not as an access offset)
    must still be emitted as a constant — else the Rust references an unknown name.
    Only referenced #defines are emitted (nothing phantom)."""
    defs = {"REG": 0x20, "USED_MASK": 0x3f, "UNUSED_MASK": 0x99}
    body = (
        "static void f(void __iomem *base) { "
        "u32 v; v = readl(base + REG); v &= USED_MASK; writel(v, base + REG); }"
    )
    ex = mh.extract(body, "f", defs)
    assert ex["const_defs"] == {"USED_MASK": 0x3f}   # UNUSED_MASK not emitted
    rs = mh.emit_cand_rs(ex, False)
    assert "const USED_MASK: u32 = 0x3f" in rs
    assert "UNUSED_MASK" not in rs


def test_write_to_struct_field_refuses():
    """A read/compute whose TARGET is struct/global state (`g->cache`, not a bare
    local) is an unmodellable effect AND would leak `->` into invalid Rust. Refuse
    rather than emit a broken/lossy program."""
    defs = {"REG": 0x20}
    read_tgt = (
        "static void f(struct s *g) { "
        "g->cache = readl(g->base + REG); writel(1, g->base + REG); }"
    )
    with pytest.raises(mh.Unsupported):
        mh.extract(read_tgt, "f", defs)


def test_unresolved_identifier_in_compute_refuses():
    """A compute/value expression referencing an identifier that is neither a
    declared local, the input, nor a resolvable #define (`stat |= nr << MYSTERY`)
    is possible hidden state — REFUSE rather than emit an undeclared reference."""
    defs = {"REG": 0x20}
    body = (
        "static void f(void __iomem *base, int nr) { "
        "u32 stat; stat = readl(base + REG); stat |= nr << MYSTERY; "
        "writel(stat, base + REG); }"
    )
    with pytest.raises(mh.Unsupported):
        mh.extract(body, "f", defs)


def test_opaque_call_in_value_refuses():
    """A written VALUE containing an opaque call (`writel(helper(stat), ...)`) can't
    be faithfully modelled — REFUSE (only BIT/hwirq are structurally translated)."""
    defs = {"REG": 0x20}
    body = (
        "static void f(void __iomem *base) { "
        "u32 stat; stat = readl(base + REG); writel(helper(stat), base + REG); }"
    )
    with pytest.raises(mh.Unsupported):
        mh.extract(body, "f", defs)
