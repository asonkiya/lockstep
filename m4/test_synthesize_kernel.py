"""Unit tests for the M4 kernel-synthesis driver's deterministic parts."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from synthesize_kernel import PRELUDE, build_kernel_prompt, sabotage  # noqa: E402

_IR = {
    "structs": {"ring_fields": {"locks": {}, "fields": {}}},
    "regions": [],
    "protects": {},
    "unprotected_accesses": [],
}


def test_prelude_has_the_kernel_seam_and_markers():
    assert "_raw_spin_lock" in PRELUDE and "_raw_spin_unlock" in PRELUDE
    assert "SABOTAGE-BEGIN" in PRELUDE and "SABOTAGE-DROP-BEGIN" in PRELUDE
    assert "#[repr(C)]" in PRELUDE and "no_std" in PRELUDE


def test_kernel_prompt_carries_all_inputs():
    p = build_kernel_prompt("void lockstep_ring_push(...) { spin_lock(l); }", _IR)
    assert "SpinLock<Fields>" in p          # catalog
    assert "lockstep_ring_push" in p        # the exact export
    assert "get_unchecked_mut" in p         # no-panic rule
    assert "KCSAN" in p                     # stakes stated
    assert "abstraction:" in p              # machine-checkable selection


def test_sabotage_drops_both_lock_and_unlock():
    src = PRELUDE + "\n// abstraction: x\nfn body() {}\n"
    bad = sabotage(src)
    assert "_raw_spin_lock(lock)" not in bad      # acquisition gone
    assert "_raw_spin_unlock(self.lock)" not in bad  # release gone
    assert "extern" in bad and "fn body" in bad   # externs + region intact
    assert bad.count("{") == bad.count("}")       # still parses
    assert "SABOTAGE-BEGIN" not in bad
