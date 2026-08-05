#!/usr/bin/env python3
"""A2 — safety metrics comparable to the C→Rust lifting literature.

The field reports unsafe-LOC% and raw-pointer decl/deref counts (Laertes,
Crown, C2SaferRust, CRustS). We report the SAME numbers per woven fn, next to
our tier system, so results are comparable — and so the headline claim ("the
logic is in a machine-checked-safe context") is backed by a number, not an
adjective.

Metric on the FUNCTION source (fn_src / fn_src_safe), not the mirror/guard
scaffold:
  * unsafe_loc  — logic lines inside the `unsafe extern "C"` boundary fn body
  * safe_loc    — logic lines inside the `#![forbid(unsafe_code)]` core fn body
                  (tier-(a) has none: the whole body is the unsafe fn)
  * raw_deref   — raw-pointer field derefs `(*ident).` (the boundary's borrows)
  * raw_ptr_params — `*mut`/`*const` in signatures
  * unsafe_pct  — unsafe_loc / (unsafe_loc + safe_loc): fraction of the
                  translated LOGIC that is NOT machine-checked-safe.
                  tier-(b): small (only the boundary); tier-(a): 100%.

For a tier-(b) fn the logic lives in the forbid core (safe_loc = N) and the
unsafe boundary is a fixed ~2 lines (the core call + return), so unsafe_pct is
low and DROPS as the fn gets more complex — the opposite of a naive count.
"""
from __future__ import annotations

import re


def _logic_lines(block: str) -> int:
    """Non-blank, non-pure-brace source lines (the field's LOC convention)."""
    n = 0
    for ln in block.splitlines():
        s = ln.strip()
        if not s or s in ("{", "}", "};"):
            continue
        n += 1
    return n


def _body_of(src: str, header_re: str) -> str:
    """Brace-balanced body following the first header matching header_re."""
    m = re.search(header_re, src)
    if not m:
        return ""
    i = src.index("{", m.start())
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i + 1:j]
    return src[i + 1:]


def fn_metrics(fn_src: str, tier: str) -> dict:
    """Standard safety metrics for one translated function source."""
    core_body = _body_of(fn_src, r"pub fn core\s*\(") if tier.startswith("b") else ""
    boundary_body = _body_of(fn_src, r'pub unsafe extern "C" fn ')
    safe_loc = _logic_lines(core_body)
    unsafe_loc = _logic_lines(boundary_body)
    raw_deref = len(re.findall(r"\(\*[A-Za-z_]\w*\)\.", fn_src))
    raw_ptr_params = len(re.findall(r"\*(?:mut|const)\s", fn_src))
    denom = safe_loc + unsafe_loc
    unsafe_pct = (100.0 * unsafe_loc / denom) if denom else 0.0
    return {"tier": tier, "safe_loc": safe_loc, "unsafe_loc": unsafe_loc,
            "raw_deref": raw_deref, "raw_ptr_params": raw_ptr_params,
            "unsafe_pct": round(unsafe_pct, 1)}


def aggregate(rows: list[dict]) -> dict:
    """Fleet-level numbers for the dashboard."""
    if not rows:
        return {}
    n = len(rows)
    tot_safe = sum(r["safe_loc"] for r in rows)
    tot_unsafe = sum(r["unsafe_loc"] for r in rows)
    tot_logic = tot_safe + tot_unsafe
    tb = [r for r in rows if r["tier"].startswith("b")]
    return {
        "n": n,
        "tier_b": len(tb),
        "tier_a": n - len(tb),
        # THE headline: fraction of ALL translated logic in a machine-checked
        # safe context (0% before any lift; grows as the tier-b set grows).
        "safe_logic_pct": round(100.0 * tot_safe / tot_logic, 1) if tot_logic else 0.0,
        "fleet_unsafe_loc": tot_unsafe,
        "fleet_safe_loc": tot_safe,
        "mean_unsafe_pct_tier_b": round(sum(r["unsafe_pct"] for r in tb) / len(tb), 1) if tb else None,
        "total_raw_derefs": sum(r["raw_deref"] for r in rows),
    }


def format_dashboard(rows: list[dict]) -> str:
    a = aggregate(rows)
    if not a:
        return "  (no fns to measure)"
    return (
        f"  safety metrics (A2, comparable to Laertes/Crown/C2SaferRust):\n"
        f"    safe-logic %      : {a['safe_logic_pct']}%  "
        f"({a['fleet_safe_loc']} of {a['fleet_safe_loc']+a['fleet_unsafe_loc']} logic LOC in a "
        f"#![forbid(unsafe_code)] core)\n"
        f"    tier-b unsafe %   : mean {a['mean_unsafe_pct_tier_b']}% per fn "
        f"(only the field-scoped boundary is unsafe)\n"
        f"    raw-ptr derefs    : {a['total_raw_derefs']} total "
        f"(all in boundaries; one per accessed field, none in cores)\n"
        f"    fleet unsafe LOC  : {a['fleet_unsafe_loc']} (boundaries) "
        f"vs {a['fleet_safe_loc']} safe (cores)")


if __name__ == "__main__":
    # self-demo on the two shapes
    tier_b = '''mod f_safe_core {
    #![forbid(unsafe_code)]
    pub fn core(f_x: &mut i32) -> i64 {
        { (*f_x) = (((*f_x) as i64) - 1) as i32; };
        0
    }
}
#[no_mangle]
pub unsafe extern "C" fn f_rs(p: *mut XMirror) {
    let __r: i64 = f_safe_core::core(&mut (*p).x);
    let _ = __r;
}'''
    tier_a = '''#[no_mangle]
pub unsafe extern "C" fn f_rs(p: *mut XMirror) {
    let __r: i64 = { (*p).x = (((*p).x as i64) - 1) as i32; 0 };
    let _ = __r;
}'''
    print("tier-b:", fn_metrics(tier_b, "b-safe-core"))
    print("tier-a:", fn_metrics(tier_a, "a-mirror"))
    print(format_dashboard([fn_metrics(tier_b, "b-safe-core"),
                            fn_metrics(tier_a, "a-mirror")]))
