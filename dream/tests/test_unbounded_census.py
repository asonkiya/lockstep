"""Lock the unbounded-tail sub-classifier: each sub-shape maps to the research
technique sized in UNBOUNDED_RESEARCH.md.
"""
import os
import sys

for p in ("efftrace", "widerun", "router", "cluster"):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", p))
import unbounded_census as uc  # noqa: E402
import purity  # noqa: E402


def _sub(body):
    return uc.sub_classify(body, purity.owned_names(body) | purity.KEYWORDS)


def test_container_op():
    assert _sub("void f(struct list_head*n,struct list_head*h){ list_add(n,h); }") == "container_op"
    assert _sub("void g(struct rb_node*n){ rb_erase(n, &g_root); }") == "container_op"


def test_alloc():
    assert _sub("void* a(int n){ return kmalloc(n, 0); }") == "alloc"


def test_reactive():
    assert _sub("unsigned long r(void){ return jiffies; }") == "reactive"


def test_external_only():
    assert _sub("void e(void){ g_x = 1; some_api(); }") == "external_only"


def test_iter_loop():
    assert _sub("void l(struct list_head*h){ struct x*p; list_for_each_entry(p,h,n){ g_c++; } }") == "container_op"
    # a raw pointer-chase loop (no container macro) is an iter_loop
    assert _sub("void w(struct node*p){ while(p->next){ p = p->next; g_c++; } }") == "iter_loop"
