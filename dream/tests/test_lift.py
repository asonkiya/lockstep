"""Tier-(b) safety lift for realized efftrace fns (realize.py fn_src_safe).

The lift splits a realized fn into a MACHINE-CHECKED safe core (a
#![forbid(unsafe_code)] module operating on &mut Mirror — rustc proves no raw
pointers) plus a boundary whose entire unsafe surface is one `&mut *p` deref.
Pins:
  * shape: forbid(unsafe_code) present; exactly ONE unsafe block (the deref);
    no raw-pointer access inside the core;
  * the lifted form passes the SAME differential (MATCH) — behavior is pinned
    across the safety lift, not assumed;
  * the differential is load-bearing over the lift (sabotage -> DIVERGE);
  * multi-node fns are NOT liftable (two &mut from two pointers could alias).
Needs cc + rustc + $KSRC + the banked candidate; skipped otherwise.
"""
import importlib.util
import os
import re
import shutil

import pytest

_HERE = os.path.dirname(__file__)
_KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
_CAND = os.path.join(_HERE, "..", "firstrun", "verified",
                     "efftrace_block__bdev.c_bdev_block_writes.rs")

_R = None
try:
    _spec = importlib.util.spec_from_file_location(
        "realize_lt", os.path.join(_HERE, "..", "realize", "realize.py"))
    _R = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_R)
except Exception:
    _R = None

pytestmark = pytest.mark.skipif(
    not (shutil.which("cc") and shutil.which("rustc") and _R
         and os.path.isdir(_KSRC) and os.path.exists(_CAND)),
    reason="needs cc + rustc + $KSRC + the bdev_block_writes verified candidate")

_FILE, _FN = "block/bdev.c", "bdev_block_writes"


@pytest.fixture(scope="module")
def lifted():
    rec, prep, tr = _R.realize(_FILE, _FN)
    return rec, prep, tr


def test_lift_shape(lifted):
    _, _, tr = lifted
    assert tr["liftable"]
    src = tr["fn_src_safe"]
    assert "#![forbid(unsafe_code)]" in src
    # entire unsafe surface = the boundary: `unsafe extern "C" fn` + ONE block
    assert src.count("unsafe {") == 1
    assert "&mut *" in src
    # the core body operates on references, never raw pointers
    core = src.split("#[no_mangle]")[0]
    assert "*mut" not in core and "(*" not in core


def test_lifted_passes_the_same_differential(lifted):
    rec, prep, tr = lifted
    r = _R.close_realized(prep, _R.rust_host_tu(rec, prep, tr, safe=True))
    assert r["verdict"] == "MATCH", r


def test_differential_is_load_bearing_over_the_lift(lifted):
    rec, prep, tr = lifted
    m = re.search(r"= (\(.*\)) as (\w+);", tr["fn_src_safe"])
    assert m, "expected a store in the safe core to sabotage"
    sab = dict(tr)
    sab["fn_src_safe"] = tr["fn_src_safe"].replace(
        m.group(0), f"= ({m.group(1)} + 1) as {m.group(2)};", 1)
    r = _R.close_realized(prep, _R.rust_host_tu(rec, prep, sab, safe=True))
    assert r["verdict"].startswith("DIVERGE"), r


def test_forbid_is_load_bearing():
    # planting a raw-pointer deref INSIDE the forbid(unsafe_code) module must
    # fail rustc — the "safe core" claim is machine-checked, not naming.
    rec, prep, tr = _R.realize(_FILE, _FN)
    sab = dict(tr)
    sab["fn_src_safe"] = tr["fn_src_safe"].replace(
        "pub fn core(", "pub fn smuggle(p: *mut i32) { unsafe { *p = 1; } }\n    pub fn core(", 1)
    r = _R.close_realized(prep, _R.rust_host_tu(rec, prep, sab, safe=True))
    assert r["verdict"] == "BUILD_FAIL_RS", r


def test_multi_node_not_liftable():
    # any banked candidate with 2 node params must refuse the lift
    import json
    rows = [json.loads(l) for l in
            open(os.path.join(_HERE, "..", "realize", "census.jsonl"))]
    multi = [r["key"] for r in rows if r.get("n_node", 0) >= 2 and r["result"] == "MATCH"]
    if not multi:
        pytest.skip("no multi-node MATCH candidate banked")
    f, fn = multi[0].rsplit(":", 1)
    _rec, tr = _R.realize_light(f, fn)
    assert not tr["liftable"]
