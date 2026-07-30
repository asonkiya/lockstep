"""Gate the effect-trace oracle mechanism proof (dream/efftrace/proof.py).

The linchpin oracle for the entangled core's bounded_state class (~35% of core
functions): record a function's ordered STATE effects under a workload, replay a
Rust candidate against the frozen trace. Must catch wrong state even when the
RETURN matches (the over-credit case a return-only differential misses).
Boot-free; skipped without host cc/rustc.
"""
import os
import importlib.util
import shutil
import tempfile

import pytest

_P = None
try:
    # unique module name — several oracle dirs ship a module named `proof`.
    _spec = importlib.util.spec_from_file_location(
        "efftrace_proof", os.path.join(os.path.dirname(__file__), "..", "efftrace", "proof.py"))
    _P = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_P)
except Exception:
    _P = None

pytestmark = pytest.mark.skipif(
    not (shutil.which("cc") and shutil.which("rustc") and _P),
    reason="needs host cc + rustc",
)


def test_efftrace_mechanism():
    with tempfile.TemporaryDirectory() as tmp:
        r = _P.run_all(tmp)
    for name, res in r.items():
        assert res["ok"], f"{name}: got {res['verdict']}, expected {res['expect']}\n{res['out']}"
    # the load-bearing property: the over-credit candidate (right RETURN, wrong
    # STATE) is DIVERGE for the effect-trace but MATCH for a return-only oracle.
    over = r["wrong_count_write"]
    assert over["verdict"] == "DIVERGE"
    assert over["ret_only"] == "MATCH", "effect-trace must be STRICTLY stronger than return-only"
