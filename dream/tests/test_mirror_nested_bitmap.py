#!/usr/bin/env python3
"""Soundness tests for the host-sound mirror extensions (dream/mirror/mirror.py):

  1. resolve_struct_source finds a `struct <name>` def near a file / under $KSRC.
  2. recursive nested-struct-of-scalars (`struct Y field;` by value) is mirrored:
     Y's mirror is emitted alongside with correct nested size/offsets, and the
     parent's field participates in LP64 packing.
  3. DECLARE_BITMAP(name, NBITS) -> [u64; ceil(NBITS/64)]; TYPE name[MACRO]
     resolves MACRO from an object-like #define.

The invariant that must never break: a layout the generator cannot fix with
certainty is REFUSED (mirror.Unsupported), never guessed —
  * a config-dependent kernel primitive (spinlock_t, atomic_t, ...) REFUSES;
  * DECLARE_BITMAP with an unresolvable NBITS (e.g. NR_CPUS -> CONFIG_NR_CPUS)
    REFUSES;
  * a nested struct-by-value whose inner type can't be mirrored propagates the
    REFUSAL to the parent.

Real-header cases skip (not fail) when $KSRC is unset/missing; the rustc compile
bonus is toolchain-gated. Synthetic cases are deterministic.

Run:  python3 -m pytest dream/tests/test_mirror_nested_bitmap.py -q
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_DREAM = _HERE.parent.parent  # .../lockstep/dream
sys.path.insert(0, str(_DREAM / "mirror"))

import mirror as M  # noqa: E402

_KSRC = os.environ.get("KSRC", "")


def _ksrc_path(rel: str) -> str:
    if not _KSRC:
        pytest.skip("KSRC not set")
    p = Path(_KSRC) / rel
    if not p.is_file():
        pytest.skip(f"{rel} not present under KSRC")
    return str(p)


def _rustc_ok(rust_src: str) -> tuple[bool, str]:
    rustc = shutil.which("rustc")
    if not rustc:
        pytest.skip("rustc not available")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "m.rs"
        f.write_text("#![allow(dead_code, non_camel_case_types)]\n" + rust_src + "\nfn main() {}\n")
        r = subprocess.run(
            [rustc, "--edition=2021", "--emit=metadata", "-o", str(Path(d) / "out"), str(f)],
            capture_output=True, text=True,
        )
        return r.returncode == 0, r.stderr


# ============================================================================
# 1. resolve_struct_source
# ============================================================================


def test_resolve_struct_source_near_file():
    hdr = _ksrc_path("include/linux/timecounter.h")
    txt = M.resolve_struct_source("cyclecounter", near_file=hdr)
    assert txt is not None
    assert "struct cyclecounter {" in txt


def test_resolve_struct_source_falls_back_to_include():
    # old_timespec32 is NOT in time32.h (it lives in vdso/time32.h under include);
    # resolution must still find it via the $KSRC/include fallback.
    hdr = _ksrc_path("include/linux/time32.h")
    txt = M.resolve_struct_source("old_timespec32", near_file=hdr)
    assert txt is not None
    assert "struct old_timespec32 {" in txt


def test_resolve_struct_source_missing_returns_none():
    if not _KSRC:
        pytest.skip("KSRC not set")
    assert M.resolve_struct_source("this_struct_does_not_exist_xyzzy", near_file=None) is None


# ============================================================================
# 2. recursive nested-struct-of-scalars (real: ieee80211_mu_edca_param_set)
# ============================================================================


def test_nested_struct_of_scalars_real_mirrors_with_correct_layout():
    path = _ksrc_path("include/linux/ieee80211-he.h")
    src = open(path).read()
    m = M.mirror(src, "ieee80211_mu_edca_param_set", near_file=path)
    # parent: u8 mu_qos_info@0, then 4x a 3-byte all-u8 record (align 1) at
    # 1,4,7,10 -> total 13. (All-u8 => packed is a no-op, so LP64 == real ABI.)
    assert m["size"] == 13, m
    off = {f: o for _, f, o in m["fields"]}
    assert off == {"mu_qos_info": 0, "ac_be": 1, "ac_bk": 4, "ac_vi": 7, "ac_vo": 10}, m["fields"]
    # the inner record was recursively mirrored and emitted alongside
    assert "ieee80211_he_mu_edca_param_ac_rec" in m["nested"], m["nested"]
    # the field's Rust type IS the nested mirror type (not a scalar / pointer)
    rowty = {f: rty for rty, f, _ in m["fields"]}
    assert rowty["ac_be"] == "Ieee80211HeMuEdcaParamAcRec", rowty
    # the emitted Rust carries BOTH structs + both size const-asserts
    assert "pub struct Ieee80211HeMuEdcaParamAcRec" in m["rust"]
    assert "pub struct Ieee80211MuEdcaParamSet" in m["rust"]
    assert "size_of::<Ieee80211HeMuEdcaParamAcRec>() == 3" in m["rust"]
    assert "size_of::<Ieee80211MuEdcaParamSet>() == 13" in m["rust"]
    # the C guard carries the nested struct's static_asserts too (file-scope,
    # non-DCE-able == form — not the vacuous BUILD_BUG_ON-in-a-function form)
    assert "sizeof(struct ieee80211_he_mu_edca_param_ac_rec) == 3" in m["c_guard"]
    assert "offsetof(struct ieee80211_mu_edca_param_set, ac_vo) == 10" in m["c_guard"]


def test_nested_struct_of_scalars_synthetic_packs_correctly():
    # synthetic (deterministic): inner {u32,u8}=8 bytes (align 4, tail pad),
    # outer {u16 head; struct inner nested; u16 tail}:
    #   head@0 (2), pad to 4, nested@4 (8), tail@12 (2) -> align 4 -> size 16.
    src = textwrap.dedent(
        """
        struct inner {
            unsigned int a;
            unsigned char b;
        };
        struct outer {
            unsigned short head;
            struct inner nested;
            unsigned short tail;
        };
        """
    )
    m = M.mirror(src, "outer")
    assert m["size"] == 16, m
    off = {f: o for _, f, o in m["fields"]}
    assert off == {"head": 0, "nested": 4, "tail": 12}, m["fields"]
    assert "inner" in m["nested"]
    # inner emitted first (dependency-ordered) so it is defined before use
    assert m["rust"].index("struct Inner") < m["rust"].index("struct Outer")


@pytest.mark.skipif(shutil.which("rustc") is None, reason="rustc not available")
def test_nested_struct_rustc_agrees_and_wrong_size_rejected():
    path = _ksrc_path("include/linux/ieee80211-he.h")
    src = open(path).read()
    m = M.mirror(src, "ieee80211_mu_edca_param_set", near_file=path)
    ok, err = _rustc_ok(m["rust"])
    assert ok, f"nested mirror should compile (rustc==generator), got:\n{err}"
    bad = m["rust"].replace("== 13", "== 14")
    ok_bad, _ = _rustc_ok(bad)
    assert not ok_bad, "a corrupted nested size const-assert must fail to compile"


# ============================================================================
# 3. DECLARE_BITMAP / fixed macro arrays (real: fb_blit_caps)
# ============================================================================


def test_declare_bitmap_real_fb_blit_caps():
    path = _ksrc_path("include/linux/fb.h")
    src = open(path).read()
    m = M.mirror(src, "fb_blit_caps", near_file=path)
    # DECLARE_BITMAP(x, FB_MAX_BLIT_WIDTH=64)  -> [u64; 1] @0
    # DECLARE_BITMAP(y, FB_MAX_BLIT_HEIGHT=128)-> [u64; 2] @8
    # u32 len @24, u32 flags @28 -> size 32
    assert m["size"] == 32, m
    off = {f: o for _, f, o in m["fields"]}
    assert off == {"x": 0, "y": 8, "len": 24, "flags": 28}, m["fields"]
    rowty = {f: rty for rty, f, _ in m["fields"]}
    assert rowty["x"] == "[u64; 1]", rowty
    assert rowty["y"] == "[u64; 2]", rowty


def test_declare_bitmap_literal_and_shift_nbits():
    # literal NBITS and a `1 << 7` NBITS resolve to fixed [u64; K].
    src = textwrap.dedent(
        """
        struct bm {
            DECLARE_BITMAP(a, 128);
            DECLARE_BITMAP(b, 1 << 7);
            unsigned int n;
        };
        """
    )
    m = M.mirror(src, "bm")
    rowty = {f: rty for rty, f, _ in m["fields"]}
    assert rowty["a"] == "[u64; 2]", rowty     # ceil(128/64)=2
    assert rowty["b"] == "[u64; 2]", rowty     # ceil(128/64)=2
    off = {f: o for _, f, o in m["fields"]}
    assert off == {"a": 0, "b": 16, "n": 32}, m["fields"]
    assert m["size"] == 40, m                  # pad u32 to u64 align


def test_fixed_array_size_from_define_macro():
    # a plain `TYPE name[MACRO]` where MACRO is an object-like #define constant
    # (not just a literal `[N]`, which was the only case handled before).
    src = textwrap.dedent(
        """
        #define MYLEN 4
        struct arr {
            unsigned int items[MYLEN];
            unsigned int tail;
        };
        """
    )
    m = M.mirror(src, "arr")
    rowty = {f: rty for rty, f, _ in m["fields"]}
    assert rowty["items"] == "[u32; 4]", rowty
    off = {f: o for _, f, o in m["fields"]}
    assert off == {"items": 0, "tail": 16}, m["fields"]
    assert m["size"] == 20, m


# ============================================================================
# 4. REFUSALS — the soundness contract: never guess an unfixable layout
# ============================================================================


def test_refuses_spinlock_field_when_unprobed(monkeypatch):
    # a config-dependent kernel primitive has no host-known sizeof; UNTIL it is
    # probed in-kernel it must REFUSE (the sound default). See
    # test_mirror_opaque_primitive.py for the probed-and-mirrors path.
    monkeypatch.setattr(M, "PRIMITIVE_SIZES", {})
    src = textwrap.dedent(
        """
        struct haslock {
            unsigned int a;
            spinlock_t lock;
            unsigned int b;
        };
        """
    )
    with pytest.raises(M.Unsupported) as ei:
        M.mirror(src, "haslock")
    assert "spinlock_t" in str(ei.value)


@pytest.mark.parametrize("prim", ["struct mutex m", "atomic_t ct",
                                  "struct list_head node", "wait_queue_head_t wq"])
def test_refuses_config_dependent_primitives_when_unprobed(monkeypatch, prim):
    # each needs an in-kernel sizeof; with no probe table the generator REFUSES.
    monkeypatch.setattr(M, "PRIMITIVE_SIZES", {})
    src = f"struct s {{\n    unsigned int a;\n    {prim};\n    unsigned int b;\n}};\n"
    with pytest.raises(M.Unsupported):
        M.mirror(src, "s")


def test_refuses_declare_bitmap_unresolvable_nbits():
    # NR_CPUS -> CONFIG_NR_CPUS (an identifier) is not a host-resolvable literal.
    src = textwrap.dedent(
        """
        struct cm {
            DECLARE_BITMAP(bits, NR_CPUS);
            unsigned int n;
        };
        """
    )
    with pytest.raises(M.Unsupported) as ei:
        M.mirror(src, "cm")
    assert "DECLARE_BITMAP" in str(ei.value) or "NBITS" in str(ei.value)


def test_refuses_array_with_unresolvable_size():
    src = textwrap.dedent(
        """
        struct a {
            unsigned int items[SOME_UNKNOWN_CONST];
            unsigned int n;
        };
        """
    )
    with pytest.raises(M.Unsupported):
        M.mirror(src, "a")


def test_nested_refusal_propagates_to_parent():
    # inner contains a primitive host can't lay out -> the WHOLE parent REFUSES.
    src = textwrap.dedent(
        """
        struct inner {
            unsigned int a;
            weird_unknown_t z;
        };
        struct outer {
            unsigned int head;
            struct inner nested;
        };
        """
    )
    with pytest.raises(M.Unsupported):
        M.mirror(src, "outer")


def test_nested_refusal_propagates_real_old_itimerspec32():
    # real: old_itimerspec32 embeds old_timespec32, whose fields are old_time32_t
    # (a vdso typedef not in the host SCALAR table) -> parent must REFUSE, never
    # guess. (If old_time32_t were ever added to SCALAR this test would need
    # revisiting — that is the point: refusal is explicit, not silent.)
    path = _ksrc_path("include/linux/time32.h")
    src = open(path).read()
    with pytest.raises(M.Unsupported):
        M.mirror(src, "old_itimerspec32", near_file=path)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
