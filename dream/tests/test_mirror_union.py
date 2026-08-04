"""Union host-layout (Sweep-1 census #1 widener: 59+ readers refusals).

A union in a repr(C) struct occupies max(member sizes) padded to max(member
align). The mirror emits it as an alignment-matching blob of that size so a
PARENT struct's downstream field offsets are correct — sound exactly like the
opaque-primitive blob (a reader that reads INTO the union just diverges, an
honest miss; the in-kernel BUILD_BUG_ON re-certifies the parent layout). A
union whose members can't all be sized still refuses.
"""
import importlib.util
import os

import pytest

_spec = importlib.util.spec_from_file_location(
    "mirror_union_t", os.path.join(os.path.dirname(__file__), "..", "mirror", "mirror.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _offs(m):
    return {f: o for _, f, o in m["fields"]}


def test_named_union_field_sizes_as_max_member():
    # union {int; long long;} -> 8 bytes, align 8. head@0(4)->pad->u@8(8)->tail@16
    src = ("struct hu {\n\tint head;\n\tunion { int a; long long b; } u;\n"
           "\tint tail;\n};\n")
    m = M.mirror(src, "hu")
    o = _offs(m)
    assert o["head"] == 0 and o["u"] == 8 and o["tail"] == 16
    assert m["size"] == 24


def test_anonymous_union_is_padding():
    # anonymous union { u32; u16; } -> 4 bytes at the current offset
    src = "struct hu2 {\n\tu16 x;\n\tunion { u32 p; u16 q; };\n\tu16 y;\n};\n"
    m = M.mirror(src, "hu2")
    o = _offs(m)
    assert o["x"] == 0            # u16
    # union @4 (align 4, size 4) -> y @ 8
    assert o["y"] == 8 and m["size"] == 12


def test_union_of_arrays():
    # union { char c[5]; int i; } -> align 4, max size 5 -> padded 8
    src = "struct hu3 {\n\tint head;\n\tunion { char c[5]; int i; } u;\n};\n"
    m = M.mirror(src, "hu3")
    o = _offs(m)
    assert o["u"] == 4
    assert m["size"] == 12       # 4 + 8


def test_union_emits_and_compiles_rust():
    src = ("struct hu4 {\n\tu32 head;\n\tunion { u64 a; u32 b; u8 c; } u;\n"
           "\tu32 tail;\n};\n")
    m = M.mirror(src, "hu4")
    # rust mirror must compile (const layout asserts included)
    import shutil, subprocess, tempfile
    if not shutil.which("rustc"):
        pytest.skip("no rustc")
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "m.rs"), "w").write(
            "#![allow(non_camel_case_types,non_snake_case,dead_code)]\n" + m["rust"] + "\n")
        r = subprocess.run(["rustc", "--edition", "2021", "--crate-type=lib",
                            os.path.join(d, "m.rs"), "-o", os.path.join(d, "libm.rlib")],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_union_with_unsizable_member_refuses():
    # a member that's a nested unknown struct-by-value -> whole union refuses
    src = "struct hu5 {\n\tint h;\n\tunion { struct totally_unknown_t s; int i; } u;\n};\n"
    with pytest.raises(M.Unsupported):
        M.mirror(src, "hu5")
