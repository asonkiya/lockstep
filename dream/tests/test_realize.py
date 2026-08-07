"""Model->real realization for efftrace candidates (dream/realize/realize.py).

A sweep-verified efftrace candidate's logic lives in a closed helper vocabulary
over a cell model whose indices were derived from the real struct fields, so it
transpiles deterministically to a real-struct function — which is then re-gated
by the SAME differential the model passed (zero-trust transpiler). Pins:
  * the transpile emits a real-signature fn with the verified value-logic;
  * the realized fn passes the full efftrace differential (MATCH);
  * the differential is LOAD-BEARING over the realized output — a corrupted
    store must DIVERGE (transpiler bugs cannot certify);
  * out-of-vocabulary bodies are REFUSED, not mis-translated.
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
        "realize_t", os.path.join(_HERE, "..", "realize", "realize.py"))
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
def realized():
    rec, prep, tr = _R.realize(_FILE, _FN)
    return rec, prep, tr


def test_transpile_shape(realized):
    _, _, tr = realized
    src = tr["fn_src"]
    # real signature: no_mangle extern "C", mirror-typed pointer param
    assert f'extern "C" fn {_FN}_rs(' in src
    assert "*mut Block_deviceMirror" in src
    # the verified decrement survives, retargeted at the real field
    assert re.search(r"\(\*bdev\)\.bd_writers\s*=", src)
    # arithmetic is emitted in EXPLICIT wrapping form (A4): the decrement
    # survives as wrapping_sub, not a bare `-` (which would panic on overflow
    # with checks on, i.e. hang a freestanding kernel object).
    flat = src.replace("\n", " ")
    assert "wrapping_sub" in flat and "bd_writers" in flat
    # no cell-model residue
    assert "S[" not in src and "set_field" not in src


def test_realized_passes_the_same_differential(realized):
    rec, prep, tr = realized
    r = _R.close_realized(prep, _R.rust_host_tu(rec, prep, tr))
    assert r["verdict"] == "MATCH", r


def test_differential_is_load_bearing_over_realized_output(realized):
    # corrupt the realized store by +1 (a transpiler-bug surrogate): the SAME
    # differential must now DIVERGE — certification never rests on the
    # transpiler being correct.
    rec, prep, tr = realized
    m = re.search(r"= (\(.*\)) as (\w+);", tr["fn_src"])
    assert m, "expected a store to sabotage"
    sab = dict(tr)
    sab["fn_src"] = tr["fn_src"].replace(
        m.group(0), f"= ({m.group(1)} + 1) as {m.group(2)};", 1)
    r = _R.close_realized(prep, _R.rust_host_tu(rec, prep, sab))
    assert r["verdict"].startswith("DIVERGE"), r


def test_out_of_vocabulary_is_refused(realized):
    rec, _, _ = realized
    for bad in ("unsafe { S[0] }\n0", "return 3;\n0",
                "set_field(F0_BD_WRITERS, a0 + 1, 1);\n0"):
        with pytest.raises(_R.Refused):
            _R.transpile(rec, bad)
