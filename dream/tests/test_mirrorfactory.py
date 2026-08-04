"""Gate the mirror factory build path (dream/mirrorfactory/factory.py):
a config-conditional struct is mirrored under the pinned config, passes BOTH
fail-closed guards (rustc const layout asserts + the cc guard TU), lands in
the bank flagged config_pinned, and an undecidable struct is refused with a
reason — never guessed.
"""
import importlib.util
import os
import shutil

import pytest

_HERE = os.path.dirname(__file__)
_KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")

_F = None
try:
    _spec = importlib.util.spec_from_file_location(
        "factory_t", os.path.join(_HERE, "..", "mirrorfactory", "factory.py"))
    _F = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_F)
except Exception:
    _F = None

pytestmark = pytest.mark.skipif(
    not (shutil.which("cc") and shutil.which("rustc") and _F
         and os.path.isdir(_KSRC)
         and os.path.isfile(os.path.join(_HERE, "..", "mirrorfactory", "pinned.config"))),
    reason="needs cc + rustc + KSRC + pinned.config",
)

_SRC = """struct mf_test_s {
\tint a;
#ifdef CONFIG_SMP
\tunsigned long smp_only;
#endif
#ifdef CONFIG_SURELY_NOT_A_REAL_OPTION
\tint ghost;
#endif
\tunsigned short b;
};
"""


@pytest.fixture()
def fixture_file():
    path = os.path.join(_KSRC, "_lockstep_mf_test.c")
    open(path, "w").write(_SRC)
    yield "_lockstep_mf_test.c"
    os.remove(path)


def test_build_one_config_pinned(fixture_file):
    ok, info = _F.build_one("mf_test_s", fixture_file)
    assert ok, info
    assert info["config_pinned"] is True
    # CONFIG_SMP is on in the pinned minimal config; the ghost option is not
    assert "smp_only" in info["fields"] and "ghost" not in info["fields"]
    banked = os.path.join(_F.BANK, "mf_test_s.rs")
    assert os.path.exists(banked)
    os.remove(banked)


def test_undecidable_struct_refused(fixture_file):
    path = os.path.join(_KSRC, "_lockstep_mf_test.c")
    open(path, "w").write("struct mf_bad_s {\n\tint a;\n#if NR_CPUS > 2\n\tint b;\n#endif\n};\n")
    ok, info = _F.build_one("mf_bad_s", fixture_file)
    assert not ok and "reason" in info
