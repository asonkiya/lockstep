#!/usr/bin/env python3
"""The false-pass hunt — the centerpiece soundness test.

The project's whole claim is "zero false passes": a WRONG Rust candidate is never
credited as equivalent to the C. This pins that as a reproducible assertion. For
each real kernel function it throws a battery of deliberately-wrong candidates at
the hostdiff (T0) oracle and asserts NONE of them yields MATCH — every documented
way a wrong candidate could sneak through (delegation to the C original, dropped
effect, wrong algorithm, off-by-one, constant return, identity, non-termination),
plus a correct candidate that MUST match so the oracle isn't vacuously rejecting
everything.

Headline assertion: across the entire wrong battery, false_passes == 0.

Toolchain-gated: skips cleanly if rustc/cc are missing.
"""
from __future__ import annotations

import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "dream", "hostdiff"))
sys.path.insert(0, os.path.join(REPO, "dream", "cluster"))
import hostdiff  # noqa: E402

KSRC = os.environ.get("KSRC", hostdiff.KSRC_DEFAULT)
NRAND = 300_000

pytestmark = pytest.mark.skipif(
    not (shutil.which("rustc") and shutil.which("cc")),
    reason="needs rustc + cc",
)


def _run(path, func, code, deps=(), timeout=20):
    d = f"/private/tmp/soundtest_{func}"
    os.makedirs(d, exist_ok=True)
    cand = f"{d}/cand.rs"
    open(cand, "w").write(code)
    return hostdiff.run(path, func, cand, list(deps), KSRC, NRAND,
                        quiet=True, probe_timeout=timeout)["verdict"]


# ---- target 1: gcd (has a cross-TU dep -> the delegation attack surface) ----
GCD = "lib/math/gcd.c"
GCD_CORRECT = """
#[no_mangle]
pub extern "C" fn cgir_gcd(mut a: u64, mut b: u64) -> u64 {
    while b != 0 { let t = a % b; a = b; b = t; }
    a
}
"""
GCD_WRONG = {
    "delegation": """
extern "C" { fn gcd(a: u64, b: u64) -> u64; }
#[no_mangle] pub extern "C" fn cgir_gcd(a: u64, b: u64) -> u64 { unsafe { gcd(a, b) } }
""",
    "constant": """
#[no_mangle] pub extern "C" fn cgir_gcd(_a: u64, _b: u64) -> u64 { 1 }
""",
    "identity": """
#[no_mangle] pub extern "C" fn cgir_gcd(a: u64, _b: u64) -> u64 { a }
""",
    "off_by_one": """
#[no_mangle] pub extern "C" fn cgir_gcd(mut a: u64, mut b: u64) -> u64 {
    while b != 0 { let t = a % b; a = b; b = t; } a + 1
}
""",
    "wrong_algo_lcm": """
#[no_mangle] pub extern "C" fn cgir_gcd(a: u64, b: u64) -> u64 {
    if a == 0 || b == 0 { return 0; }
    let mut x=a; let mut y=b; while y!=0 { let t=x%y; x=y; y=t; } a / x * b
}
""",
    "nonterminating": """
#[no_mangle] pub extern "C" fn cgir_gcd(mut a: u64, mut b: u64) -> u64 {
    // subtraction gcd -> underflow/near-infinite loop on u64::MAX vs 1
    while a != b { if a < b { b = b.wrapping_sub(a); } else { a = a.wrapping_sub(b); } } a
}
""",
}

# ---- target 2: int_pow (pure arithmetic, overflow-sensitive) ----
POW = "lib/math/int_pow.c"
POW_CORRECT = """
#[no_mangle]
pub extern "C" fn cgir_int_pow(mut base: u64, mut exp: u32) -> u64 {
    let mut r: u64 = 1;
    while exp != 0 {
        if exp & 1 == 1 { r = r.wrapping_mul(base); }
        exp >>= 1; base = base.wrapping_mul(base);
    }
    r
}
"""
POW_WRONG = {
    "constant": """
#[no_mangle] pub extern "C" fn cgir_int_pow(_b: u64, _e: u32) -> u64 { 1 }
""",
    "no_wrap_but_wrong": """
#[no_mangle] pub extern "C" fn cgir_int_pow(base: u64, exp: u32) -> u64 {
    // linear mul, but forgets the squaring -> wrong for exp>=2
    let mut r: u64 = 1; let mut i = 0u32;
    while i < exp { r = r.wrapping_mul(base); i += 1; if i > 3 { break; } } r
}
""",
    "off_by_one_exp": """
#[no_mangle] pub extern "C" fn cgir_int_pow(mut base: u64, mut exp: u32) -> u64 {
    let mut r: u64 = 1; exp = exp.wrapping_add(1);
    while exp != 0 { if exp & 1 == 1 { r = r.wrapping_mul(base); } exp >>= 1; base = base.wrapping_mul(base); } r
}
""",
}

# ---- target 3: __sw_hweight32 (bit population count) ----
HW = "lib/hweight.c"
HW_FUNC = "__sw_hweight32"
HW_CORRECT = """
#[no_mangle]
pub extern "C" fn cgir_sw_hweight32(mut w: u32) -> u32 {
    w = w - ((w >> 1) & 0x55555555);
    w = (w & 0x33333333) + ((w >> 2) & 0x33333333);
    w = (w + (w >> 4)) & 0x0f0f0f0f;
    w.wrapping_mul(0x01010101) >> 24
}
"""
HW_WRONG = {
    "constant": '#[no_mangle] pub extern "C" fn cgir_sw_hweight32(_w: u32) -> u32 { 16 }',
    "identity": '#[no_mangle] pub extern "C" fn cgir_sw_hweight32(w: u32) -> u32 { w }',
    "leading_zeros": '#[no_mangle] pub extern "C" fn cgir_sw_hweight32(w: u32) -> u32 { w.leading_zeros() }',
    "off_by_one": """
#[no_mangle] pub extern "C" fn cgir_sw_hweight32(mut w: u32) -> u32 {
    w = w - ((w >> 1) & 0x55555555);
    w = (w & 0x33333333) + ((w >> 2) & 0x33333333);
    w = (w + (w >> 4)) & 0x0f0f0f0f;
    (w.wrapping_mul(0x01010101) >> 24) + 1
}
""",
}

TARGETS = [
    # gcd is self-contained (calls only file-static binary_gcd) — no deps; adding
    # its own file as a dep would duplicate-define gcd and LINK_FAIL spuriously.
    (GCD, "gcd", GCD_CORRECT, GCD_WRONG, []),
    (POW, "int_pow", POW_CORRECT, POW_WRONG, []),
    (HW, HW_FUNC, HW_CORRECT, HW_WRONG, []),
]


@pytest.mark.parametrize("path,func,correct,_wrong,deps", TARGETS,
                         ids=[t[1] for t in TARGETS])
def test_correct_candidate_matches(path, func, correct, _wrong, deps):
    """The oracle isn't vacuous: a correct candidate MUST match."""
    v = _run(path, func, correct, deps)
    assert v == "MATCH", f"{func}: correct candidate got {v}, expected MATCH"


@pytest.mark.parametrize("path,func,_c,wrong,deps", TARGETS,
                         ids=[t[1] for t in TARGETS])
def test_no_wrong_candidate_matches(path, func, _c, wrong, deps):
    """The headline: NO wrong candidate is credited as MATCH."""
    false_passes = []
    for name, code in wrong.items():
        v = _run(path, func, code, deps)
        if v == "MATCH":
            false_passes.append(name)
    assert not false_passes, f"{func}: FALSE PASS on wrong candidates {false_passes}"


def test_zero_false_passes_aggregate():
    """Cross-target: count MATCHes across the entire wrong battery — must be 0."""
    total_wrong = 0
    false_passes = 0
    detail = []
    for path, func, _c, wrong, deps in TARGETS:
        for name, code in wrong.items():
            total_wrong += 1
            v = _run(path, func, code, deps)
            detail.append(f"{func}/{name}={v}")
            if v == "MATCH":
                false_passes += 1
    print(f"\nfalse-pass battery: {total_wrong} wrong candidates, {false_passes} false passes")
    print("  " + "  ".join(detail))
    assert false_passes == 0, f"{false_passes}/{total_wrong} wrong candidates falsely MATCHed"


def test_delegation_specifically_caught():
    """The delegation hole (forward to the C original) must be its own verdict,
    not a silent MATCH — pins the no-delegation gate."""
    v = _run(GCD, "gcd", GCD_WRONG["delegation"], [])
    assert v == "DELEGATION", f"delegation candidate got {v}, expected DELEGATION"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s"]))
