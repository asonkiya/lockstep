"""Invariant-4 restoration (Run 1 breach, RUN1-REPORT.md): checkpoint keys
must be FILE-QUALIFIED so same-named fns in different files each get a
verdict. Run 1's efftrace worklist carried cache_contiguous in two files; the
bare-name key made the second silently skip after the first solved — a silent
drop the pre-reg grades as run-failing. This pins the fix on the exact pair.
"""
import importlib.util
import json
import os

import pytest

_HERE = os.path.dirname(__file__)

_O = None
try:
    _spec = importlib.util.spec_from_file_location(
        "overnight_keys_t", os.path.join(_HERE, "..", "firstrun", "overnight.py"))
    _O = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_O)
except Exception:
    _O = None

pytestmark = pytest.mark.skipif(_O is None, reason="overnight.py deps unavailable")


def test_same_fn_two_files_distinct_keys():
    k1 = _O._key("efftrace", "mm/foo.c", "cache_contiguous")
    k2 = _O._key("efftrace", "fs/bar.c", "cache_contiguous")
    assert k1 != k2
    # a done-set holding one must not skip the other
    done = {k1}
    assert k2 not in done


def test_run1_collision_pair_now_distinct():
    wl = json.load(open(os.path.join(_HERE, "..", "efftrace", "reach_accepted.json")))
    dupes = {}
    for it in wl:
        dupes.setdefault(it["fn"], []).append(it["file"])
    multi = {fn: fs for fn, fs in dupes.items() if len(fs) > 1}
    assert "cache_contiguous" in multi, "worklist no longer carries the Run-1 pair"
    keys = {_O._key("efftrace", f, fn) for fn, fs in multi.items() for f in fs}
    n_pairs = sum(len(fs) for fs in multi.values())
    assert len(keys) == n_pairs, "file-qualified keys must be unique per (file, fn)"


def test_key_is_valid_filename():
    # keys become verified/<key>.rs filenames — no path separators allowed
    k = _O._key("reader", "net/ipv4/tcp.c", "fn_x")
    assert "/" not in k and k.startswith("reader_")
