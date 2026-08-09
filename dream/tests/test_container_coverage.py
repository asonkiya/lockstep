"""Branch-coverage as a gate PRECONDITION (generalization slice A).

Every workload-hole defect the 2026-08-09 repair fixed shared one root cause:
the gate declared MATCH for functions whose predicates were never exercised on
both sides (pnull models verified without a null row; `id != 0` passing
because the pool never held 0; flip_guard no-oping on one emission shape).
This slice makes that class structurally impossible: the C reference TU
carries per-op execution counters and per-guard taken/not-taken counters, and
run_gate REFUSES a MATCH whose coverage is incomplete —
`coverage:unexercised_branch:*` / `coverage:dead_op:*`. A MATCH now certifies
"behaviorally equal AND every branch and op exercised."

Pins:
  * unit: _cov_enforce refuses dead ops, single-polarity guards, missing
    reports — and passes complete coverage;
  * live: MATCH verdicts carry a full CGCOV report for every gate mode
    (unconditional, list_empty guard, tok, pnull);
  * the NEGATIVE CONTROL for the whole slice: the HISTORICAL workload holes,
    reconstructed via probe_flags (PNULL_MODE=0 = the pre-repair probe with
    no null row; COND_MODE=0 = no drained phase), are refused BY THE COVERAGE
    CHECK ALONE — the defect class dies even when the workload is bad.

Needs cc + rustc + $KSRC; skipped otherwise.
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
        "container_realize_cov",
        os.path.join(_HERE, "..", "container_adt", "container_realize.py"))
    _CR = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_CR)
except Exception:
    _CR = None

pytestmark = pytest.mark.skipif(
    not (_CR and shutil.which("cc") and shutil.which("rustc")
         and os.path.isdir(_KSRC)),
    reason="needs cc + rustc + $KSRC")

# one fn per gate mode, all previously boot- or gate-verified
_PLAIN = ("drivers/crypto/intel/qat/qat_common/adf_init.c", "adf_service_add")
_GUARD = ("drivers/net/ethernet/broadcom/bnxt/bnxt.c", "bnxt_del_one_usr_fltr")
_TOK = ("drivers/mfd/abx500-core.c", "abx500_remove_ops")            # tok sweep
_PN = ("drivers/misc/vmw_vmci/vmci_queue_pair.c", "qp_list_remove_entry")


@pytest.fixture(scope="module")
def layout():
    return _CR.LM.probe_layout()


# ---------------- unit: the enforcement itself ----------------

def _mkops(n, conds):
    return [{"c_op": f"op{i}", "cond": conds.get(i)} for i in range(n)]


def _out(rows, lg="t=1 f=1", tok="t=1 f=1"):
    lines = [f"CGCOV i={i} exec={e} t={t} f={f}" for i, (e, t, f) in rows.items()]
    lines += [f"CGCOV lg {lg}", f"CGCOV tok {tok}"]
    return "\n".join(lines)


def test_enforce_passes_complete_coverage():
    ops = _mkops(2, {1: ("not_empty", "entry")})
    _CR._cov_enforce(_out({0: (6, 0, 0), 1: (6, 4, 2)}), ops, None)


def test_enforce_refuses_dead_op():
    ops = _mkops(2, {})
    with pytest.raises(_CR.Refused, match="coverage:dead_op"):
        _CR._cov_enforce(_out({0: (6, 0, 0), 1: (0, 0, 0)}), ops, None)


def test_enforce_refuses_single_polarity_guard():
    ops = _mkops(1, {0: ("nonnull", "entry")})
    with pytest.raises(_CR.Refused, match="coverage:unexercised_branch:nonnull"):
        _CR._cov_enforce(_out({0: (6, 6, 0)}), ops, None)
    with pytest.raises(_CR.Refused, match="coverage:unexercised_branch:nonnull"):
        _CR._cov_enforce(_out({0: (0, 0, 6)}), ops, None)


def test_enforce_refuses_missing_report():
    ops = _mkops(1, {})
    with pytest.raises(_CR.Refused, match="coverage:report_missing"):
        _CR._cov_enforce("CREALIZE verdict=MATCH calls=6", ops, None)


def test_enforce_covers_loop_guard_and_tok_rows():
    ops = _mkops(1, {})
    it_g = {"safe": True, "guard": "not_empty"}
    with pytest.raises(_CR.Refused, match="coverage:unexercised_branch:loop_guard"):
        _CR._cov_enforce(_out({0: (6, 0, 0)}, lg="t=0 f=6"), ops, it_g)
    it_t = {"safe": True, "tok": {"field": "x", "op": "=="}}
    with pytest.raises(_CR.Refused, match="coverage:unexercised_branch:tok"):
        _CR._cov_enforce(_out({0: (6, 0, 0)}, tok="t=6 f=0"), ops, it_t)


# ---------------- live: every MATCH carries full coverage ----------------

def test_match_requires_and_carries_coverage(layout):
    for rel, fn in (_PLAIN, _GUARD, _TOK, _PN):
        v, out, _ = _CR.run_gate(rel, fn, layout)
        assert v == "MATCH", (fn, v, out[-200:] if out else "")
        assert "CGCOV" in out, fn


# ---------------- the slice's negative control: history refused ----------

def test_old_pnull_hole_refused_by_coverage_alone(layout):
    # the EXACT pre-repair workload (no null row): both sides agree on every
    # non-null call, so the old gate said MATCH — the coverage check must be
    # the thing that refuses it
    with pytest.raises(_CR.Refused, match="coverage:unexercised_branch:nonnull"):
        _CR.run_gate(*_PN, layout, probe_flags={"PNULL_MODE": 0})


def test_old_single_phase_hole_refused_by_coverage_alone(layout):
    # a probe without the drained phase (pre-COND_MODE shape): the list_empty
    # guard only ever sees a populated arena — single polarity, refused
    with pytest.raises(_CR.Refused, match="coverage:unexercised_branch"):
        _CR.run_gate(*_GUARD, layout, probe_flags={"COND_MODE": 0})
