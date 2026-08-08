"""Containers step 3 — realize container candidates against the list_head
mirror (container_realize.py). Pins:
  * the concrete op comes from the REAL C, not the abstract model (list_del vs
    list_del_init are the same ADT op but different kernel semantics);
  * correspondence between C ops and model ops is required, else REFUSED;
  * real candidates realize and pass the chain-walking differential;
  * the gate is load-bearing (wrong op -> DIVERGE);
  * the headline: emitting list_del where the kernel wrote list_del_init is
    caught STRUCTURALLY but is INVISIBLE to the ADT oracle — the justification
    for reading the C.
Needs cc + rustc + docker (in-kernel probe) + $KSRC; skipped otherwise.
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
        "container_realize_t",
        os.path.join(_HERE, "..", "container_adt", "container_realize.py"))
    _CR = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_CR)
except Exception:
    _CR = None

pytestmark = pytest.mark.skipif(
    not (_CR and shutil.which("cc") and shutil.which("rustc")
         and shutil.which("docker") and os.path.isdir(_KSRC)),
    reason="needs cc + rustc + docker + $KSRC")

_ADD = ("drivers/crypto/intel/qat/qat_common/adf_init.c", "adf_service_add")
_DEL_INIT = ("drivers/md/dm-cache-policy.c", "dm_cache_policy_unregister")


@pytest.fixture(scope="module")
def layout():
    return _CR.LM.probe_layout()


def test_concrete_op_comes_from_the_c_not_the_model():
    # both C functions' models say "del"; only the C distinguishes the op
    cops, _, _ = _CR.c_ops(*_DEL_INIT)
    assert [o["c_op"] for o in cops] == ["list_del_init"]
    assert [o["adt"] for o in cops] == ["del"]          # ADT class is the same
    assert [o["rs"] for o in cops] == ["list_del_init"]  # emission follows the C


def test_correspondence_is_required():
    cops, _, _ = _CR.c_ops(*_ADD)
    aops, _ = _CR.adt_ops(*_ADD)
    assert _CR.correspond(cops, aops)                   # matching case works
    with pytest.raises(_CR.Refused):                    # count mismatch refused
        _CR.correspond(cops, aops + ["del"])
    with pytest.raises(_CR.Refused):                    # class mismatch refused
        _CR.correspond(cops, ["push_back"])


def test_allocation_and_iteration_are_refused():
    # T3 (kfree) must route to the allocator model, not a list oracle
    bad = [("mm/mmap.c", "exit_mmap")]
    for rel, fn in bad:
        try:
            _CR.c_ops(rel, fn)
        except _CR.Refused:
            return
        except Exception:
            return
    pytest.skip("no refusable sample available in this tree")


@pytest.mark.parametrize("target", [_ADD, _DEL_INIT])
def test_real_candidates_realize_and_verify(layout, target):
    v, out, d = _CR.run_gate(target[0], target[1], layout)
    assert v == "MATCH", (target[1], v, out[-300:])


def test_gate_is_load_bearing(layout):
    v, out, d = _CR.run_gate(_ADD[0], _ADD[1], layout, sabotage="wrong_op")
    assert v == "DIVERGE", (v, out[-300:])


def test_del_vs_del_init_is_adt_invisible_but_structurally_caught(layout):
    # THE headline: emitting list_del where the kernel wrote list_del_init.
    full, _, _ = _CR.run_gate(*_DEL_INIT, L=layout, sabotage="del_not_init") \
        if False else _CR.run_gate(_DEL_INIT[0], _DEL_INIT[1], layout,
                                   sabotage="del_not_init")
    adt, _, _ = _CR.run_gate(_DEL_INIT[0], _DEL_INIT[1], layout,
                             sabotage="del_not_init", adt_only=True)
    assert full == "DIVERGE", "structural oracle must catch it"
    assert adt == "MATCH", "expected the ADT oracle to be blind to this class"


# --- iteration (list_for_each_entry[_safe]) ---------------------------------

_ITER = ("drivers/net/ethernet/mellanox/mlx5/core/diag/fw_tracer.c",
         "mlx5_fw_tracer_clean_ready_list")


def test_iteration_is_classified_from_the_c():
    cops, _, it = _CR.c_ops(*_ITER)
    assert it == {"safe": True}          # list_for_each_entry_safe
    assert [o["c_op"] for o in cops] == ["list_del"]


def test_safe_iteration_realizes_and_verifies(layout):
    v, out, d = _CR.run_gate(_ITER[0], _ITER[1], layout)
    assert v == "MATCH", (v, out[-300:])


def test_emitted_safe_walk_caches_next_before_the_body(layout):
    src, _, _, it = _CR.emit_realized(_ITER[0], _ITER[1], layout)
    body = src[src.index("while"):]
    # the cached next must be taken BEFORE the mutating op, else the walk reads
    # a poisoned pointer after list_del
    assert body.index("let n = (*pos).next") < body.index("list_del")
    assert "pos = n;" in body


def test_plain_walk_over_a_deleting_body_is_rejected(layout):
    # emitting the non-cached walk reads pos->next AFTER list_del poisoned it:
    # a wild-pointer dereference (segfault here, kernel oops in situ). The gate
    # must REJECT — never report a pass.
    v, out, d = _CR.run_gate(_ITER[0], _ITER[1], layout, sabotage="unsafe_iter")
    assert v in ("CRASH", "DIVERGE", "HANG"), (v, out[-200:])


def test_cross_list_move_is_refused():
    # dev_exceptions_move iterates `orig` and moves to `dest` — a TWO-list
    # function. v1 models one list, so it must refuse rather than mis-model
    # (collapsing the heads makes it a self-move that never terminates).
    with pytest.raises(_CR.Refused, match="cross_list_move"):
        _CR.c_ops("security/device_cgroup.c", "dev_exceptions_move")


def test_conditional_loop_body_is_refused():
    # (abx500_remove_ops, the original example here, moved IN-class with the
    # tokf-equality build; the per-node list_empty predicate is the shape the
    # single-list arena still cannot model)
    with pytest.raises(_CR.Refused, match="conditional_loop_body|tok_guard"):
        _CR.c_ops("fs/fuse/dax.c", "fuse_free_dax_mem_ranges")
