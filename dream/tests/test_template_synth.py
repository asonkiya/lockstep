"""Template synthesis ($0, no model) must produce candidates that trace-match the
REAL driver C reference — the cheapest rung of the synth ladder, gate-arbitrated.

For each SET/CLR bgpio-family driver, render the Rust candidate deterministically
from (idiom, offsets, dir_mode) and verify it DIFF_PASSes against the real C ref
via the GPIO family trace oracle. A wrong offset table must DIFF_FAIL (the gate
still arbitrates, so template synth is sound). Boot-free; skipped without cc/rustc.
"""
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "family"))

_G = _T = None
try:
    import gpio_family as _G  # noqa: E402
    import template_synth as _T  # noqa: E402
except Exception:
    pass

pytestmark = pytest.mark.skipif(
    not (shutil.which("cc") and shutil.which("rustc") and _G and _T),
    reason="needs host cc + rustc",
)


@pytest.mark.parametrize("driver", ["gpio-mmio(bgpio-core)", "mxs-alias(set/clr@+4/+8)"])
def test_template_candidate_closes(driver):
    cand = _T.synth_for(driver)                 # deterministic, $0, no model
    with tempfile.TemporaryDirectory() as tmp:
        v, out = _G.close(driver, wrong=False, workdir=tmp, cand_override=cand)
    assert v == "DIFF_PASS", f"template candidate for {driver}: {v}\n{out}"


def test_template_wrong_offsets_caught():
    # a mis-templated candidate (swapped set/clr offsets) must be REJECTED —
    # the gate still arbitrates, so template synth cannot introduce a false pass.
    bad = _T.synth("set_clr", {"dat": 0x00, "set": 0x14, "clr": 0x10, "dir": 0x08},
                   dir_mode="shadow")
    with tempfile.TemporaryDirectory() as tmp:
        v, out = _G.close("gpio-mmio(bgpio-core)", wrong=False, workdir=tmp, cand_override=bad)
    assert v == "DIFF_FAIL", f"wrong offsets not caught: {v}\n{out}"


def test_no_template_for_custom_idiom():
    with pytest.raises(ValueError):
        _T.synth("rmw_data", {"dat": 0, "set": 0, "clr": 0, "dir": 0})
