"""Containers conditional class, step 1: list_empty guards ONLY.

The predicate census (10ea4f7) split the 56-fn conditional refusal class;
this build covers the 12 whose EVERY predicate is a bare `!?list_empty(expr)`
— the one class expressible entirely in list vocabulary, so BOTH gate sides
can execute the real predicate (the C ref must not be re-emitted
unconditionally: that was the T3-audit false-pass vector). Pins:

  * c_ops parses the guard into structure (polarity + target), never drops it;
  * the pop idiom (`if (empty) return; e = first; del(e)`) resolves the del
    target to FIRST-of-head through the list_first_entry local;
  * guarded candidates realize and MATCH under a two-phase probe (populated
    arena + drained arena) so BOTH branches are exercised;
  * the gate is load-bearing on the new axis: guard-DROPPED emission and
    guard-FLIPPED polarity must both be rejected;
  * non-list_empty predicates (tokf equality, or_null truthiness) stay
    refused, as does the second-list-per-node shape the arena cannot model.

Needs cc + rustc + docker + $KSRC; skipped otherwise.
"""
import importlib.util
import os
import shutil

import pytest

_HERE = os.path.dirname(__file__)
_KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")

_CR = None
try:
    _spec = importlib.util.spec_from_file_location(
        "container_realize_cond",
        os.path.join(_HERE, "..", "container_adt", "container_realize.py"))
    _CR = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_CR)
except Exception:
    _CR = None

pytestmark = pytest.mark.skipif(
    not (_CR and shutil.which("cc") and shutil.which("rustc")
         and shutil.which("docker") and os.path.isdir(_KSRC)),
    reason="needs cc + rustc + docker + $KSRC")

# guarded-entry: if (!list_empty(&fltr->list)) list_del_init(&fltr->list);
_GUARD = ("drivers/net/ethernet/broadcom/bnxt/bnxt.c", "bnxt_del_one_usr_fltr")
# guarded-pop: if (list_empty(head)) return NULL; pw = first; list_del(...)
_POP = ("kernel/padata.c", "padata_work_alloc")
# add-if-unlinked: if (list_empty(&dev->unlink_list)) list_add_tail(...)
_ADDIF = ("net/core/dev.c", "net_unlink_todo")
# guarded-flush (T3): if (list_empty(head)) return; for_each_safe {del;kfree}
_FLUSH = ("drivers/net/ethernet/chelsio/cxgb4/cxgb4_mps.c",
          "cxgb4_free_mps_ref_entries")
# equality predicate — stays refused (tokf class, out of scope)
_EQ = ("drivers/mfd/abx500-core.c", "abx500_remove_ops")
# second list_head per node — the arena cannot model it; stays refused
_SECOND = ("fs/fuse/dax.c", "fuse_free_dax_mem_ranges")


@pytest.fixture(scope="module")
def layout():
    return _CR.LM.probe_layout()


def test_guard_parsed_into_structure():
    cops, _, it = _CR.c_ops(*_GUARD)
    assert it is None
    assert [o["c_op"] for o in cops] == ["list_del_init"]
    assert cops[0]["cond"] == ("not_empty", "entry")


def test_pop_shape_resolves_first_through_the_local():
    cops, _, it = _CR.c_ops(*_POP)
    assert it is None
    assert [o["c_op"] for o in cops] == ["list_del"]
    # `if (list_empty(h)) return NULL;` inverts to a not_empty guard on the rest
    assert cops[0]["cond"] == ("not_empty", "head")
    assert cops[0]["tgt"] == "first"


def test_add_if_unlinked_polarity():
    cops, _, _ = _CR.c_ops(*_ADDIF)
    assert [o["c_op"] for o in cops] == ["list_add_tail"]
    assert cops[0]["cond"] == ("empty", "entry")


def test_guarded_flush_iteration():
    cops, _, it = _CR.c_ops(*_FLUSH)
    assert it is not None and it["safe"]
    assert it.get("guard") == "not_empty"


def test_guarded_candidates_pass_the_gate(layout):
    for rel, fn in (_GUARD, _POP, _FLUSH):
        v, out, _ = _CR.run_gate(rel, fn, layout)
        assert v == "MATCH", (fn, v, out[-200:] if out else "")


def test_wrong_object_model_is_refused_not_gated(layout):
    # net_unlink_todo FINDING: the C guards on the NODE's own list_head
    # ("is dev unlinked" — a self-loop test), but the banked model guards on
    # the GLOBAL list's emptiness with an else branch — the ADT vocabulary
    # cannot even express per-node emptiness, so the model verified while
    # describing a different function. Correspondence must refuse it BEFORE
    # the gate; realizing it naively would change kernel behavior.
    with pytest.raises(_CR.Refused, match="op_count_mismatch|no_empty"):
        _CR.emit_realized(*_ADDIF, layout)


def test_dropped_guard_is_rejected_where_it_matters(layout):
    # the exact false-pass class the T3 audit closed: ops emitted
    # unconditionally while the C keeps the guard. Pinned on the POP shape,
    # where the guard is load-bearing (del of head->next on an EMPTY list is
    # not a no-op) — phase 2 of the probe catches it.
    v, _, _ = _CR.run_gate(*_POP, layout, sabotage="drop_guard")
    assert v in ("DIVERGE", "CRASH", "HANG"), v


def test_dropped_redundant_guard_is_equivalent_and_accepted(layout):
    # MEASURED, not asserted: `if (!list_empty(&x->l)) list_del_init(&x->l)`
    # minus the guard is behaviorally IDENTICAL — list_del_init on a
    # self-looped node is a no-op, the kernel's guard is an optimization.
    # A behavioral differential accepts semantically-equivalent translations;
    # rejecting this would be checking syntax, not behavior.
    v, _, _ = _CR.run_gate(*_GUARD, layout, sabotage="drop_guard")
    assert v == "MATCH", v


def test_flipped_guard_is_rejected(layout):
    v, _, _ = _CR.run_gate(*_GUARD, layout, sabotage="flip_guard")
    assert v in ("DIVERGE", "CRASH", "HANG")


def test_out_of_class_predicates_stay_refused():
    with pytest.raises(_CR.Refused, match="conditional|guard"):
        _CR.c_ops(*_EQ)
    with pytest.raises(_CR.Refused, match="conditional|guard|second"):
        _CR.c_ops(*_SECOND)
