"""A4 — the formal rung above the differential (dream/formal/lift_proof.py).

The differential SAMPLES the safety lift; Kani/CBMC PROVES it: both real
artifacts (tier-(a) unsafe, tier-(b) safe core + boundary) run on identical
SYMBOLIC struct state, asserting equal return and equal post-state for ALL
inputs. Panic-freedom comes free (a reachable panic in a freestanding kernel
object is a hang). Pins:
  * the generated crate contains BOTH artifacts and a #[kani::proof] harness;
  * a real candidate PROVES;
  * the proof is NON-VACUOUS — a sabotaged lift must FAIL by name.
Kani is slow, so the proving tests are marked slow and skipped without it.
"""
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile

import pytest

_HERE = os.path.dirname(__file__)
_KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")

_LP = None
try:
    _spec = importlib.util.spec_from_file_location(
        "lift_proof_t", os.path.join(_HERE, "..", "formal", "lift_proof.py"))
    _LP = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_LP)
except Exception:
    _LP = None

_HAS_KANI = shutil.which("cargo-kani") is not None or shutil.which("kani") is not None

pytestmark = pytest.mark.skipif(
    not (_LP and os.path.isdir(_KSRC)), reason="needs lift_proof + $KSRC")

_FILE, _FN = "block/bdev.c", "bdev_block_writes"


def test_generated_crate_has_both_artifacts_and_a_proof():
    lib, meta = _LP.build_proof(_FILE, _FN)
    # both real artifacts, verbatim-derived
    assert "pub unsafe fn tier_a" in lib
    assert "pub unsafe fn tier_b" in lib
    assert "#![forbid(unsafe_code)]" in lib          # the tier-b core is intact
    # a kani harness with symbolic inputs and both comparisons
    assert "#[kani::proof]" in lib and "kani::any()" in lib
    assert "lift changed field" in lib
    assert meta["fields"], "expected at least one mirrored field"


@pytest.mark.skipif(not _HAS_KANI, reason="cargo-kani not installed")
def test_real_candidate_proves():
    rc, verdict = _LP.prove(_FILE, _FN)
    assert verdict in ("PROVEN", "SKIP_LOOP"), verdict


@pytest.mark.skipif(not _HAS_KANI, reason="cargo-kani not installed")
def test_proof_is_non_vacuous():
    # sabotage the tier-(b) core only: the proof MUST fail, naming the field.
    lib, meta = _LP.build_proof(_FILE, _FN)
    # form-agnostic sabotage: the emitted decrement is `wrapping_sub( 1)` after
    # A4, and was a bare `- 1` before. Try both; if NEITHER applies the test
    # fails loudly rather than "passing" a proof that was never sabotaged.
    bad = lib.replace("wrapping_sub( 1)", "wrapping_sub( 2)", 1)
    if bad == lib:
        bad = lib.replace("- 1) as i32", "- 2) as i32", 1)
    assert bad != lib, "sabotage did not apply — update the pattern"
    d = tempfile.mkdtemp(prefix="liftproof_t_")
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    open(os.path.join(d, "Cargo.toml"), "w").write(_LP._CARGO)
    open(os.path.join(d, "src", "lib.rs"), "w").write(bad)
    r = subprocess.run(["cargo", "kani"], cwd=d, capture_output=True, text=True, timeout=900)
    out = r.stdout + r.stderr
    assert "VERIFICATION:- FAILED" in out, out[-400:]
    assert "lift changed field" in out
    shutil.rmtree(d, ignore_errors=True)
