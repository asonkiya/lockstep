#!/usr/bin/env python3
"""Adversarial battery for the purity router (dream/widerun/purity.py).

The purity classifier is the most soundness-critical component in the wide
pipeline: a *false-PURE* (an effectful/state-reading body called "pure") routes
that function to the scalar value-differential, where an effect-dropping
candidate can pass — the `__refrigerator` / `probe_irq_mask` over-credit class.
A false-IMPURE is merely conservative (quarantined to the trace oracle), which
is safe. So this battery hunts for false-PURE: the headline assertion is that
ZERO body in MUST_BE_IMPURE is classified "pure".

Self-contained: the bodies are literal strings, so no KSRC / kernel tree is
needed. The `pure_names` fixpoint set is empty for these self-contained bodies
(they call no other harvested leaf); each function's own name is passed as
`own` where it recurses.
"""
from __future__ import annotations

import os
import sys

import pytest

# import purity from dream/widerun
_HERE = os.path.dirname(os.path.abspath(__file__))
_WIDERUN = os.path.normpath(os.path.join(_HERE, "..", "widerun"))
sys.path.insert(0, _WIDERUN)

import purity  # noqa: E402


# --------------------------------------------------------------------------
# MUST_BE_IMPURE — every one of these reads or writes global/nonlocal state,
# calls something opaque, or has an observable effect, in a varied/sneaky way.
# Each MUST classify "impure". If any comes back "pure" it is a false-PURE and
# a potential UNSOUND pass in the wide pipeline.
# (name, body, own)
# --------------------------------------------------------------------------
MUST_BE_IMPURE: list[tuple[str, str, str]] = [
    # --- bare global reads (whitelist must reject the unresolved identifier) ---
    ("bare_global_read",
     "int f(void) { return counter; }", "f"),
    ("global_read_in_expr",
     "int f(int x) { return x + counter; }", "f"),
    ("global_flag_read",
     "int f(int x) { return enabled ? x : -1; }", "f"),
    ("static_key_var_read",
     "int f(int x) { return static_key_enabled(&mykey) ? x : 0; }", "f"),
    ("static_branch_var_read",
     "int f(int x) { if (static_branch_unlikely(&my_key)) return x + 1; return x; }", "f"),
    ("lookup_table_read",
     "int f(int x) { return sbox[x]; }", "f"),
    ("lookup_table_read_2d",
     "int f(int a, int b) { return crc_table[a][b]; }", "f"),

    # --- global WRITES via plain assignment / inc / compound (no kernel marker) ---
    ("global_increment",
     "int f(int x) { counter++; return x; }", "f"),
    ("global_preincrement",
     "int f(int x) { ++counter; return x; }", "f"),
    ("global_compound_add",
     "int f(int x) { total += x; return total; }", "f"),
    ("global_plain_assign",
     "void f(int x) { state = x; }", "f"),
    ("global_array_index_write",
     "void f(int i, int v) { table[i] = v; }", "f"),
    ("global_array_compound_write",
     "void f(int i, int v) { histogram[i] += v; }", "f"),

    # --- opaque / effectful callee ---
    ("unknown_helper_call",
     "int f(int x) { return do_thing(x); }", "f"),
    ("unknown_helper_nested",
     "int f(int x) { return 1 + compute_something(x, x); }", "f"),

    # --- explicit kernel state/effect markers ---
    ("readl_mmio",
     "u32 f(void) { return readl(REG_BASE); }", "f"),
    ("writel_mmio",
     "void f(u32 v) { writel(v, REG_BASE); }", "f"),
    ("write_once",
     "void f(int v) { WRITE_ONCE(g, v); }", "f"),
    ("read_once",
     "int f(void) { return READ_ONCE(g); }", "f"),
    ("atomic_inc",
     "void f(void) { atomic_inc(&c); }", "f"),
    ("percpu_read",
     "int f(void) { return this_cpu_read(x); }", "f"),
    ("smp_processor_id",
     "int f(void) { return smp_processor_id(); }", "f"),
    ("jiffies_clock",
     "unsigned long f(void) { return jiffies; }", "f"),
    ("printk_effect",
     "void f(int x) { printk(\"x=%d\", x); }", "f"),
    ("kmalloc_effect",
     "void *f(int n) { return kmalloc(n, 0); }", "f"),
    ("spin_lock_effect",
     "void f(void) { spin_lock(&l); }", "f"),
    ("get_random_state",
     "u32 f(void) { return get_random_u32(); }", "f"),
    ("deref_struct_arrow",
     "int f(void) { return g->field; }", "f"),
]


# --------------------------------------------------------------------------
# MUST_BE_PURE — genuinely pure bodies: arithmetic/bit ops on params only,
# ALL_CAPS compile-time constants, locals, and known-pure helpers.
# Each MUST classify "pure". (A false-IMPURE here would be conservative/safe,
# but we still pin these so the classifier doesn't drift into rejecting real
# pure leaves and shrinking the scalar-verifiable set.)
# (name, body, own)
# --------------------------------------------------------------------------
MUST_BE_PURE: list[tuple[str, str, str]] = [
    ("arith_on_params",
     "int f(int x) { return x * 2 + 1; }", "f"),
    ("arith_two_params",
     "int f(int a, int b) { return a * b - a + b; }", "f"),
    ("bit_ops",
     "u32 f(u32 x) { return (x << 3) | (x >> 5); }", "f"),
    ("allcaps_mask_const",
     "int f(int x) { return x & MASK; }", "f"),
    ("allcaps_multiple_consts",
     "u32 f(u32 x) { return (x & LOW_MASK) | (x & HIGH_MASK); }", "f"),
    ("local_temp",
     "u32 f(u32 x) { u32 v = x * 2; return v; }", "f"),
    ("local_temp_chain",
     "int f(int x) { int a = x + 1; int b = a * 2; return b - x; }", "f"),
    ("pure_helper_min",
     "int f(int a, int b) { return min(a, b); }", "f"),
    ("pure_helper_clamp",
     "int f(int x) { return clamp(x, 0, 100); }", "f"),
    ("pure_helper_hweight",
     "int f(u32 x) { return hweight32(x); }", "f"),
    ("pure_helper_genmask",
     "u32 f(void) { return GENMASK(5, 0); }", "f"),
    ("cond_expr_params_only",
     "int f(int x, int y) { return x > y ? x : y; }", "f"),
    ("loop_params_locals_only",
     "int f(int n) { int s = 0; for (int i = 0; i < n; i++) { s += i; } return s; }", "f"),
    ("self_recursion",
     "int f(int n) { return n <= 1 ? 1 : n * f(n - 1); }", "f"),
]


# empty fixpoint set — these bodies are self-contained and call no harvested leaf
PURE_NAMES: set[str] = set()


@pytest.mark.parametrize("name,body,own", MUST_BE_IMPURE, ids=[t[0] for t in MUST_BE_IMPURE])
def test_must_be_impure(name: str, body: str, own: str) -> None:
    verdict, why = purity.classify(body, PURE_NAMES, own)
    assert verdict == "impure", (
        f"FALSE-PURE (unsound): {name!r} was classified pure but reads/writes "
        f"state or has an effect. reason={why!r}\n  body: {body}"
    )


@pytest.mark.parametrize("name,body,own", MUST_BE_PURE, ids=[t[0] for t in MUST_BE_PURE])
def test_must_be_pure(name: str, body: str, own: str) -> None:
    verdict, why = purity.classify(body, PURE_NAMES, own)
    assert verdict == "pure", (
        f"FALSE-IMPURE (conservative, but pins drift): {name!r} was classified "
        f"impure though it touches only params/locals/consts/pure-helpers. "
        f"reason={why!r}\n  body: {body}"
    )


def test_zero_false_pure_headline() -> None:
    """Headline soundness assertion: NOT ONE body in MUST_BE_IMPURE may be
    classified pure. A single false-PURE is a potential unsound pass in the
    wide pipeline (effect-dropping candidate slips through the scalar diff)."""
    false_pures = []
    for name, body, own in MUST_BE_IMPURE:
        verdict, why = purity.classify(body, PURE_NAMES, own)
        if verdict != "impure":
            false_pures.append((name, why))
    assert not false_pures, (
        f"{len(false_pures)} FALSE-PURE case(s) — potential unsound pass(es): "
        f"{false_pures}"
    )


# --------------------------------------------------------------------------
# Regression cases mirroring the real widerun set.
# --------------------------------------------------------------------------

def test_regression_gcd_reading_static_branch_global_is_impure() -> None:
    """A gcd-like body that reads a static-branch global must be IMPURE.

    The static_branch_* CALL is whitelisted (read-only branch selector), but
    the *key variable* it reads (`gcd_key` here) is a bare global — unresolved,
    lowercase => state. This is the exact class the whitelist hardening added:
    the call alone looks pure, the global read is what convicts it."""
    body = (
        "unsigned long f(unsigned long a, unsigned long b) {\n"
        "    if (static_branch_likely(&gcd_key)) {\n"
        "        while (b) { unsigned long t = b; b = a % b; a = t; }\n"
        "    }\n"
        "    return a;\n"
        "}"
    )
    verdict, why = purity.classify(body, PURE_NAMES, "f")
    assert verdict == "impure", f"static-branch-global gcd wrongly pure: {why!r}"


def test_regression_refrigerator_over_credit_is_impure() -> None:
    """The `__refrigerator` over-credit class: an effectful body whose return
    value can be reproduced by an effect-dropping candidate MUST be quarantined
    (impure), never routed to the scalar differential."""
    body = "bool f(bool check_kthr_stop) { return __refrigerator(check_kthr_stop); }"
    verdict, why = purity.classify(body, PURE_NAMES, "f")
    assert verdict == "impure", f"__refrigerator over-credit wrongly pure: {why!r}"


def test_regression_pure_gcd_is_pure() -> None:
    """Sanity floor: the actual pure gcd (params + locals only, no global) is
    pure — so the hardening didn't over-reject the real scalar-verifiable leaf."""
    body = (
        "unsigned long f(unsigned long a, unsigned long b) {\n"
        "    while (b) { unsigned long t = b; b = a % b; a = t; }\n"
        "    return a;\n"
        "}"
    )
    verdict, why = purity.classify(body, PURE_NAMES, "f")
    assert verdict == "pure", f"pure gcd wrongly impure: {why!r}"
