"""Gate the REAL struct-driven branch harness (dream/structdiff/harness.py) on
actual kernel functions: re-emit the C reference from cfg.py's block tree with
coverage instrumentation, build the struct from the mirror, sweep fields, and
differentially compare return + out-param + struct bytes against a Rust
candidate. Correct candidate -> MATCH; a wrong one -> DIVERGE.

Skipped without host cc/rustc or the kernel source tree.
"""
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "structdiff"))

_HARNESS = None
try:
    import harness as _HARNESS  # noqa: E402
except Exception:
    _HARNESS = None

pytestmark = pytest.mark.skipif(
    not (shutil.which("cc") and shutil.which("rustc") and _HARNESS
         and os.path.isdir(_HARNESS.KSRC)),
    reason="needs host cc + rustc + kernel source (KSRC)",
)

LINEAR_OK = """
#[repr(C)]
pub struct LinearRange { pub min: u32, pub min_sel: u32, pub max_sel: u32, pub step: u32 }
#[no_mangle]
pub extern "C" fn linear_range_get_value_rs(r: *const LinearRange, selector: u32, val: *mut u32) -> i32 {
    let r = unsafe { &*r };
    if r.min_sel > selector || r.max_sel < selector { return -22; }
    unsafe { *val = r.min.wrapping_add((selector - r.min_sel).wrapping_mul(r.step)); }
    0
}
"""

SUBPOOL_OK = """
#[repr(C)]
pub struct HugepageSubpool { pub lock: [u64;8], pub count: i64, pub max_hpages: i64,
    pub used_hpages: i64, pub hstate: *mut core::ffi::c_void, pub min_hpages: i64, pub rsv_hpages: i64 }
#[no_mangle]
pub extern "C" fn subpool_is_free_rs(s: *const HugepageSubpool) -> bool {
    let s = unsafe { &*s };
    if s.count != 0 { return false; }
    if s.max_hpages != -1 { return s.used_hpages == 0; }
    if s.min_hpages != -1 { return s.rsv_hpages == s.min_hpages; }
    true
}
"""


def _close(tmp, tag, rel, fn, cand):
    return _HARNESS.close(rel, fn, cand, os.path.join(tmp, tag))[0]


def test_linear_range_close_and_sabotage():
    with tempfile.TemporaryDirectory() as tmp:
        assert _close(tmp, "ok", "lib/linear_ranges.c",
                      "linear_range_get_value", LINEAR_OK) == "MATCH"
        wrong = LINEAR_OK.replace("r.min_sel > selector", "r.min_sel >= selector")
        assert _close(tmp, "bad", "lib/linear_ranges.c",
                      "linear_range_get_value", wrong) == "DIVERGE"


def test_subpool_is_free_close_and_sabotage():
    # struct carries a spinlock_t (opaque-primitive-sized mirror) + a pointer.
    with tempfile.TemporaryDirectory() as tmp:
        assert _close(tmp, "ok", "mm/hugetlb.c",
                      "subpool_is_free", SUBPOOL_OK) == "MATCH"
        wrong = SUBPOOL_OK.replace("s.rsv_hpages == s.min_hpages",
                                   "s.rsv_hpages != s.min_hpages")
        assert _close(tmp, "bad", "mm/hugetlb.c",
                      "subpool_is_free", wrong) == "DIVERGE"
