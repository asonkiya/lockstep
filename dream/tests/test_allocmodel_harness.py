"""Gate the PRODUCTIZED allocator-init oracle (dream/allocmodel/{reach,harness}.py).

proof.py proved the fresh-arena-slot mechanism on a synthetic subject; the
harness runs the real C verbatim (k[mz]alloc* bump-allocated over a host arena)
against a Rust fresh-slot model. Pinned contract, on a KSRC fixture exercising
the full vocabulary (alloc + NULL-guard + field inits incl. a narrow u8 field +
an arg-guarded write + a global side effect):

  * reach.gate resolves the allocated struct, its written fields, the global;
  * correct body -> MATCH;
  * no_init (valid id returned, fields never written) -> DIVERGE:state — the
    over-credit case a pointer-only oracle false-passes;
  * double alloc -> DIVERGE:ret (the fresh-slot sequence is observable);
  * dropped arg-guard (writes flag unconditionally) -> DIVERGE:state (the
    boundary sweep exercises both branches);
  * dropped global side effect -> DIVERGE:state;
  * an empty workload -> REFUSED_COVERAGE even for the correct body.

Needs host cc + rustc + the kernel tree at $KSRC; skipped otherwise.
"""
import copy
import importlib.util
import os
import shutil

import pytest

_D = os.path.join(os.path.dirname(__file__), "..", "allocmodel")


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_D, fname))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_R = _H = None
try:
    _R = _load("alloc_reach_t", "reach.py")
    _H = _load("alloc_harness_t", "harness.py")
except Exception:
    pass

_KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
pytestmark = pytest.mark.skipif(
    not (shutil.which("cc") and shutil.which("rustc") and _R and _H
         and os.path.isdir(_KSRC)),
    reason="needs host cc + rustc + $KSRC kernel tree",
)

_SRC = """struct amt_obj {
\tint x;
\tint y;
\tunsigned char flag;
};
static int g_nalloc;
struct amt_obj *amt_make(int a, int b)
{
\tstruct amt_obj *p;

\tp = kzalloc(sizeof(*p), GFP_KERNEL);
\tif (!p)
\t\treturn NULL;
\tp->x = a;
\tp->y = b + 1;
\tif (a > 10)
\t\tp->flag = 1;
\tg_nalloc++;
\treturn p;
}
"""


@pytest.fixture(scope="module")
def prep():
    path = os.path.join(_KSRC, "_lockstep_alloc_test.c")
    open(path, "w").write(_SRC)
    try:
        yield _H.prepare(_R.gate("_lockstep_alloc_test.c", "amt_make"))
    finally:
        os.remove(path)


def test_gate_record(prep):
    rec = prep["rec"]
    assert rec["alloc_struct"] == "amt_obj"
    assert rec["write_afields"] == ["flag", "x", "y"]
    assert list(rec["globals"]) == ["g_nalloc"]
    assert rec["write_globals"] == ["g_nalloc"]
    assert rec["flags"]["alloc_stripped"] is True
    assert rec["flags"]["kmalloc_zero_modeled"] is False   # kzalloc form


_CORRECT = """
    let p = alloc();
    set_af(A_X, p, a0);
    set_af(A_Y, p, a1 + 1);
    if a0 > 10 { set_af(A_FLAG, p, 1); }
    set_g(G_G_NALLOC, g(G_G_NALLOC) + 1);
    p
"""


def test_correct_matches(prep):
    r = _H.close(prep, _CORRECT)
    assert r["verdict"] == "MATCH", r


def test_no_init_over_credit_diverges(prep):
    # valid id returned, contents never written — a pointer-only oracle passes
    # this; the field differential must not.
    body = """
    let p = alloc();
    set_g(G_G_NALLOC, g(G_G_NALLOC) + 1);
    p
"""
    r = _H.close(prep, body)
    assert r["verdict"] == "DIVERGE:state", r


def test_double_alloc_diverges_on_ret(prep):
    body = """
    let _leak = alloc();
    let p = alloc();
    set_af(A_X, p, a0);
    set_af(A_Y, p, a1 + 1);
    if a0 > 10 { set_af(A_FLAG, p, 1); }
    set_g(G_G_NALLOC, g(G_G_NALLOC) + 1);
    p
"""
    r = _H.close(prep, body)
    assert r["verdict"] == "DIVERGE:ret", r


def test_dropped_arg_guard_diverges(prep):
    body = """
    let p = alloc();
    set_af(A_X, p, a0);
    set_af(A_Y, p, a1 + 1);
    set_af(A_FLAG, p, 1);
    set_g(G_G_NALLOC, g(G_G_NALLOC) + 1);
    p
"""
    r = _H.close(prep, body)
    assert r["verdict"] == "DIVERGE:state", r


def test_dropped_global_effect_diverges(prep):
    body = """
    let p = alloc();
    set_af(A_X, p, a0);
    set_af(A_Y, p, a1 + 1);
    if a0 > 10 { set_af(A_FLAG, p, 1); }
    p
"""
    r = _H.close(prep, body)
    assert r["verdict"] == "DIVERGE:state", r


def test_empty_workload_refuses_coverage(prep):
    starved = copy.deepcopy(prep)
    starved["rounds"] = [{"seeds": [], "calls": []}]
    r = _H.close(starved, _CORRECT)
    assert r["verdict"] == "REFUSED_COVERAGE", r


# ---- INIT_LIST_HEAD strip (the container-composition seam) -----------------
# The allocated struct carries a list_head; the fn INIT_LIST_HEADs it. The
# list field is not a modeled cell either way, so the strip claims the
# non-list state transition (FLAGGED); the harness's variadic no-op macro
# discards the &p->link arg so the scalar-only host struct compiles.

_LI_SRC = """struct amt_node {
\tint v;
\tstruct list_head link;
};
struct amt_node *amt_node_make(int a)
{
\tstruct amt_node *p = kzalloc(sizeof(*p), GFP_KERNEL);

\tif (!p)
\t\treturn NULL;
\tINIT_LIST_HEAD(&p->link);
\tp->v = a + 1;
\treturn p;
}
"""


@pytest.fixture(scope="module")
def prep_li():
    path = os.path.join(_KSRC, "_lockstep_alloc_li_test.c")
    open(path, "w").write(_LI_SRC)
    try:
        yield _H.prepare(_R.gate("_lockstep_alloc_li_test.c", "amt_node_make"))
    finally:
        os.remove(path)


def test_list_init_stripped_flag(prep_li):
    assert prep_li["rec"]["flags"]["list_init_stripped"] is True
    assert prep_li["rec"]["write_afields"] == ["v"]


def test_list_init_correct_matches(prep_li):
    r = _H.close(prep_li, "let p = alloc();\nset_af(A_V, p, a0 + 1);\np\n")
    assert r["verdict"] == "MATCH", r


def test_list_init_wrong_value_diverges(prep_li):
    r = _H.close(prep_li, "let p = alloc();\nset_af(A_V, p, a0);\np\n")
    assert r["verdict"] == "DIVERGE:state", r
