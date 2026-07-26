"""M1 concurrency-IR extractor — the static lock->data map.

Pinned on a synthetic subsystem with a clear spinlock, two critical sections,
and one field deliberately touched OUTSIDE the lock (must not appear protected).
"""

from extract import extract

_SAMPLE = r"""
#define SIZE 64

struct ring {
    spinlock_t lock;
    int head;
    int tail;
    int count;
    char buf[SIZE];
    const char *name;   /* touched only outside the lock -> not protected */
};

static void ring_push(struct ring *r, char c)
{
    spin_lock(&r->lock);
    r->buf[r->head] = c;                 /* buf: protected */
    r->head = (r->head + 1) % SIZE;      /* head: protected */
    r->count++;                          /* count: protected */
    spin_unlock(&r->lock);
}

static int ring_count(struct ring *r)
{
    int n;
    spin_lock_irqsave(&r->lock, flags);
    n = r->count;                        /* count: protected (again) */
    spin_unlock_irqrestore(&r->lock, flags);
    return n;
}

static void ring_set_name(struct ring *r, const char *s)
{
    r->name = s;                         /* NO lock held -> name not protected */
}
"""


def test_finds_lock_bearing_struct():
    ir = extract(_SAMPLE)
    assert "ring" in ir["structs"]
    assert "lock" in ir["structs"]["ring"]["locks"]
    assert ir["structs"]["ring"]["locks"]["lock"] == "spinlock_t"


def test_extracts_critical_sections():
    ir = extract(_SAMPLE)
    # two critical sections: ring_push and ring_count
    regions = [r for r in ir["regions"] if r["lock_field"] == "lock"]
    assert len(regions) == 2
    assert {r["function"] for r in regions} == {"ring_push", "ring_count"}


def test_protects_map_only_locked_fields():
    ir = extract(_SAMPLE)
    protected = set(ir["protects"]["ring"]["lock"])
    assert {"head", "count", "buf"} <= protected   # touched inside the lock
    assert "name" not in protected                 # only touched outside the lock


def test_flags_field_touched_outside_lock():
    ir = extract(_SAMPLE)
    # `name` is a field of a lock-bearing struct but never touched under the lock:
    # report it as unprotected-access so the map is honest about coverage.
    unprot = {(u["field"]) for u in ir["unprotected_accesses"]}
    assert "name" in unprot
