"""Pin the config-aware #if resolution in mirror.py (the mirror factory's
core widener). Struct bodies with CONFIG_* conditionals were refused wholesale
("config-dependent (#if) fields — layout not fixed") — the largest single
struct-resolution wall (12/78 readers in Run 1 alone). Under a PINNED config
the layout IS fixed: strip_config_ifs evaluates simple CONFIG conditionals
against the pinned set; anything it cannot decide statically stays REFUSED
(the #if backstop in parse_struct is unchanged). Mirrors produced this way
carry config_pinned=True — the claim is scoped to the pinned config.
"""
import importlib.util
import os

import pytest

_spec = importlib.util.spec_from_file_location(
    "mirror_cfg_t", os.path.join(os.path.dirname(__file__), "..", "mirror", "mirror.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

CFG = {"CONFIG_SMP", "CONFIG_LOCKDEP"}

BODY = """\tint a;
#ifdef CONFIG_SMP
\tint on_smp;
#endif
#ifdef CONFIG_NUMA
\tint on_numa;
#else
\tint no_numa;
#endif
#ifndef CONFIG_NUMA
\tint also_no_numa;
#endif
\tint z;
"""


def test_strip_keeps_enabled_drops_disabled():
    out = M.strip_config_ifs(BODY, CFG)
    assert "on_smp" in out and "on_numa" not in out
    assert "no_numa" in out and "also_no_numa" in out
    assert "#if" not in out and "#endif" not in out


def test_nested_blocks():
    body = "#ifdef CONFIG_SMP\nint x;\n#ifdef CONFIG_NUMA\nint y;\n#endif\nint w;\n#endif\nint z;\n"
    out = M.strip_config_ifs(body, CFG)
    assert "x" in out and "w" in out and "z" in out and "int y;" not in out


def test_is_enabled_and_defined_forms():
    body = ("#if defined(CONFIG_SMP)\nint a;\n#endif\n"
            "#if IS_ENABLED(CONFIG_LOCKDEP)\nint b;\n#endif\n")
    out = M.strip_config_ifs(body, CFG)
    assert "int a;" in out and "int b;" in out


def test_complex_expression_refused():
    with pytest.raises(M.Unsupported):
        M.strip_config_ifs("#if CONFIG_NR_CPUS > 2\nint a;\n#endif\n", CFG)
    with pytest.raises(M.Unsupported):
        M.strip_config_ifs("#if defined(CONFIG_A) && defined(CONFIG_B)\nint a;\n#endif\n", CFG)


def test_parse_struct_with_config_loaded():
    src = "struct cfgt {\n\tint a;\n#ifdef CONFIG_SMP\n\tint b;\n#endif\n\tint c;\n};\n"
    # without a config: refused as before
    M.set_pinned_config(None)
    with pytest.raises(M.Unsupported):
        M.parse_struct(src, "cfgt")
    # with the pinned config: resolved, and the mirror is flagged
    M.set_pinned_config(CFG)
    try:
        fields = [f for _, f, _ in M.parse_struct(src, "cfgt")]
        assert fields == ["a", "b", "c"]
        m = M.mirror(src, "cfgt")
        assert m.get("config_pinned") is True
    finally:
        M.set_pinned_config(None)


def test_unpinned_mirror_not_flagged():
    src = "struct plaint {\n\tint a;\n\tint b;\n};\n"
    m = M.mirror(src, "plaint")
    assert not m.get("config_pinned")
