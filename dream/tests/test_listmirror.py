"""The list_head mirror + faithful ops + chain-walking gate (listmirror.py).

Step 2 of the containers build. Pins:
  * the mirror's layout claim matches the IN-KERNEL probe (and the poison
    values follow the config's POISON_POINTER_DELTA, not the header literal);
  * a faithful transcription of list.h MATCHes the real inlines structurally;
  * the negative controls DIVERGE (corrupted chain / missing poison / wrong
    insertion side) — the gate is load-bearing, not decorative;
  * the structural oracle is STRICTLY STRONGER than an ADT-sequence oracle:
    the forward-chain-only view MISSES unlink-without-poison.
Needs cc + rustc + docker (in-kernel probe); skipped otherwise.
"""
import importlib.util
import os
import shutil

import pytest

_HERE = os.path.dirname(__file__)
_M = None
try:
    _spec = importlib.util.spec_from_file_location(
        "listmirror_t", os.path.join(_HERE, "..", "container_adt", "listmirror.py"))
    _M = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_M)
except Exception:
    _M = None

pytestmark = pytest.mark.skipif(
    not (_M and shutil.which("cc") and shutil.which("rustc") and shutil.which("docker")),
    reason="needs cc + rustc + docker for the in-kernel layout probe")


@pytest.fixture(scope="module")
def layout():
    return _M.probe_layout()


def test_probed_layout_is_the_real_one(layout):
    # LP64 list_head: two pointers, next first
    assert layout["list_head_size"] == 16
    assert layout["next_off"] == 0 and layout["prev_off"] == 8
    # poison is CONFIG-dependent — must be delta-adjusted, never the bare header
    # literal (arm64 defconfig sets CONFIG_ILLEGAL_POINTER_VALUE)
    assert layout["poison1"] == (0x100 + layout["poison_delta"]) & 0xFFFFFFFFFFFFFFFF
    assert layout["poison2"] == (0x122 + layout["poison_delta"]) & 0xFFFFFFFFFFFFFFFF
    if layout["poison_delta"]:
        assert layout["poison1"] != 0x100, "delta not applied — would mis-compare"


def test_emitted_mirror_pins_its_layout(layout):
    src = _M.emit_mirror(layout)
    assert "#[repr(C)]" in src and "pub next: *mut ListHead" in src
    # const-asserts, so a layout drift fails the COMPILE
    assert f"size_of::<ListHead>() == {layout['list_head_size']}" in src
    assert "offset_of!(ListHead, next) == 0" in src
    # write ORDER of __list_add matches list.h: next->prev, new->next, new->prev, prev->next
    body = src.split("pub unsafe fn __list_add")[1].split("}")[0]
    order = [ln.strip() for ln in body.splitlines() if "=" in ln]
    assert order[0].startswith("(*next).prev") and order[-1].startswith("(*prev).next")


def test_faithful_transcription_matches(layout):
    v, out, d = _M.run_diff(layout, "correct")
    assert v == "MATCH", (v, out[-300:])


@pytest.mark.parametrize("variant", ["forward_only", "no_poison", "add_wrong_side"])
def test_negative_controls_diverge(layout, variant):
    v, out, d = _M.run_diff(layout, variant)
    assert v == "DIVERGE", (variant, v, out[-300:])


def test_structural_oracle_is_strictly_stronger_than_adt(layout):
    # the ADT view (forward chain only) MUST miss unlink-without-poison, while
    # the structural view catches it — this is the whole justification for
    # building the chain-walking oracle instead of reusing the ADT one.
    adt, _, _ = _M.run_diff(layout, "no_poison", adt_only=True)
    full, _, _ = _M.run_diff(layout, "no_poison", adt_only=False)
    assert adt == "MATCH", "expected the ADT view to be blind to poison state"
    assert full == "DIVERGE"
    # and both agree on a faithful translation
    assert _M.run_diff(layout, "correct", adt_only=True)[0] == "MATCH"
