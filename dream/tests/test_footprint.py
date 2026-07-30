"""Lock the effect-trace footprint extractor: write-set precision + the
boundedness verdict (bounded => effect-trace oracle applies; the refutations are
the honest reach limiters, chief among them opaque callees = the CGIR gap).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "efftrace"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "widerun"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "router"))
import footprint  # noqa: E402


def test_bounded_accumulator():
    r = footprint.extract("long acct(long v){ g_total += v; g_count++; if(v>g_max) g_max=v; return g_total; }")
    assert r["bounded"]
    assert r["writes"] == {"g_total", "g_count", "g_max"}


def test_bounded_field_writes():
    r = footprint.extract("void inc(struct x*p){ p->count = p->count + 1; g_total++; }")
    assert r["bounded"]
    assert "p->count" in r["writes"] and "g_total" in r["writes"]


def test_pointer_graph_unbounded():
    r = footprint.extract("void a(struct list_head*n,struct list_head*h){ list_add(n,h); }")
    assert not r["bounded"] and "graph" in r["reason"]


def test_alloc_unbounded():
    assert not footprint.extract("void* m(int n){ return kmalloc(n,0); }")["bounded"]


def test_opaque_call_refuted_flags_cgir_gap():
    r = footprint.extract("void o(void){ g_flag=1; do_something_effectful(); }", "o")
    assert not r["bounded"]
    assert "opaque call" in r["reason"]  # the recoverable-by-CGIR class


def test_local_only_no_footprint():
    assert not footprint.extract("int f(int a){ int x=a*2; return x; }")["bounded"]


def test_dynamic_index_write_unbounded():
    assert not footprint.extract("void d(int i,int v){ g_arr[i]=v; }")["bounded"]
