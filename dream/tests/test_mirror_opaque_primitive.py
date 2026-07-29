"""Opaque-primitive sizing: a field of a config-dependent kernel primitive
(spinlock_t, atomic_t, struct mutex, ...) is REFUSED unless its real size has
been probed in-kernel (probe_primitives.py -> primitive_sizes.json), in which
case it is emitted as an alignment-matching integer array so the parent struct's
field offsets are correct. The parent's file-scope static_assert re-certifies
against the real kernel; a wrong probe value cannot pass (proven by the gate's
negative control).

These tests are hermetic: they patch mirror.PRIMITIVE_SIZES rather than depend
on the config-specific probed values, so they pin the *mechanism*, not a size.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mirror"))
import mirror  # noqa: E402


@pytest.fixture
def probed(monkeypatch):
    """A known primitive-size table (LOCKDEP-ish sizes) for deterministic tests."""
    monkeypatch.setattr(mirror, "PRIMITIVE_SIZES", {
        "spinlock_t": [64, 8],
        "atomic_t": [4, 4],
        "struct mutex": [136, 8],
        "struct list_head": [16, 8],
    })


def _fields(rows):
    return {f: o for _, f, o in rows}


def test_spinlock_field_mirrors_with_probe(probed):
    src = "struct s {\n\tspinlock_t lock;\n\tint x;\n\tint y;\n};"
    m = mirror.mirror(src, "s")
    rows = _fields(m["fields"])
    assert dict(rows) == {"lock": 0, "x": 64, "y": 68}
    assert m["size"] == 72
    # opaque field is an alignment-matching integer array: 64B / align 8 -> [u64; 8]
    assert any(rty == "[u64; 8]" and fn == "lock" for rty, fn, _ in m["fields"])


def test_atomic_single_element_is_scalar(probed):
    # size==align -> count 1 -> a bare scalar, not [u32; 1] (identical layout)
    src = "struct s {\n\tatomic_t c;\n\tint n;\n};"
    m = mirror.mirror(src, "s")
    assert any(rty == "u32" and fn == "c" for rty, fn, _ in m["fields"])
    assert _fields(m["fields"]) == {"c": 0, "n": 4}


def test_struct_mutex_by_value_mirrors_with_probe(probed):
    src = "struct s {\n\tint head;\n\tstruct mutex m;\n\tint tail;\n};"
    m = mirror.mirror(src, "s")
    rows = _fields(m["fields"])
    # head@0, mutex padded to align 8 -> @8, size 136 -> tail@144
    assert rows == {"head": 0, "m": 8, "tail": 144}
    assert m["size"] == 152


def test_unprobed_primitive_still_refuses(monkeypatch):
    # empty cache -> the sound default: refuse, do not guess.
    monkeypatch.setattr(mirror, "PRIMITIVE_SIZES", {})
    src = "struct s {\n\tspinlock_t lock;\n\tint x;\n};"
    with pytest.raises(mirror.Unsupported) as e:
        mirror.mirror(src, "s")
    assert "opaque kernel type" in str(e.value)
    assert "probe_primitives" in str(e.value)


def test_bad_shape_refuses(monkeypatch):
    # size not a multiple of align -> refuse (never emit an unsound layout)
    monkeypatch.setattr(mirror, "PRIMITIVE_SIZES", {"spinlock_t": [7, 4]})
    src = "struct s {\n\tspinlock_t lock;\n\tint x;\n};"
    with pytest.raises(mirror.Unsupported):
        mirror.mirror(src, "s")


def test_unknown_align_refuses(monkeypatch):
    monkeypatch.setattr(mirror, "PRIMITIVE_SIZES", {"spinlock_t": [16, 16]})
    src = "struct s {\n\tspinlock_t lock;\n\tint x;\n};"
    with pytest.raises(mirror.Unsupported):
        mirror.mirror(src, "s")


def test_guard_is_nonvacuous_static_assert_form(probed):
    # the kernel guard must be a file-scope `== ` static_assert (fires
    # unconditionally), NOT a `!=` BUILD_BUG_ON inside a DCE-able function.
    src = "struct s {\n\tspinlock_t lock;\n\tint x;\n};"
    m = mirror.mirror(src, "s")
    assert "static_assert(sizeof(struct s) == 72" in m["c_guard"]
    assert "static_assert(offsetof(struct s, x) == 64" in m["c_guard"]
    assert "BUILD_BUG_ON" not in m["c_guard"]


def test_probe_cache_is_present_and_sane():
    # the committed probe output exists and has the expected shape.
    sizes = mirror._load_primitive_sizes()
    assert sizes, "primitive_sizes.json missing — run probe_primitives.py"
    for ctype, v in sizes.items():
        assert len(v) == 2 and v[0] > 0 and v[1] in (1, 2, 4, 8), (ctype, v)
    # sanity: atomic_t is 4/4 in every config
    assert sizes.get("atomic_t") == [4, 4]
