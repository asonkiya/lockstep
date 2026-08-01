"""Gate the PRODUCTIZED effect-trace oracle (dream/efftrace/harness.py).

proof.py proved the ordered record/replay mechanism on a synthetic subject; the
harness runs a per-call FULL-FOOTPRINT state differential against a REAL kernel
function verbatim (rb_set_black, lib/rbtree.c). Pinned contract:

  * reach.gate accepts it with resolved footprint (param-struct scalar field +
    the RB_BLACK define);
  * correct body (+= RB_BLACK) -> MATCH;
  * over-credit sabotage (right void return, untouched state) -> DIVERGE:state
    — the exact case a return-only oracle false-passes;
  * the plausible |=-for-+= mistranslation -> DIVERGE:state (agrees on first
    touch, caught on the second application to the same node — this exact bug
    was caught LIVE while writing the hand candidate);
  * an empty workload -> REFUSED_COVERAGE even for the correct body.

Needs host cc + rustc + the kernel tree at $KSRC; skipped otherwise.
"""
import copy
import importlib.util
import os
import shutil

import pytest

_D = os.path.join(os.path.dirname(__file__), "..", "efftrace")


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_D, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_R = _H = None
try:
    # unique module names — several oracle dirs ship same-named modules.
    _R = _load("eff_reach_t", "reach.py")
    _H = _load("eff_harness_t", "harness.py")
except Exception:
    pass

pytestmark = pytest.mark.skipif(
    not (shutil.which("cc") and shutil.which("rustc") and _R and _H
         and os.path.isdir(os.environ.get(
             "KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux"))),
    reason="needs host cc + rustc + $KSRC kernel tree",
)


@pytest.fixture(scope="module")
def prep():
    rec = _R.gate(*_H._CANON)
    return _H.prepare(rec)


def test_gate_record(prep):
    rec = prep["rec"]
    assert rec["defines"] == {"RB_BLACK": 1}
    assert rec["write_fields"] == ["rb->__rb_parent_color"]
    assert prep["widx"], "write-target cells must be identified"


def test_correct_matches(prep):
    r = _H.close(prep, _H._CANON_BODIES["correct"])
    assert r["verdict"] == "MATCH", r


def test_over_credit_diverges(prep):
    r = _H.close(prep, _H._CANON_BODIES["over_credit"])
    assert r["verdict"] == "DIVERGE:state", r


def test_or_for_add_mistranslation_diverges(prep):
    r = _H.close(prep, _H._CANON_BODIES["or_not_add"])
    assert r["verdict"] == "DIVERGE:state", r


def test_empty_workload_refuses_coverage(prep):
    starved = copy.deepcopy(prep)
    starved["rounds"] = [{"seeds": [], "calls": []}]
    r = _H.close(starved, _H._CANON_BODIES["correct"])
    assert r["verdict"] == "REFUSED_COVERAGE", r


# ---- directed workload synthesis ------------------------------------------
# ctx_set_mount_opt (fs/ext2/super.c): `|= flag` on two fields. The undirected
# workload can't guarantee a flag bit the seed lacks on every arena slot, so a
# CORRECT translation spuriously REFUSED_COVERAGE. The coverage search over the
# real C drives it -> MATCH. Load-bearing soundness property: directed only
# ADDS calls, so the wrong translation still diverges (it never false-passes).

_CTX = ("fs/ext2/super.c", "ctx_set_mount_opt")
_CTX_CORRECT = (
    "set_field(F0_MASK_S_MOUNT_OPT, a0, field(F0_MASK_S_MOUNT_OPT, a0) | a1);\n"
    "set_field(F0_VALS_S_MOUNT_OPT, a0, field(F0_VALS_S_MOUNT_OPT, a0) | a1);\n0\n")
# drops the second field write -> must diverge even with directed coverage
_CTX_WRONG = (
    "set_field(F0_MASK_S_MOUNT_OPT, a0, field(F0_MASK_S_MOUNT_OPT, a0) | a1);\n0\n")


@pytest.fixture(scope="module")
def ctx_prep():
    return _H.prepare(_R.gate(*_CTX))


def test_baseline_boundary_sweep_covers_arg_guard(ctx_prep):
    # ctx_set_mount_opt's `|= flag` writes are ARGUMENT-driven; the round-2
    # per-param boundary sweep now covers them without directed search. (It
    # formerly REFUSED_COVERAGE; the stronger baseline closed this arg-branch
    # gap. Directed synth remains for STATE-dependent guards below.)
    r = _H.close(ctx_prep, _CTX_CORRECT)
    assert r["verdict"] == "MATCH", r


def test_directed_still_matches_correct(ctx_prep):
    # directed synth only ADDS rounds -> a correct candidate still matches.
    d = _H.with_directed(ctx_prep)
    r = _H.close(d, _CTX_CORRECT)
    assert r["verdict"] == "MATCH", r


def test_directed_still_catches_wrong(ctx_prep):
    d = _H.with_directed(ctx_prep)
    r = _H.close(d, _CTX_WRONG)
    assert r["verdict"].startswith("DIVERGE"), r


# ---- interprocedural ladder ------------------------------------------------
# A caller F that calls a same-file global-mutating helper G. The ladder admits
# G (inlines its real C, folds its global footprint g_total/g_count into F's
# cell vector), and the differential arbitrates the whole composite. Written to
# a temp file under $KSRC so gate() can resolve it, then cleaned up.

_IP_SRC = """static long g_total;
static int g_count;
static void bump(int by){ g_total += by; g_count++; }
void accumulate(int a, int b){ bump(a); if (b > 0) bump(b); }
"""


@pytest.fixture(scope="module")
def prep_ip():
    ksrc = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
    path = os.path.join(ksrc, "_lockstep_ip_test.c")
    open(path, "w").write(_IP_SRC)
    try:
        yield _H.prepare(_R.gate("_lockstep_ip_test.c", "accumulate"))
    finally:
        os.remove(path)


def test_ladder_admits_and_folds(prep_ip):
    assert "bump" in prep_ip["rec"]["inlined_callees"]
    assert set(prep_ip["rec"]["globals"]) == {"g_total", "g_count"}


_IP_CORRECT = ("set_g(G_G_TOTAL,g(G_G_TOTAL)+a0); set_g(G_G_COUNT,g(G_G_COUNT)+1);\n"
               "if a1>0 { set_g(G_G_TOTAL,g(G_G_TOTAL)+a1); set_g(G_G_COUNT,g(G_G_COUNT)+1); }\n0\n")


def test_ladder_correct_composite_matches(prep_ip):
    assert _H.close(prep_ip, _IP_CORRECT)["verdict"] == "MATCH"


def test_ladder_over_credit_diverges(prep_ip):
    # drops the helper's count++ effect on the folded global -> caught only
    # because the callee footprint was folded into the compared cell vector.
    body = ("set_g(G_G_TOTAL,g(G_G_TOTAL)+a0);\nif a1>0 { set_g(G_G_TOTAL,g(G_G_TOTAL)+a1); }\n0\n")
    assert _H.close(prep_ip, body)["verdict"] == "DIVERGE:state"


def test_ladder_dropped_guard_diverges(prep_ip):
    # calls the helper unconditionally -> caught by the round-2 arg boundary
    # sweep (b <= 0 exercised), not the shared-LCG draw.
    body = ("set_g(G_G_TOTAL,g(G_G_TOTAL)+a0); set_g(G_G_COUNT,g(G_G_COUNT)+1);\n"
            "set_g(G_G_TOTAL,g(G_G_TOTAL)+a1); set_g(G_G_COUNT,g(G_G_COUNT)+1);\n0\n")
    assert _H.close(prep_ip, body)["verdict"] == "DIVERGE:state"


# ---- interprocedural ladder: STRUCT-mutating helper -----------------------
# The pattern with real reach: F(struct*, ...) calls a same-file setter G(q,...)
# that writes q->fields. The ladder maps G's struct param to F's, folds G's
# field-writes into F's struct cell vector, inlines G's C, and the differential
# arbitrates the composite. (Global-only helpers measured ~0 real reach.)

_IPS_SRC = """struct st {
\tint a;
\tint b;
\tint n;
};
static void set_ab(struct st *q, int v){ q->a = v; q->b = v + 1; q->n++; }
void configure(struct st *p, int x){ set_ab(p, x); if (x > 10) p->b = 0; }
"""


@pytest.fixture(scope="module")
def prep_ips():
    ksrc = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
    path = os.path.join(ksrc, "_lockstep_ips_test.c")
    open(path, "w").write(_IPS_SRC)
    try:
        yield _H.prepare(_R.gate("_lockstep_ips_test.c", "configure"))
    finally:
        os.remove(path)


def test_struct_helper_folds_fields(prep_ips):
    assert "set_ab" in prep_ips["rec"]["inlined_callees"]
    # the helper's field writes are folded into the caller's struct footprint
    assert set(prep_ips["rec"]["write_fields"]) == {"p->a", "p->b", "p->n"}


_IPS_CORRECT = ("set_field(F0_A,a0,a1); set_field(F0_B,a0,a1+1); "
                "set_field(F0_N,a0,field(F0_N,a0)+1);\nif a1>10 { set_field(F0_B,a0,0); }\n0\n")


def test_struct_helper_correct_matches(prep_ips):
    assert _H.close(prep_ips, _IPS_CORRECT)["verdict"] == "MATCH"


def test_struct_helper_over_credit_diverges(prep_ips):
    # drops the helper's n++ on the folded field -> caught only because the
    # callee's field footprint was folded into the compared cell vector.
    body = ("set_field(F0_A,a0,a1); set_field(F0_B,a0,a1+1);\n"
            "if a1>10 { set_field(F0_B,a0,0); }\n0\n")
    assert _H.close(prep_ips, body)["verdict"] == "DIVERGE:state"


# ---- logging strip (base-gate widening) ------------------------------------
# pr_*/dev_*/WARN* are pure-logging: no modeled-state effect, execution
# continues (WARN_ON returns its condition). Strippable like locks for the
# state-transition claim. BUG/panic stay refused (they abort control flow).
# Measured +6 sole-blocker accepts over kernel+mm+lib.

_LOG_SRC = """static int g_state;
void set_state(int v){
\tif (WARN_ON(v < 0))
\t\treturn;
\tpr_info("setting %d", v);
\tg_state = v;
}
"""


@pytest.fixture(scope="module")
def prep_log():
    ksrc = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
    path = os.path.join(ksrc, "_lockstep_log_test.c")
    open(path, "w").write(_LOG_SRC)
    try:
        yield _H.prepare(_R.gate("_lockstep_log_test.c", "set_state"))
    finally:
        os.remove(path)


def test_logging_stripped_flag(prep_log):
    assert prep_log["rec"]["flags"]["logging_stripped"] is True


def test_logging_correct_matches(prep_log):
    # WARN_ON(v<0) preserves the guard: writes only when v >= 0
    assert _H.close(prep_log, "if a0 < 0 { return 0; }\nset_g(G_G_STATE, a0);\n0\n")["verdict"] == "MATCH"


def test_logging_warn_guard_is_load_bearing(prep_log):
    # dropping the WARN_ON guard writes when v<0 -> caught (WARN_ON's condition
    # semantics survive the strip; the boundary sweep exercises v<0).
    assert _H.close(prep_log, "set_g(G_G_STATE, a0);\n0\n")["verdict"] == "DIVERGE:state"
