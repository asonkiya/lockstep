"""Gate the GPIO driver-family trace oracle (dream/family/gpio_family.py): each
driver's Rust transplant must trace-match the C reference (DIFF_PASS) and a
one-line wrong-register mutation must be caught (DIFF_FAIL), across two register
idioms (RMW-DATA, SET/CLR) through one generic op-driver. Boot-free.

Skipped without host cc/rustc.
"""
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "family"))

_G = None
try:
    import gpio_family as _G  # noqa: E402
except Exception:
    _G = None

pytestmark = pytest.mark.skipif(
    not (shutil.which("cc") and shutil.which("rustc") and _G),
    reason="needs host cc + rustc",
)


@pytest.mark.parametrize("name", ["gpio-zevio", "gpio-mmio(bgpio-core)",
                                  "mxs-alias(set/clr@+4/+8)"])
def test_driver_closes_and_wrong_register_caught(name):
    with tempfile.TemporaryDirectory() as tmp:
        vok, ook = _G.close(name, wrong=False, workdir=os.path.join(tmp, "ok"))
        vbad, obad = _G.close(name, wrong=True, workdir=os.path.join(tmp, "bad"))
    assert vok == "DIFF_PASS", f"{name} correct transplant: {vok}\n{ook}"
    assert vbad == "DIFF_FAIL", f"{name} wrong-register not caught: {vbad}\n{obad}"


def test_coverage_gate_is_nonvacuous():
    # only the set-0 arm is reachable with a single pin -> must REFUSE, not pass.
    covcfg = dict(_G.DRIVERS["gpio-zevio"], npins=1)
    _G.DRIVERS["_covtest"] = covcfg
    try:
        with tempfile.TemporaryDirectory() as tmp:
            v, out = _G.close("_covtest", wrong=False, workdir=tmp)
        assert v == "REFUSE", f"coverage gate vacuous: {v}\n{out}"
    finally:
        _G.DRIVERS.pop("_covtest", None)


def test_two_idioms_cover_three_drivers():
    idioms = {c["idiom"] for n, c in _G.DRIVERS.items() if not n.startswith("_")}
    assert idioms == {"rmw_data", "set_clr"}
    assert len([n for n in _G.DRIVERS if not n.startswith("_")]) == 3
