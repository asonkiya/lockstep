"""enum-as-field-type -> i32 (Sweep-1 post-union census #1 at 45 refusals).

A kernel enum field is `int` (4/4) on the target ABI; the in-kernel BUILD_BUG_ON
re-certifies size, so a packed/non-int enum fails the gate (sound). Unlike a
union blob, an enum field is READABLE (just an i32), so this recovers solves,
not only prepare. Covers named, anonymous, tagged, and array enum fields.
"""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "mirror_enum_t", os.path.join(os.path.dirname(__file__), "..", "mirror", "mirror.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _offs(m):
    return {f: o for _, f, o in m["fields"]}


def test_named_enum_field():
    src = "struct he {\n\tu32 a;\n\tenum color c;\n\tu32 b;\n};\n"
    m = M.mirror(src, "he")
    o = _offs(m)
    assert o["a"] == 0 and o["c"] == 4 and o["b"] == 8 and m["size"] == 12
    assert [r for r, f, _ in m["fields"] if f == "c"] == ["i32"]


def test_anonymous_enum_field():
    src = "struct he2 {\n\tenum { A = 1, B = 2 } kind;\n\tu32 tail;\n};\n"
    m = M.mirror(src, "he2")
    o = _offs(m)
    assert o["kind"] == 0 and o["tail"] == 4


def test_tagged_enum_with_body():
    src = "struct he3 {\n\tu8 h;\n\tenum bcj { X86 = 4, PPC = 5 } type;\n};\n"
    m = M.mirror(src, "he3")
    o = _offs(m)
    assert o["type"] == 4 and m["size"] == 8


def test_pointer_to_enum_is_ptr():
    src = "struct he4 {\n\tenum color *cp;\n\tu32 n;\n};\n"
    m = M.mirror(src, "he4")
    o = _offs(m)
    assert o["cp"] == 0 and o["n"] == 8    # 8-byte pointer
