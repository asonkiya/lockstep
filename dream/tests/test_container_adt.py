"""Gate the container-family ADT oracle (dream/container_adt/proof.py).

Representation-independent ADT differential over the LIST idiom: C reference runs
real list.h ops on real list_head nodes; Rust candidate runs the same logic against
a Vec-modeled List ADT; the oracle extracts each side's per-list id-sequence and
compares. Must catch wrong ADT contents even when the op SHAPE matches (the
container analog of the effect-trace over-credit case). Boot-free; skipped without
host cc/rustc.
"""
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "container_adt"))

_P = None
try:
    import proof as _P  # noqa: E402
except Exception:
    _P = None

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
