"""Lock the entanglement router's classification on representative shapes, so the
core-composition measurement stays meaningful as the heuristics evolve.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "router"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "widerun"))
import entangle  # noqa: E402


CASES = [
    ("pure_leaf",       "int f(int a,int b){ return a>b? a-b : b-a; }", ""),
    ("struct_reader",   "int g(struct x *p){ if(p->flags) return p->hi; return p->lo; }", ""),
    ("mmio",            "void w(void*b){ writel(1, b+0x10); }", ""),
    ("concurrent",      "void c(struct x*p){ spin_lock(&p->lock); p->n++; spin_unlock(&p->lock); }", ""),
    ("concurrent",      "void pc(void){ this_cpu_inc(counter); }", ""),
    ("concurrent",      "int r(void){ rcu_read_lock(); int v=*rcu_dereference(g); rcu_read_unlock(); return v; }", ""),
    ("bounded_state",   "void b(void){ global_counter++; state_flag = 1; }", ""),
    ("bounded_state",   "void b2(struct x*p){ p->count = p->count + 1; g_total++; }", ""),
    ("unbounded_state", "void gr(struct list_head*n,struct list_head*h){ list_add(n,h); }", ""),
    ("unbounded_state", "void* a(int n){ return kmalloc(n, 0); }", ""),
    ("arch_asm",        "unsigned long rd(void){ unsigned long v; asm volatile(\"mrs %0, x\":\"=r\"(v)); return v; }", "kernel/x.c"),
    ("arch_asm",        "void ax(void){ setup(); }", "arch/arm64/kernel/setup.c"),
]


def test_classification():
    for want, body, path in CASES:
        got, reason = entangle.classify(body, path, "")
        assert got == want, f"{body[:40]!r}: got {got} ({reason}), want {want}"


def test_every_class_has_a_route():
    for klass in entangle.ROUTE:
        oracle, status = entangle.ROUTE[klass]
        assert oracle and status in {"DONE", "PROTOTYPED", "BUILD", "HARD", "FLOOR"}


def test_rollup_buckets_partition_the_classes():
    covered = entangle.AUTO_NOW | entangle.UNLOCKS | entangle.FLOOR
    assert covered == set(entangle.ROUTE), "every class must fall in exactly one rollup bucket"
    assert not (entangle.AUTO_NOW & entangle.UNLOCKS)
    assert not (entangle.AUTO_NOW & entangle.FLOOR)
    assert not (entangle.UNLOCKS & entangle.FLOOR)
