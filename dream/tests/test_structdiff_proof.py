"""Gate the struct-driven branch MECHANISM proof (dream/structdiff/proof.py).

Proves, boot-free on the host, that a struct-field-conditioned function verified
via a generated mirror + field sweep: (A) closes when the Rust is correct AND
every branch is exercised, (B/C) DIVERGES when the translation is wrong, and
(D) REFUSES when the sweep leaves a branch uncovered (not a vacuous MATCH).
Skipped if the host has no cc/rustc.
"""
import os
import importlib.util
import shutil
import tempfile

import pytest

pytestmark = pytest.mark.skipif(
    not (shutil.which("cc") and shutil.which("rustc")),
    reason="needs host cc + rustc",
)


def _load_proof():
    # unique module name — several oracle dirs ship a module named `proof`.
    spec = importlib.util.spec_from_file_location(
        "structdiff_proof", os.path.join(os.path.dirname(__file__), "..", "structdiff", "proof.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_structdiff_mechanism():
    proof = _load_proof()
    with tempfile.TemporaryDirectory() as tmp:
        results, real_size = proof.run_all(tmp)
    assert real_size == 16
    # every scenario must land on its expected verdict
    for name, r in results.items():
        assert r["ok"], f"{name}: got {r['verdict']}, expected {r['expect']}\n{r['out']}"
    # spell out the load-bearing ones explicitly
    assert results["A_correct"]["verdict"] == "MATCH"
    assert results["B_swap_arm"]["verdict"] == "DIVERGE"
    assert results["C_invert"]["verdict"] == "DIVERGE"
    assert results["D_undercover"]["verdict"] == "REFUSE"  # path-coverage gate, not MATCH
