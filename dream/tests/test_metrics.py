"""A2 safety metrics (dream/realize/metrics.py) — comparable to the C→Rust
lifting literature (unsafe-LOC% / raw-pointer counts). Pins:
  * a tier-(b) safe core has its logic in the forbid module (safe_loc > 0) and
    the boundary is the only unsafe surface;
  * a tier-(a) mirror fn has ZERO safe logic (the whole body is the unsafe fn);
  * raw-ptr derefs count the boundary's field borrows (never the core);
  * the fleet aggregate's safe-logic% is a real fraction in [0, 100].
Pure-Python, no toolchain — always runs.
"""
import importlib.util
import os

_HERE = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location(
    "metrics_t", os.path.join(_HERE, "..", "realize", "metrics.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

_TIER_B = '''mod f_safe_core {
    #![forbid(unsafe_code)]
    pub fn core(f_a: &mut i32, f_b: &mut u32) -> i64 {
        { (*f_a) = (((*f_a) as i64) + 1) as i32; };
        { (*f_b) = (((*f_b) as i64) * 2) as u32; };
        0
    }
}
#[no_mangle]
pub unsafe extern "C" fn f_rs(p: *mut XMirror) {
    let __r: i64 = f_safe_core::core(&mut (*p).a, &mut (*p).b);
    let _ = __r;
}'''

_TIER_A = '''#[no_mangle]
pub unsafe extern "C" fn f_rs(p: *mut XMirror) {
    let __r: i64 = {
        (*p).a = (((*p).a as i64) + 1) as i32;
        (*p).b = (((*p).b as i64) * 2) as u32;
        0
    };
    let _ = __r;
}'''


def test_tier_b_logic_is_in_the_forbid_core():
    m = M.fn_metrics(_TIER_B, "b-safe-core")
    assert m["safe_loc"] >= 2          # the two field updates live in the core
    # ONE field borrow per accessed field, in the boundary; the core's `(*f_a)`
    # are safe REFERENCE derefs (no `.`), not raw-pointer derefs.
    assert m["raw_deref"] == 2
    assert m["unsafe_pct"] < 100.0     # not everything is unsafe


def test_lift_reduces_and_confines_raw_derefs():
    # tier-(a) re-derefs the raw pointer for EVERY field access (read + write);
    # the lift borrows each field ONCE in the boundary and the core uses safe
    # refs → fewer raw derefs, all confined to the boundary.
    a = M.fn_metrics(_TIER_A, "a-mirror")
    b = M.fn_metrics(_TIER_B, "b-safe-core")
    assert a["raw_deref"] > b["raw_deref"]     # 4 -> 2 here
    assert a["safe_loc"] == 0                  # tier-a: no machine-checked logic
    assert a["unsafe_pct"] == 100.0


def test_aggregate_is_a_real_fraction():
    rows = [M.fn_metrics(_TIER_B, "b-safe-core"), M.fn_metrics(_TIER_A, "a-mirror")]
    a = M.aggregate(rows)
    assert a["n"] == 2 and a["tier_b"] == 1 and a["tier_a"] == 1
    assert 0.0 < a["safe_logic_pct"] < 100.0     # some safe, some not
    assert a["fleet_safe_loc"] > 0 and a["fleet_unsafe_loc"] > 0
    assert a["total_raw_derefs"] == 6            # 2 (tier-b) + 4 (tier-a)


def test_safe_logic_pct_rises_when_a_tier_a_is_lifted():
    # lifting a fn (a -> b) can only INCREASE the fleet safe-logic fraction
    before = M.aggregate([M.fn_metrics(_TIER_A, "a-mirror"),
                          M.fn_metrics(_TIER_A, "a-mirror")])["safe_logic_pct"]
    after = M.aggregate([M.fn_metrics(_TIER_A, "a-mirror"),
                         M.fn_metrics(_TIER_B, "b-safe-core")])["safe_logic_pct"]
    assert after > before
