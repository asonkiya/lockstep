"""Gate the struct-driven branch MECHANISM proof (dream/structdiff/proof.py).

Proves, boot-free on the host, that a struct-field-conditioned function verified
via a generated mirror + field sweep: (A) closes when the Rust is correct AND
every branch is exercised, (B/C) DIVERGES when the translation is wrong, and
(D) REFUSES when the sweep leaves a branch uncovered (not a vacuous MATCH).
Skipped if the host has no cc/rustc.
"""
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "structdiff"))

pytestmark = pytest.mark.skipif(
    not (shutil.which("cc") and shutil.which("rustc")),
    reason="needs host cc + rustc",
)


def test_structdiff_mechanism():
    import proof
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
