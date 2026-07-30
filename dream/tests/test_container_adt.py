"""Gate the container-family ADT oracle (dream/container_adt/proof.py).

Representation-independent ADT differential over the LIST idiom: C reference runs
real list.h ops on real list_head nodes; Rust candidate runs the same logic against
a Vec-modeled List ADT; the oracle extracts each side's per-list id-sequence and
compares. Must catch wrong ADT contents even when the op SHAPE matches (the
container analog of the effect-trace over-credit case). Boot-free; skipped without
host cc/rustc.
"""
import importlib.util
import os
import shutil
import tempfile

import pytest

_DIR = os.path.join(os.path.dirname(__file__), "..", "container_adt")


def _load(fname, modname):
    # load by explicit path under a UNIQUE name — several oracle dirs each ship a
    # module literally named `proof`, which would otherwise collide in sys.modules.
    spec = importlib.util.spec_from_file_location(modname, os.path.join(_DIR, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_P = _RB = None
try:
    _P = _load("proof.py", "container_adt_proof")
    _RB = _load("proof_rbtree.py", "container_adt_rbtree")
except Exception:
    pass

pytestmark = pytest.mark.skipif(
    not (shutil.which("cc") and shutil.which("rustc") and _P),
    reason="needs host cc + rustc",
)


def test_container_adt_mechanism():
    with tempfile.TemporaryDirectory() as tmp:
        r = _P.run_all(tmp)
    for name, res in r.items():
        assert res["ok"], f"{name}: got {res['verdict']}, expected {res['expect']}\n{res['out']}"
    # the load-bearing property: shallow_ok has the RIGHT op shape (op-count check
    # passes) but WRONG ADT contents -> the ADT oracle is strictly stronger.
    sh = r["shallow_ok"]
    assert sh["verdict"] == "DIVERGE"
    assert sh["shape_ok"], "shallow_ok must match op-counts (else it's not the strictly-stronger case)"


def test_container_adt_rbtree_idiom():
    with tempfile.TemporaryDirectory() as tmp:
        r = _RB.run_all(tmp)
    for name, res in r.items():
        assert res["ok"], f"{name}: got {res['verdict']}, expected {res['expect']}\n{res['out']}"
    sh = r["shallow_ok"]
    assert sh["verdict"] == "DIVERGE" and sh["shape_ok"]
    # a keyset-only check would pass wrong_id (right keys, wrong values); the
    # ordered-MAP ADT comparison catches it.
    assert r["wrong_id"]["verdict"] == "DIVERGE"
