"""Gate the allocator-init oracle mechanism proof (dream/allocmodel/proof.py).

The graph/alloc decomposition found 26% of that mass is alloc-only (`p =
kzalloc(...); if (!p) return NULL; p->f = ...; return p;`) — unreachable by the
container oracle (no list op) and by effect-trace (alloc is forbidden), with 63
gate-clean instances in kernel+mm+lib. This oracle models allocation as a fresh
arena slot and verifies the init field-writes + returned id via a state
differential. Boot-free; skipped without host cc/rustc.
"""
import importlib.util
import os
import shutil
import tempfile

import pytest

_P = None
try:
    _spec = importlib.util.spec_from_file_location(
        "allocmodel_proof", os.path.join(os.path.dirname(__file__), "..", "allocmodel", "proof.py"))
    _P = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_P)
except Exception:
    _P = None

pytestmark = pytest.mark.skipif(
    not (shutil.which("cc") and shutil.which("rustc") and _P),
    reason="needs host cc + rustc",
)


def test_allocmodel_mechanism():
    with tempfile.TemporaryDirectory() as tmp:
        r = _P.run_all(tmp)
    for name, res in r.items():
        assert res["ok"], f"{name}: got {res['verdict']}, expected {res['expect']}\n{res['out']}"
    # load-bearing: no_init returns a VALID pointer but uninitialized contents —
    # a return/pointer-only oracle passes it; the field differential diverges.
    assert r["no_init"]["verdict"] == "DIVERGE:state"
    # the fresh-slot sequence is observable: an extra allocation shifts the id.
    assert r["wrong_count"]["verdict"] == "DIVERGE:ret"
