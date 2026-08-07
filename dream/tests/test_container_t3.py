"""Containers T3 — realize the retire/kfree class with the COMPOSED gate
(chain-walking differential + order-sensitive free-event log).

The census (t3_census.py) measured the population: 103/131 in v1 scope — 71
unconditional safe-iteration flushes + 32 straight-line del+kfree — every one
using a single bare `kfree(node)`. Pins:

  * kfree is a CONCRETE op read from the real C (class `retire`), kept in
    order with the list ops;
  * the freed node must be the node being unlinked (free-target guard) and
    multi-head iterations are refused — fail-closed, never guessed;
  * a realized T3 candidate passes the composed gate, and the free log is
    actually exercised (frees > 0 — no vacuous pass);
  * the gate is load-bearing on the NEW axis: dropping the free -> DIVERGE
    (the over-credit case the feasibility doc predicted for a list-only
    oracle), freeing the wrong node -> DIVERGE;
  * free-BEFORE-unlink (a real UAF: list_del then reads freed memory) is
    caught by the chain-digest-at-free-time, and is INVISIBLE to the ADT
    retire-log view (slots only) — the composed gate is measured strictly
    stronger, same discipline as no_poison/del_not_init.

Needs cc + rustc + docker (in-kernel layout probe) + $KSRC; skipped otherwise.
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
        "container_realize_t3",
        os.path.join(_HERE, "..", "container_adt", "container_realize.py"))
    _CR = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_CR)
except Exception:
    _CR = None

pytestmark = pytest.mark.skipif(
    not (_CR and shutil.which("cc") and shutil.which("rustc")
         and shutil.which("docker") and os.path.isdir(_KSRC)),
    reason="needs cc + rustc + docker + $KSRC")

# straight-line: list_del(&cl->node); kfree(cl);
_DROP = ("drivers/clk/clkdev.c", "clkdev_drop")
# canonical flush: list_for_each_entry_safe { list_del; kfree; }
_FLUSH = ("block/sed-opal.c", "clean_opal_dev")
# two for_each heads + two kfrees — must NOT collapse into one walk
_MULTI = ("drivers/vhost/vhost.c", "vhost_clear_msg")


@pytest.fixture(scope="module")
def layout():
    return _CR.LM.probe_layout()


def test_kfree_is_a_concrete_op_in_order():
    cops, _, it = _CR.c_ops(*_DROP)
    assert it is None
    assert [o["c_op"] for o in cops] == ["list_del", "kfree"]
    assert [o["adt"] for o in cops] == ["del", "retire"]
    assert [o["rs"] for o in cops] == ["list_del", "free_ev"]


def test_retire_corresponds_to_kfree():
    cops, _, _ = _CR.c_ops(*_DROP)
    aops, _ = _CR.adt_ops(*_DROP)
    assert "retire" in aops
    assert _CR.correspond(cops, aops)
    with pytest.raises(_CR.Refused):        # model missing the retire -> refuse
        _CR.correspond(cops, [o for o in aops if o != "retire"])


def test_multi_head_iteration_refused():
    with pytest.raises(_CR.Refused, match="multi_head|cross_list"):
        _CR.c_ops(*_MULTI)


def test_conditional_straightline_refused():
    # nfp_port_free: `if (...) ...; list_del; kfree` — a straight-line body
    # whose ops sit under (or after) a conditional. Extracting the ops
    # unconditionally would DROP the guard, and the gate could not see it
    # because the C reference is re-emitted from the same extracted ops. The
    # audit found 24 such fns already accepted in T2 + 3 in T3 — this pins the
    # fail-closed refusal that corrects both.
    with pytest.raises(_CR.Refused, match="conditional_body"):
        _CR.c_ops("drivers/net/ethernet/netronome/nfp/nfp_port.c",
                  "nfp_port_free")
    with pytest.raises(_CR.Refused, match="conditional_body"):
        _CR.c_ops("drivers/infiniband/core/iwcm.c", "get_work")


def test_straightline_t3_passes_composed_gate(layout):
    v, out, _ = _CR.run_gate(*_DROP, layout)
    assert v == "MATCH"
    assert "frees=" in out                   # the free log was exercised
    assert int(out.split("frees=")[1].split()[0]) > 0


def test_flush_t3_passes_composed_gate(layout):
    v, out, _ = _CR.run_gate(*_FLUSH, layout)
    assert v == "MATCH"
    assert int(out.split("frees=")[1].split()[0]) > 0


def test_dropped_free_diverges(layout):
    # the over-credit case: right membership, dropped free
    v, _, _ = _CR.run_gate(*_DROP, layout, sabotage="no_free")
    assert v == "DIVERGE"
    v, _, _ = _CR.run_gate(*_FLUSH, layout, sabotage="no_free")
    assert v == "DIVERGE"


def test_wrong_free_target_diverges(layout):
    v, _, _ = _CR.run_gate(*_DROP, layout, sabotage="wrong_free")
    assert v == "DIVERGE"


def test_uaf_free_order_caught_structurally_only(layout):
    # kfree BEFORE list_del: list_del then operates on freed memory (UAF in
    # situ). Slots and chain states at call boundaries are identical — only
    # the chain-digest-at-free-time distinguishes the orders.
    full, _, _ = _CR.run_gate(*_DROP, layout, sabotage="free_before_del")
    adt, _, _ = _CR.run_gate(*_DROP, layout, sabotage="free_before_del",
                             adt_only=True)
    assert full == "DIVERGE"
    assert adt == "MATCH"                    # ADT retire-log view is blind
