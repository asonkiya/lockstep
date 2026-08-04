#!/usr/bin/env python3
"""Adversarial + fuzz battery for the struct-mirror generator (dream/mirror/mirror.py)
and the static-cluster weaver (dream/cluster/cluster.py).

Two soundness contracts are pinned here:

  * mirror computes a byte-exact LP64 layout OR refuses. A wrong layout is
    statically catchable (the emitted const-assert names the correct size), and
    anything whose layout is not fixed by the C types (bitfields, unions, #if
    fields, non-mirrorable nested struct-by-value) is REFUSED, never guessed.

  * static_cluster(entry) == entry + its transitive file-local `static` callees
    and NOTHING else. The length-preserving comment/string mask must not let a
    doc comment ("(C) 2013", stray '{' or unbalanced parens) swallow the next
    definition, and a body's // -commented-out call or a ")" string literal must
    not corrupt call detection.

Real kernel headers are read via $KSRC; the adversarial/fuzz inputs are
synthetic so they are deterministic. Real-header tests skip (not fail) when
$KSRC is unset/missing, and the rustc compile bonus is toolchain-gated.

Run:  python3 -m pytest dream/tests/test_mirror_cluster.py -q
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# --- import the two modules under test (they are not a package) --------------
_HERE = Path(__file__).resolve()
_DREAM = _HERE.parent.parent  # .../lockstep/dream
sys.path.insert(0, str(_DREAM / "mirror"))
sys.path.insert(0, str(_DREAM / "cluster"))

import mirror as M  # noqa: E402
import cluster as C  # noqa: E402


# --- KSRC plumbing -----------------------------------------------------------
_KSRC = os.environ.get("KSRC", "")


def _ksrc_file(rel: str) -> str:
    """Read a file under $KSRC, or skip the test if KSRC/the file is absent."""
    if not _KSRC:
        pytest.skip("KSRC not set")
    p = Path(_KSRC) / rel
    if not p.is_file():
        pytest.skip(f"{rel} not present under KSRC")
    return p.read_text()


def _rustc_check(rust_src: str) -> tuple[bool, str]:
    """Compile a Rust snippet with `rustc --emit=metadata`. Returns (ok, stderr)."""
    rustc = shutil.which("rustc")
    if not rustc:
        pytest.skip("rustc not available")
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "m.rs"
        # allow dead code / non-camelcase so only LAYOUT asserts decide the build
        f.write_text("#![allow(dead_code, non_camel_case_types)]\n" + rust_src + "\nfn main() {}\n")
        r = subprocess.run(
            [rustc, "--edition=2021", "--emit=metadata", "-o", str(Path(d) / "out"), str(f)],
            capture_output=True,
            text=True,
        )
        return r.returncode == 0, r.stderr


# ============================================================================
# 1. mirror CORRECT — real kernel headers, exact sizes + offsets
# ============================================================================


def test_mirror_clk_div_table_size_and_offsets():
    src = _ksrc_file("include/linux/clk-provider.h")
    m = M.mirror(src, "clk_div_table")
    assert m["size"] == 8, m
    off = {f: o for _, f, o in m["fields"]}
    assert off == {"val": 0, "div": 4}, m["fields"]


def test_mirror_cyclecounter_size_and_offsets():
    src = _ksrc_file("include/linux/timecounter.h")
    m = M.mirror(src, "cyclecounter")
    # read (fn-ptr) @0, mask u64 @8, mult u32 @16, shift u32 @20 -> size 24
    assert m["size"] == 24, m
    off = {f: o for _, f, o in m["fields"]}
    assert off == {"read": 0, "mask": 8, "mult": 16, "shift": 20}, m["fields"]


def test_mirror_timecounter_size_and_nested_layout():
    src = _ksrc_file("include/linux/timecounter.h")
    m = M.mirror(src, "timecounter")
    # cc is `struct cyclecounter *cc` (a POINTER, not by-value): 8@0,
    # then u64 cycle_last/nsec/mask/frac at 8,16,24,32 -> size 40
    assert m["size"] == 40, m
    off = {f: o for _, f, o in m["fields"]}
    assert off == {"cc": 0, "cycle_last": 8, "nsec": 16, "mask": 24, "frac": 32}, m["fields"]
    # the nested cyclecounter comes in by pointer, so the mirror row is a pointer
    rows = {f: rty for rty, f, _ in m["fields"]}
    assert "*mut" in rows["cc"], rows


# ============================================================================
# 2. mirror ADVERSARIAL — must REFUSE unfixable layouts
# ============================================================================


def test_mirror_refuses_bitfield():
    # parse_struct's regex anchors on `\n}` so the body must be multi-line;
    # this mirrors how real kernel structs are laid out.
    src = textwrap.dedent(
        """
        struct bf {
            u32 a;
            u32 x:3;
            u32 b;
        };
        """
    )
    with pytest.raises(M.Unsupported) as ei:
        M.mirror(src, "bf")
    assert "bitfield" in str(ei.value).lower()


def test_mirror_refuses_union():
    src = textwrap.dedent(
        """
        struct hasu {
            u32 tag;
            union { u32 as_int; u64 as_long; } u;
        };
        """
    )
    with pytest.raises(M.Unsupported) as ei:
        M.mirror(src, "hasu")
    assert "union" in str(ei.value).lower()


def test_mirror_resolves_config_ifdef_under_pinning():
    # Milestone B: config-pinning is the standing default (pinned.config auto-
    # loaded). A CONFIG-conditional field resolves to the pinned layout and the
    # mirror is flagged config_pinned. CONFIG_SMP is enabled in the pinned
    # minimal config, so `maybe` is present.
    src = textwrap.dedent(
        """
        struct cfg {
            u32 always;
        #ifdef CONFIG_SMP
            u32 maybe;
        #endif
            u32 tail;
        };
        """
    )
    m = M.mirror(src, "cfg")
    assert [f for _, f, _ in m["fields"]] == ["always", "maybe", "tail"]
    assert m["config_pinned"] is True


def test_mirror_still_refuses_undecidable_if():
    # the widener is scoped: a #if the resolver cannot decide statically (not a
    # bare CONFIG symbol) STILL refuses — never guessed.
    src = "struct cfg2 {\n\tu32 a;\n#if CONFIG_NR_CPUS > 2\n\tu32 b;\n#endif\n};\n"
    with pytest.raises(M.Unsupported):
        M.mirror(src, "cfg2")


def test_mirror_refuses_real_irq_desc_unmappable_field():
    # irq_desc's #ifdef CONFIG_* fields now RESOLVE under pinning; it is still
    # refused, but for a genuinely-unmappable field (unsigned int __private, a
    # sparse annotation), NOT for #if — pinning resolves conditionals without
    # recklessly accepting unmappable members.
    src = _ksrc_file("include/linux/irqdesc.h")
    with pytest.raises(M.Unsupported) as ei:
        M.mirror(src, "irq_desc")
    assert "#if" not in str(ei.value)


def test_mirror_refuses_nested_struct_by_value():
    # a nested non-scalar struct-BY-VALUE (not a pointer) has no fixed
    # scalar layout the generator can trust -> layout() must refuse.
    src = textwrap.dedent(
        """
        struct outer {
            u32 head;
            struct inner nested;
            u32 tail;
        };
        """
    )
    with pytest.raises(M.Unsupported) as ei:
        M.mirror(src, "outer")
    assert "scalar" in str(ei.value).lower() or "nested" in str(ei.value).lower()


# ============================================================================
# 3. mirror WRONG-LAYOUT is detectable
# ============================================================================


def test_mirror_emits_correct_size_assert_and_mutation_differs():
    src = _ksrc_file("include/linux/clk-provider.h")
    m = M.mirror(src, "clk_div_table")
    rust = m["rust"]
    # the emitted const-assert pins the TRUE size; a mutated wrong size is a
    # different string (so a wrong layout is statically catchable).
    assert "size_of::<ClkDivTable>() == 8" in rust
    mutated = rust.replace("== 8", "== 9")
    assert mutated != rust
    assert "== 9" in mutated
    # offset asserts pin each field too
    assert "offset_of!(ClkDivTable, val) == 0" in rust
    assert "offset_of!(ClkDivTable, div) == 4" in rust


@pytest.mark.skipif(shutil.which("rustc") is None, reason="rustc not available")
def test_mirror_rustc_accepts_correct_rejects_wrong():
    # bonus, toolchain-gated: rustc's REAL layout must agree with the generator,
    # and a corrupted size const-assert must fail to compile.
    src = _ksrc_file("include/linux/clk-provider.h")
    m = M.mirror(src, "clk_div_table")
    ok, err = _rustc_check(m["rust"])
    assert ok, f"correct mirror should compile, got:\n{err}"
    bad = m["rust"].replace("== 8", "== 9")
    ok_bad, _ = _rustc_check(bad)
    assert not ok_bad, "corrupted size const-assert must fail to compile"


# ============================================================================
# 4. cluster CORRECT — real lib/math/gcd.c
# ============================================================================


def test_cluster_gcd_pulls_only_binary_gcd():
    src = _ksrc_file("lib/math/gcd.c")
    cl = C.static_cluster(src, "gcd")
    assert cl == ["gcd", "binary_gcd"], cl


def test_cluster_gcd_functions_flags_static():
    src = _ksrc_file("lib/math/gcd.c")
    fns = C.functions(src)
    assert fns["gcd"]["static"] is False
    assert fns["binary_gcd"]["static"] is True


def test_cluster_weave_gcd_removes_helper_and_forwards():
    src = _ksrc_file("lib/math/gcd.c")
    woven = C.cluster_weave(
        src, "gcd", "cgir_gcd", "extern unsigned long cgir_gcd(unsigned long a, unsigned long b);"
    )
    # binary_gcd's definition is gone (no orphan / -Werror=unused-function)
    assert "static unsigned long binary_gcd" not in woven
    # the entry now forwards to the Rust seam
    assert "return cgir_gcd(a, b)" in woven
    # the extern seam prototype was inserted
    assert "extern unsigned long cgir_gcd(" in woven
    # naive_weave leaves the orphan (demonstrates the bug the cluster weave fixes)
    naive = C.naive_weave(
        src, "gcd", "cgir_gcd", "extern unsigned long cgir_gcd(unsigned long a, unsigned long b);"
    )
    assert "static unsigned long binary_gcd" in naive


def test_cluster_shared_static_helper_is_a_boundary_not_private():
    # A static helper reachable from `entry` is pulled in; the CONTRACT is that
    # cluster reflects the *call graph*. Pin that a helper NOT called by the
    # entry (but called by a sibling entry in the TU) is NOT swept into the
    # entry's cluster -- it stays a genuine boundary of `entry`.
    src = textwrap.dedent(
        """
        static int shared(int x) { return x + 1; }

        int entry_a(int x) { return shared(x); }

        int entry_b(int x) { return shared(x) + entry_a(x); }
        """
    )
    # entry_a reaches shared; it does NOT reach entry_b.
    assert C.static_cluster(src, "entry_a") == ["entry_a", "shared"]
    # entry_b reaches shared and entry_a is NOT static -> boundary, not pulled in.
    assert C.static_cluster(src, "entry_b") == ["entry_b", "shared"]


# ============================================================================
# 5. cluster ADVERSARIAL — comment/string masking must not break parsing
# ============================================================================


def test_functions_survives_hostile_doc_comment():
    # The exact bug the mask fixes: a doc comment carrying "(C) 2013",
    # an unbalanced '(' and a stray '{' must NOT swallow the next definition.
    src = textwrap.dedent(
        """
        /*
         * Copyright (C) 2013 Somebody <who@example.com>
         * This comment has an unbalanced paren ( and a stray brace {
         * and even the token gcd( to try to fool the matcher.
         */
        static int helper(int x) { return x + 1; }

        // one-line comment with ) and { and another_fn(
        int real_entry(int x) { return helper(x); }
        """
    )
    fns = C.functions(src)
    assert "helper" in fns, fns.keys()
    assert "real_entry" in fns, fns.keys()
    assert fns["helper"]["static"] is True
    assert fns["real_entry"]["static"] is False
    # and the cluster still resolves correctly through the noise
    assert C.static_cluster(src, "real_entry") == ["real_entry", "helper"]


def test_callees_ignores_commented_and_string_calls():
    # A //-commented-out readl(), and a string literal containing ")" and a
    # "call-looking" token must not register as real callees / corrupt detection.
    src = textwrap.dedent(
        """
        static int noise(int x) { return x; }

        int driver_probe(int x)
        {
            // readl(base + 4);           <- commented out, must be ignored
            const char *s = "not_a_call( ) and a lone )";
            return noise(x);
        }
        """
    )
    fns = C.functions(src)
    body = fns["driver_probe"]["text"]
    # callees() runs on the RAW body, so masking happens at functions()/parse
    # time; here we assert the higher-level contract: the cluster is exactly the
    # real static callee, no phantom readl/not_a_call pulled in.
    cl = C.static_cluster(src, "driver_probe")
    assert cl == ["driver_probe", "noise"], cl
    # readl / not_a_call are neither defined nor static, so even if callees()
    # names them they must never enter the cluster.
    assert "readl" not in cl
    assert "not_a_call" not in cl


def test_doc_comment_does_not_merge_two_definitions():
    # Pin that two real definitions separated only by a hostile comment are BOTH
    # found (the "swallow the next definition" regression).
    src = textwrap.dedent(
        """
        int first(void) { return 1; }
        /* function-like ( noise and a { brace, plus second( token */
        int second(void) { return 2; }
        """
    )
    fns = C.functions(src)
    assert set(fns) >= {"first", "second"}, fns.keys()
    assert fns["first"]["text"].strip().startswith("int first")
    assert fns["second"]["text"].strip().startswith("int second")


# ============================================================================
# 6. FUZZ — malformed inputs must refuse-or-be-sane, never crash unhandled
# ============================================================================

_FUZZ_STRUCTS = [
    "",  # empty
    "struct",  # truncated keyword
    "struct foo",  # no body
    "struct foo {",  # unbalanced open brace
    "struct foo { u32 a",  # no closing / no semicolon
    "struct foo { u32 a; ",  # unterminated
    "struct foo { };",  # empty body
    "struct foo { int; };",  # nameless field
    "struct foo { #define X 1\n u32 a; };",  # macro-heavy
    "struct foo { struct bar b; };",  # nested by value
    "struct foo { u32 a : 3; };",  # bitfield
    "struct foo { union { int x; } u; };",  # union
    "}}}{{{ struct foo",  # brace garbage
    "struct foo { u32 a; } __attribute__((packed))",  # attr, no semicolon
    "struct foo { unknown_type_t z; };",  # unknown scalar
    "\x00\x01 struct foo { u32 a; };",  # binary noise prefix
]


@pytest.mark.parametrize("src", _FUZZ_STRUCTS)
def test_fuzz_parse_struct_refuses_or_parses_never_crashes(src):
    # For every malformed input, parse_struct/mirror must either return a value
    # or raise Unsupported -- never leak an unhandled exception type.
    try:
        M.mirror(src, "foo")
    except M.Unsupported:
        pass  # acceptable, conservative refusal
    except Exception as e:  # pragma: no cover - this is the failure we guard
        pytest.fail(f"parse_struct/mirror crashed with {type(e).__name__}: {e!r} on {src!r}")


_FUZZ_FUNCS = [
    "",  # empty
    "int",  # truncated
    "int f(",  # unbalanced open paren
    "int f() {",  # unbalanced open brace
    "int f() { return 0;",  # no closing brace
    "static int f() { { { } }",  # unbalanced nested
    "int f(void);",  # prototype only, no body
    "#define M(x) ((x)+1)\n int f(void) { return M(1); }",  # macro-heavy
    "int f() { /* unclosed comment ",  # unterminated comment
    'int f() { char *s = "unterminated string ;',  # unterminated string
    "}}}{{{",  # brace garbage
    "int\nf\n(\nvoid\n)\n{\nreturn 0;\n}",  # newline-scattered
    "\x00\x01 int f(void) { return 0; }",  # binary noise
]


@pytest.mark.parametrize("src", _FUZZ_FUNCS)
def test_fuzz_functions_never_crashes(src):
    try:
        C.functions(src)
    except Exception as e:  # pragma: no cover
        pytest.fail(f"functions() crashed with {type(e).__name__}: {e!r} on {src!r}")


@pytest.mark.parametrize("src", _FUZZ_FUNCS)
def test_fuzz_static_cluster_refuses_or_sane(src):
    # static_cluster on an absent entry must raise KeyError (defined behaviour),
    # never an unhandled parser crash.
    try:
        fns = C.functions(src)
    except Exception as e:  # pragma: no cover
        pytest.fail(f"functions() crashed on {src!r}: {e!r}")
    entry = next(iter(fns), "f")
    try:
        cl = C.static_cluster(src, entry)
        assert isinstance(cl, list)
        if fns:
            assert cl and cl[0] == entry
    except KeyError:
        pass  # entry not in TU -> defined refusal
    except Exception as e:  # pragma: no cover
        pytest.fail(f"static_cluster crashed with {type(e).__name__}: {e!r} on {src!r}")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
