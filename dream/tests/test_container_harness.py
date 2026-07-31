"""Gate the PRODUCTIZED container-ADT oracle (dream/container_adt/harness.py).

proof.py proved the mechanism on a synthetic subject; the harness runs the same
representation-independent ADT differential against a REAL kernel function
taken verbatim from the tree (module_unload_ei_list — the canonical destroy
pattern: iterate a static-global list under a mutex, filter on a token field,
del + kfree). The contract pinned here:

  * reach.gate accepts the canonical fn with the structured record the harness
    needs (iter_node shape, global anchor, token param + token field, flags).
  * correct ADT-model body -> MATCH;
  * wrong-filter and dropped-guard sabotages -> DIVERGE:adt;
  * dropped kfree -> DIVERGE:retire (the retire log is load-bearing);
  * a workload that leaves a mutation site un-exercised -> REFUSED_COVERAGE
    even for the CORRECT body (coverage gate: un-exercised can never certify).

Needs host cc + rustc + the kernel tree at $KSRC; skipped otherwise.
"""
import copy
import importlib.util
import os
import shutil

import pytest

_D = os.path.join(os.path.dirname(__file__), "..", "container_adt")


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_D, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_R = _H = None
try:
    # unique module names — several oracle dirs ship same-named modules.
    _R = _load("cadt_reach_t", "reach.py")
    _H = _load("cadt_harness_t", "harness.py")
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


def test_gate_record_shape(prep):
    rec = prep["rec"]
    assert rec["shape"] == "iter_node"
    assert rec["globals"] == ["error_injection_list"]
    assert rec["flags"]["locks_stripped"] and rec["flags"]["alloc_stripped"]
    assert rec["token_reads"] == {"mod": ["num_ei_funcs"]}
    assert prep["sites"], "static mutation sites must be found"


def test_correct_matches(prep):
    r = _H.close(prep, _H._CANON_BODIES["correct"])
    assert r["verdict"] == "MATCH", r


def test_sabotages_diverge(prep):
    for name in ("no_filter", "no_guard"):
        r = _H.close(prep, _H._CANON_BODIES[name])
        assert r["verdict"].startswith("DIVERGE"), (name, r)


def test_retire_log_is_load_bearing(prep):
    r = _H.close(prep, _H._CANON_BODIES["no_retire"])
    assert r["verdict"] == "DIVERGE:retire", r


def test_starved_workload_refuses_coverage(prep):
    starved = copy.deepcopy(prep)
    starved["setup"]["tokset"] = [(h, f, 0) for h, f, _ in prep["setup"]["tokset"]]
    r = _H.close(starved, _H._CANON_BODIES["correct"])
    assert r["verdict"] == "REFUSED_COVERAGE", r


# ---- multi-lh-field (membership universes) --------------------------------
# ddebug_table_free: node with TWO list_head members (link, maps), anchor-less
# del through `link` (workload links nodes on a synthetic elsewhere-list).
# The load-bearing new soundness property: deleting through the WRONG member
# (del_m(M_MAPS, ...)) must DIVERGE — a universe-blind del would false-pass it.

@pytest.fixture(scope="module")
def prep_multi():
    rec = _R.gate("lib/dynamic_debug.c", "ddebug_table_free")
    return _H.prepare(rec)


def test_multi_member_record(prep_multi):
    assert prep_multi["members"] == ["link", "maps"]
    assert [k for k, _ in prep_multi["lists"]] == ["synth"]


def test_multi_member_correct_matches(prep_multi):
    r = _H.close(prep_multi, "del_m(M_LINK, a0 as u32); retire(a0 as u32);\n0\n")
    assert r["verdict"] == "MATCH", r


def test_wrong_member_diverges(prep_multi):
    r = _H.close(prep_multi, "del_m(M_MAPS, a0 as u32); retire(a0 as u32);\n0\n")
    assert r["verdict"].startswith("DIVERGE"), r


# ---- head-holder pattern (list head is a field of a container param) -------
# free_conflicting_inodes (fs/btrfs/tree-log.c): iterate &ctx->conflict_inodes
# and free each btrfs_ino_list element. The container (btrfs_log_ctx) is not
# itself a list element, and the element type differs -> prepare must model the
# container as a head-holder (its field is a named list), element type = cursor.

@pytest.fixture(scope="module")
def prep_holder():
    return _H.prepare(_R.gate("fs/btrfs/tree-log.c", "free_conflicting_inodes"))


def test_holder_list_named(prep_holder):
    assert [k for k, _ in prep_holder["lists"]] == ["holder"]


def test_holder_correct_matches(prep_holder):
    r = _H.close(prep_holder,
                 "for id in iter(L_CTX_CONFLICT_INODES) { del(id); retire(id); }\n0\n")
    assert r["verdict"] == "MATCH", r


def test_holder_dropped_retire_diverges(prep_holder):
    r = _H.close(prep_holder,
                 "for id in iter(L_CTX_CONFLICT_INODES) { del(id); }\n0\n")
    assert r["verdict"] == "DIVERGE:retire", r
