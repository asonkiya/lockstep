"""Gate the PRODUCTIZED effect-trace oracle (dream/efftrace/harness.py).

proof.py proved the ordered record/replay mechanism on a synthetic subject; the
harness runs a per-call FULL-FOOTPRINT state differential against a REAL kernel
function verbatim (rb_set_black, lib/rbtree.c). Pinned contract:

  * reach.gate accepts it with resolved footprint (param-struct scalar field +
    the RB_BLACK define);
  * correct body (+= RB_BLACK) -> MATCH;
  * over-credit sabotage (right void return, untouched state) -> DIVERGE:state
    — the exact case a return-only oracle false-passes;
  * the plausible |=-for-+= mistranslation -> DIVERGE:state (agrees on first
    touch, caught on the second application to the same node — this exact bug
    was caught LIVE while writing the hand candidate);
  * an empty workload -> REFUSED_COVERAGE even for the correct body.

Needs host cc + rustc + the kernel tree at $KSRC; skipped otherwise.
"""
import copy
import importlib.util
import os
import shutil

import pytest

_D = os.path.join(os.path.dirname(__file__), "..", "efftrace")


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_D, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_R = _H = None
try:
    # unique module names — several oracle dirs ship same-named modules.
    _R = _load("eff_reach_t", "reach.py")
    _H = _load("eff_harness_t", "harness.py")
except Exception:
    pass

pytestmark = pytest.mark.skipif(
    not (shutil.which("cc") and shutil.which("rustc") and _R and _H
         and os.path.isdir(os.environ.get(
             "KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux"))),
    reason="needs host cc + rustc + $KSRC kernel tree",
)


@pytest.fixture(scope="module")
def prep():
    rec = _R.gate(*_H._CANON)
    return _H.prepare(rec)


def test_gate_record(prep):
    rec = prep["rec"]
    assert rec["defines"] == {"RB_BLACK": 1}
    assert rec["write_fields"] == ["rb->__rb_parent_color"]
    assert prep["widx"], "write-target cells must be identified"


def test_correct_matches(prep):
    r = _H.close(prep, _H._CANON_BODIES["correct"])
    assert r["verdict"] == "MATCH", r


def test_over_credit_diverges(prep):
    r = _H.close(prep, _H._CANON_BODIES["over_credit"])
    assert r["verdict"] == "DIVERGE:state", r


def test_or_for_add_mistranslation_diverges(prep):
    r = _H.close(prep, _H._CANON_BODIES["or_not_add"])
    assert r["verdict"] == "DIVERGE:state", r


def test_empty_workload_refuses_coverage(prep):
    starved = copy.deepcopy(prep)
    starved["rounds"] = [{"seeds": [], "calls": []}]
    r = _H.close(starved, _H._CANON_BODIES["correct"])
    assert r["verdict"] == "REFUSED_COVERAGE", r
