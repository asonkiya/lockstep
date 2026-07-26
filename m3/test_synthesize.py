"""Red-phase tests for the M3 driver's deterministic parts (no network):
prompt construction, candidate extraction, abstraction-selection parsing, and
the scaffold sabotage used as the negative control.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from synthesize import (  # noqa: E402
    CATALOG,
    build_prompt,
    parse_candidate,
    sabotage_scaffold,
)

_IR = {
    "structs": {"ring": {"locks": {"lock": "spinlock_t"}, "fields": {}}},
    "regions": [{"function": "ring_push", "base": "r", "lock_field": "lock"}],
    "protects": {"ring": {"lock": ["buf", "count", "head"]}},
    "unprotected_accesses": [],
}


def test_prompt_carries_ir_catalog_and_scaffold_api():
    p = build_prompt("struct ring { ... };", _IR)
    # the three inputs the model synthesizes from (design.md §3.2)
    assert "spin_lock" in p or "spinlock_t" in p  # the C idiom
    assert "SpinLock<Fields>" in p  # the R4L catalog row
    assert '"protects"' in p or "protects" in p  # the extracted IR
    # the scaffold contract the candidate must compile against
    assert "with_mut" in p and "Guard" in p
    # selection must be declared machine-checkably
    assert "abstraction:" in p


def test_catalog_is_the_design_table():
    idioms = [row[0] for row in CATALOG]
    assert any("spin_lock" in i for i in idioms)
    assert any("rcu" in i.lower() for i in idioms)
    assert any("kref" in i or "refcount" in i for i in idioms)


def test_parse_candidate_strips_fences_and_reads_selection():
    raw = (
        "```rust\n"
        "// abstraction: SpinLock<T>\n"
        "pub struct RingFields { head: usize }\n"
        "```\n"
    )
    sel, code = parse_candidate(raw)
    assert sel == "SpinLock<T>"
    assert "```" not in code
    assert code.startswith("// abstraction")


def test_parse_candidate_no_fences_passthrough():
    raw = "// abstraction: Rcu<T>\npub struct X;"
    sel, code = parse_candidate(raw)
    assert sel == "Rcu<T>"
    assert code == raw


def test_sabotage_removes_acquisition_only():
    scaffold = (
        "pub fn lock(&self) {\n"
        "// SABOTAGE-BEGIN\n"
        "while self.locked.compare_exchange(false, true).is_err() {}\n"
        "// SABOTAGE-END\n"
        "}\n"
    )
    out = sabotage_scaffold(scaffold)
    assert "compare_exchange" not in out  # the lock no longer locks
    assert "pub fn lock" in out  # but the API surface is intact
    assert out.count("{") == out.count("}")  # still balanced -> still compiles
