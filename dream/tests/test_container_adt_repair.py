"""Banked-model repair contract (worklist 2026-08-09): the container-ADT
verification workload had measured holes that let structurally-wrong models
verify. Pinned here, red-green:

  * NULL-arg rows: a fn whose C null-guards a pointer param gets a workload
    row passing NULL (node/entry sentinel -1, token 0). A model that never
    guards the sentinel — including the dead `tokf(id) == -1` dialect the
    three pnull worklist fns shipped — must NOT verify; the faithful
    `a == -1` guard must.
  * fresh-id-0: the fresh pool includes id 0, so a model that conflates
    "id != 0" with a null check (qp_list_add_entry) diverges on a valid node.
  * del_m dialect: `del_m(M_X, id)` in a multi-membership model counts as a
    `del` for correspondence (8 T3 models were CORRECT and refused only
    because adt_ops could not see del_m).
  * INIT_LIST_HEAD correspondence: a C-side INIT op may correspond to zero
    model ops (the surface documents fresh-node/sub-anchor INIT as a no-op)
    OR to a model `del` (the older banked dialect) — both align; any other
    op-count gap still refuses.
  * member-emptiness consult dialect: `list_empty(&node->member)` guards
    (entry-target conds) accept `linked(`/`.contains(` as the model's
    emptiness consult; a not_empty guard on a del-class op's OWN member is
    canonically redundant (del of an unlinked-inited node is a no-op on both
    sides — measured in the list_empty class) and needs no consult. Head-target
    guards still require `empty(`.
  * spurious-del stays envelope-equal: `del(id); push(l, id)` for a bare C
    `list_add` cannot be killed by any workload within the caller contract
    (presenting a linked node to the C ref is corruption, not a differential)
    — the catch for that class is correspondence in the verify loop.

Needs host cc + rustc + $KSRC for the end-to-end pins; pure pins run anywhere.
"""
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


_R = _H = _CR = None
try:
    _R = _load("cadt_reach_rp", "reach.py")
    _H = _load("cadt_harness_rp", "harness.py")
    _CR = _load("cadt_realize_rp", "container_realize.py")
except Exception:
    pass

_KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
_e2e = pytest.mark.skipif(
    not (shutil.which("cc") and shutil.which("rustc") and _R and _H
         and os.path.isdir(_KSRC)),
    reason="needs host cc + rustc + $KSRC kernel tree",
)


def _prep(rel, fn):
    return _H.prepare(_R.gate(rel, fn))


def _cop(adt, c_op):
    return {"adt": adt, "c_op": c_op}


# ---------------------------------------------------------------------------
# pure correspondence pins
# ---------------------------------------------------------------------------

def test_del_m_counts_as_del():
    assert _CR is not None
    seq = _CR._adt_seq("del_m(M_NODE, id);\nretire(id);\n0")
    assert seq == ["del", "retire"]


def test_del_m_model_corresponds_with_del_kfree_c():
    cops = [_cop("del", "list_del"), _cop("retire", "kfree")]
    aops = _CR._adt_seq("del_m(M_LIST, a0 as u32); retire(a0 as u32); 0")
    _CR.correspond(cops, aops)          # must not raise


def test_init_may_correspond_to_nothing():
    cops = [_cop("del", "INIT_LIST_HEAD"), _cop("del", "INIT_LIST_HEAD"),
            _cop("push_back", "list_add_tail")]
    _CR.correspond(cops, ["push_back"])                 # rdmacg shape


def test_init_may_correspond_to_del():
    cops = [_cop("del", "INIT_LIST_HEAD"), _cop("push_back", "list_add_tail")]
    _CR.correspond(cops, ["del", "push_back"])          # older banked dialect


def test_init_optional_does_not_swallow_real_dels():
    cops = [_cop("del", "INIT_LIST_HEAD"), _cop("del", "list_del")]
    _CR.correspond(cops, ["del"])                       # aligns: INIT skipped
    with pytest.raises(_CR.Refused):
        _CR.correspond(cops, [])                        # real del unmatched


def test_spurious_del_still_refused():
    cops = [_cop("push_front", "list_add")]
    with pytest.raises(_CR.Refused, match="op_count_mismatch"):
        _CR.correspond(cops, ["del", "push_front"])


# ---------------------------------------------------------------------------
# member-emptiness consult dialect
# ---------------------------------------------------------------------------

def _gcop(adt, c_op, cond):
    o = _cop(adt, c_op)
    o["cond"] = cond
    return o


def test_entry_guarded_add_requires_linked_dialect():
    cops = [_gcop("push_back", "list_add_tail", ("empty", "entry"))]
    _CR._check_empty_consult(cops, None, "if !linked(a0 as u32) { push_back(L_X, a0 as u32); } 0")
    _CR._check_empty_consult(cops, None, "if !iter(L_X).contains(&(a0 as u32)) { push_back(L_X, a0 as u32); } 0")
    with pytest.raises(_CR.Refused, match="no_empty_in_model"):
        _CR._check_empty_consult(cops, None, "push_back(L_X, a0 as u32); 0")


def test_redundant_not_empty_del_guard_needs_no_consult():
    cops = [_gcop("del", "list_del_init", ("not_empty", "entry")),
            _cop("push_back", "list_add_tail")]
    _CR._check_empty_consult(cops, None, "del(a1 as u32); push_back(L_M_LRU, a1 as u32); 0")


def test_head_guard_still_requires_empty():
    cops = [_gcop("del", "list_del", ("not_empty", "head"))]
    with pytest.raises(_CR.Refused, match="no_empty_in_model"):
        _CR._check_empty_consult(cops, None, "del(a0 as u32); 0")
    _CR._check_empty_consult(cops, None, "if !empty(L_X) { del(a0 as u32); } 0")


# ---------------------------------------------------------------------------
# end-to-end workload pins (the measured holes, closed)
# ---------------------------------------------------------------------------

_ACPI_DEAD_GUARD = """
if tokf(a0 as u32, T_IDS) == -1 {
    return -22;
}
push_back(L_ACPI_SCAN_HANDLERS_LIST, a0 as u32);
0
"""

_ACPI_CORRECT = """
if a0 == -1 { return -22; }
push_back(L_ACPI_SCAN_HANDLERS_LIST, a0 as u32);
0
"""


@_e2e
def test_null_row_kills_dead_tokf_guard():
    prep = _prep("drivers/acpi/scan.c", "acpi_scan_add_handler")
    assert any(r[0] == -1 for r in prep["calls"]), "no NULL row generated"
    assert _H.close(prep, _ACPI_DEAD_GUARD)["verdict"] != "MATCH"
    assert _H.close(prep, _ACPI_CORRECT)["verdict"] == "MATCH"


_QP_BAD = """
if a1 as u32 as i64 != 0 {
    del(a1 as u32);
    push_front(L_QP_LIST_HEAD, a1 as u32);
}
0
"""

_QP_CORRECT = """
if a1 == -1 { return 0; }
push_front(L_QP_LIST_HEAD, a1 as u32);
0
"""


@_e2e
def test_fresh_zero_and_null_row_kill_id0_conflation():
    prep = _prep("drivers/misc/vmw_vmci/vmci_queue_pair.c", "qp_list_add_entry")
    ids = {r[1] for r in prep["calls"]}
    assert 0 in ids and -1 in ids, f"fresh-0/null rows missing: {ids}"
    assert _H.close(prep, _QP_BAD)["verdict"] != "MATCH"
    assert _H.close(prep, _QP_CORRECT)["verdict"] == "MATCH"


@_e2e
def test_unguarded_fn_gets_no_null_rows():
    prep = _prep("drivers/scsi/esp_scsi.c", "esp_put_ent")
    assert all(v >= 0 for r in prep["calls"] for v in r)


@_e2e
def test_multi_member_ops_refused_not_crashed():
    """del through TWO members of one node is beyond the single-member gate
    arena: collapsing both onto one offset double-dels (measured CRASH) —
    it must be a NAMED front refusal, never reach the differential."""
    with pytest.raises(_CR.Refused, match="multi_member_ops"):
        _CR.c_ops("drivers/rapidio/devices/rio_mport_cdev.c",
                  "rio_mport_delete_db_filter")


@_e2e
def test_linked_dialect_verifies_net_unlink_todo():
    prep = _prep("net/core/dev.c", "net_unlink_todo")
    body = """
if !linked_m(M_UNLINK_LIST, a0 as u32) {
    push_back(L_NET_UNLINK_LIST, a0 as u32);
}
0
"""
    assert _H.close(prep, body)["verdict"] == "MATCH"
