"""Lock the interprocedural footprint closure: transitive folding of bounded
callees, refutation on unbounded/external callees, pure-builtin ignore, recursion.
"""
import os
import sys

for p in ("efftrace", "widerun", "router", "cluster"):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", p))
import interproc as ip  # noqa: E402

CORPUS = {
    "f_ok":    "void f_ok(void){ g_a = 1; helper(); }",
    "helper":  "void helper(void){ g_b = 2; }",
    "f_graph": "void f_graph(struct list_head*n){ g_c=1; list_add(n,&g_head); }",
    "f_ext":   "void f_ext(void){ g_d=1; some_kernel_api(); }",
    "f_chain": "void f_chain(void){ g_e=1; helper(); mid(); }",
    "mid":     "void mid(void){ g_f=3; helper(); }",
    "f_pure":  "void f_pure(void){ g_g = min(1,2); }",
    "rec_a":   "void rec_a(void){ g_h=1; rec_b(); }",
    "rec_b":   "void rec_b(void){ g_i=1; rec_a(); }",
}


def _r(name):
    return ip.resolve(name, CORPUS, {}, set())


def test_folds_bounded_callee():
    v, w, _ = _r("f_ok")
    assert v == ip.BOUNDED and w == {"g_a", "g_b"}


def test_transitive_fold():
    v, w, _ = _r("f_chain")
    assert v == ip.BOUNDED and w == {"g_b", "g_e", "g_f"}


def test_graph_callee_unbounded():
    assert _r("f_graph")[0] == ip.UNBOUNDED


def test_external_callee_unresolved():
    assert _r("f_ext")[0] == ip.UNRESOLVED


def test_pure_builtin_ignored():
    v, w, _ = _r("f_pure")
    assert v == ip.BOUNDED and w == {"g_g"}


def test_recursion_refuted():
    # a recursive cycle must not hang and must not be claimed bounded
    assert _r("rec_a")[0] == ip.UNBOUNDED
