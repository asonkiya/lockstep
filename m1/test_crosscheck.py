"""The M1 proof as a gated test: the static protects map matches TSan's runtime
race observation on ringbuf.c. Skips cleanly where clang/ThreadSanitizer is
absent (CI without the toolchain), runs the real cross-check where present.
"""

import shutil
import subprocess

import pytest

import crosscheck


def _tsan_available() -> bool:
    if not shutil.which("clang"):
        return False
    r = subprocess.run(
        ["clang", "-fsanitize=thread", "-x", "c", "-", "-o", "/dev/null"],
        input="int main(){return 0;}",
        text=True,
        capture_output=True,
    )
    return r.returncode == 0


pytestmark = pytest.mark.skipif(
    not _tsan_available(), reason="clang with ThreadSanitizer not available"
)


def test_static_map_matches_runtime():
    """protected fields never race; every raced field was flagged unprotected."""
    src = open(crosscheck.SUBJECT).read()
    ir = crosscheck.extract(src)
    protected = {f for d in ir["protects"].values() for fs in d.values() for f in fs}
    flagged = {u["field"] for u in ir["unprotected_accesses"]}

    raced = crosscheck.raced_fields(crosscheck.run_tsan(), src.splitlines())

    assert raced, "harness did not exercise the data race"
    assert not (raced & protected), f"map called protected but raced: {raced & protected}"
    assert not (raced - flagged), f"raced but never flagged: {raced - flagged}"
    # the deliberate bug specifically
    assert "name" in raced and "name" in flagged
    assert {"head", "count", "buf"} <= protected
