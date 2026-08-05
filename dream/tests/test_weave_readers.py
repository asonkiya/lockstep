"""Non-leaf weave: the readers-weave mechanism (dream/ratchet/weave_readers.py).

A structdiff-verified reader candidate operates on a #[repr(C)] mirror whose
layout is gate-verified == the real kernel struct, so it weaves directly: the
freestanding Rust object links against the real-ABI struct pointer, and an
in-tree _Static_assert re-certifies the layout at kernel build. Pins:
  * a verified reader's artifacts host-compile BOTH ways (rust obj + C seam);
  * the seam call carries the real C params;
  * the layout guard is LOAD-BEARING — a wrong offset fails the C compile.
Needs cc + rustc + $KSRC + the verified candidate; skipped otherwise.
"""
import importlib.util
import os
import re
import shutil
import subprocess
import tempfile

import pytest

_HERE = os.path.dirname(__file__)
_KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
_CAND = os.path.join(_HERE, "..", "firstrun", "verified",
                     "reader_kernel__resource.c_resource_clip.rs")

_W = None
try:
    _spec = importlib.util.spec_from_file_location(
        "weave_readers_t", os.path.join(_HERE, "..", "ratchet", "weave_readers.py"))
    _W = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_W)
except Exception:
    _W = None

pytestmark = pytest.mark.skipif(
    not (shutil.which("cc") and shutil.which("rustc") and _W
         and os.path.isdir(_KSRC) and os.path.exists(_CAND)),
    reason="needs cc + rustc + $KSRC + the resource_clip verified candidate")


def test_artifacts_build_and_seam_shape():
    a = _W.build_artifacts("kernel/resource.c", "resource_clip")
    assert a["struct"] == "resource"
    assert a["seam"] == "resource_clip_rs"
    # the seam call passes the real C param names, in order
    assert a["argnames"][0] in a["seam_body"]
    # freestanding preamble present (no_std + panic handler)
    assert "#![no_std]" in a["rust_obj"] and "panic_handler" in a["rust_obj"]
    # a size + per-field offset re-cert
    assert a["guard"].count("_Static_assert") == len(a["fields"]) + 1


def test_host_proof_passes():
    assert _W.prove("kernel/resource.c", "resource_clip") == 0


def test_layout_guard_is_load_bearing():
    # corrupt ONE offset in the generated guard -> the C compile must FAIL,
    # proving the in-tree _Static_assert catches a layout drift (not decorative).
    a = _W.build_artifacts("kernel/resource.c", "resource_clip")
    bad_guard = re.sub(r"offsetof\(struct resource, end\) == \d+",
                       "offsetof(struct resource, end) == 999", a["guard"], count=1)
    assert bad_guard != a["guard"], "expected an 'end' offset assert to corrupt"
    with tempfile.TemporaryDirectory() as d:
        prog = ("#include <stddef.h>\n"
                "struct resource { unsigned long long start, end; void *name;\n"
                "  unsigned long long flags, desc; void *parent, *sibling, *child; };\n"
                + bad_guard + "\n")
        open(os.path.join(d, "g.c"), "w").write(prog)
        r = subprocess.run(["cc", "-std=c11", "-c", os.path.join(d, "g.c"),
                            "-o", os.path.join(d, "g.o")], capture_output=True, text=True)
        assert r.returncode != 0, "a wrong offset must fail the guard compile"
