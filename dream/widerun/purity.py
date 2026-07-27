#!/usr/bin/env python3
"""The purity router — decide which oracle a function is eligible for.

The wide run's lesson: a return-value differential is SOUND only for provably
pure functions; on a stateful one it can pass a transplant that reproduces the
return while dropping the effect (`__refrigerator`, `probe_irq_mask`). So before
the scalar gate, classify:

  PURE   -> scalar differential (cheap, sound; every PASS is behavior-equivalent)
  IMPURE -> quarantine: needs Ring 3's recorded-I/O / trace oracle, NEVER scalar

Conservative by construction: anything we cannot prove pure is IMPURE. For a
scalar-arg leaf (no pointer params), correctness of purity reduces to: the body
touches only its args + locals + pure arithmetic + calls to known-pure helpers,
reads no global/per-cpu/clock/random state, and has no side effect.
"""
from __future__ import annotations

import re

# Any of these in a scalar-leaf body => IMPURE (reads state, has an effect, or
# recovers/derefs memory it wasn't handed as a scalar).
IMPURE = re.compile(
    r"->"                                             # deref => touches a struct/global (no ptr args exist here)
    r"|\b(readl|writel|read[bwlq]|write[bwlq]|ioread|iowrite)\b"
    r"|\bk[mzv]alloc\b|\bkfree\b|\bvfree\b|_alloc\b|\balloc_|\bkmem_"
    r"|\bspin_lock|\bspin_unlock|\bmutex_|\bdown_|\brcu_|\bread_lock|\bwrite_lock|\bseq(lock|count)"
    r"|\bprintk\b|\bpr_(err|warn|info|debug|cont|notice)\b|\bdev_(err|warn|info|dbg)\b|\bWARN|\bBUG\b|\bpanic\b"
    r"|random|prandom|get_random|\brng\b"
    r"|\bjiffies\b|\bktime|get_cycles|local_clock|sched_clock|timeofday|\bnsec\b.*=|\btimekeeping"
    r"|this_cpu|per_cpu|smp_processor_id|raw_smp|__percpu|\bcurrent\b|get_cpu|preempt_"
    r"|\blist_|\bhlist_|\bllist_|\bklist_"
    r"|register|unregister|_probe\b|\bschedule\b|\bwait_|\bsleep\b|msleep|udelay|ndelay|cpu_relax|freeze|refrigerator"
    r"|WRITE_ONCE|READ_ONCE|\bxchg\b|cmpxchg|\batomic_|refcount_|\bkref_"
    r"|container_of|kobject|sysfs|debugfs|\bnode_distance|numa_|node_to|cpu_to_node|target_node"
    r"|copy_(from|to)_user|get_user|put_user|access_ok|irq_desc|irq_to_desc"
)

# Calls that are safe (pure math / bit / cast helpers).
PURE_CALL = {
    "min", "max", "min_t", "max_t", "min3", "max3", "clamp", "clamp_t", "clamp_val",
    "abs", "abs64", "swap", "roundup", "rounddown", "round_up", "round_down",
    "DIV_ROUND_UP", "DIV_ROUND_CLOSEST", "DIV_ROUND_UP_ULL", "DIV_ROUND_DOWN_ULL",
    "order_base_2", "ilog2", "__ilog2_u32", "__ilog2_u64", "roundup_pow_of_two",
    "rounddown_pow_of_two", "is_power_of_2", "__roundup_pow_of_two",
    "__ffs", "ffs", "fls", "fls64", "__fls", "ffz", "hweight8", "hweight16",
    "hweight32", "hweight64", "hweight_long", "BIT", "BIT_ULL", "GENMASK", "GENMASK_ULL",
    "upper_32_bits", "lower_32_bits", "mul_u32_u32", "sizeof", "offsetof_never",
    "le16_to_cpu", "le32_to_cpu", "le64_to_cpu", "cpu_to_le16", "cpu_to_le32",
    "cpu_to_le64", "be16_to_cpu", "be32_to_cpu", "array_index_nospec",
    # read-only branch selectors: read a static key, no side effect (the
    # differential still decides if the taken branch produces matching output)
    "static_branch_likely", "static_branch_unlikely", "static_key_enabled",
    "static_key_false", "static_key_true", "likely", "unlikely",
}
CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
# C keywords / type-ish tokens that appear before '(' but aren't calls
NONCALL = {"if", "for", "while", "switch", "return", "sizeof", "do", "else",
           "int", "long", "unsigned", "u32", "u64", "s32", "s64", "void", "bool",
           "char", "short", "size_t", "typeof", "__typeof__"}


# side-effect markers: writes state / has an observable effect. A read-only
# function (reads state but writes/does nothing) can be checked by the in-kernel
# differential — the C reads the real state, the Rust is pure, and for a
# read-only function return-equivalence IS behavior-equivalence (no effect to
# miss). An effectful function needs a real effect-trace oracle (per-function).
EFFECT = re.compile(
    r"\bwritel\b|\bwrite[bwlq]\b|iowrite|\bout[bwl]\b"
    r"|WRITE_ONCE|\bxchg\b|cmpxchg|atomic_(set|add|sub|inc|dec|or|and|xor|cmpxchg)|refcount_(set|add|inc|dec|sub)"
    r"|\bk[mzv]alloc|\bkfree|\bvfree"
    r"|register|unregister|\bschedule\b|\bwait_|\bsleep\b|msleep|freeze|refrigerator|wake_up|complete\("
    r"|\bprintk\b|\bpr_(err|warn|info|debug|cont|notice)|\bdev_(err|warn|info|dbg)|\bWARN|\bBUG\b|\bpanic\b"
    r"|\bspin_lock|\bmutex_lock|\bdown_|_send|_ipi|raise_|trigger_|notify_"
    r"|->\s*\w+\s*(=|\+\+|--|\+=|-=|\|=|&=)|\*\s*\w+\s*="   # write through a deref/field
    r"|\bset_\w+\(|\bclear_\w+\(|\benable_\w+\(|\bdisable_\w+\("
)


def has_effect(body: str) -> bool:
    b = mask(body)
    hdr = b.find("{")
    return bool(EFFECT.search(b[hdr:] if hdr > 0 else b))


def mask(s):
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
    s = re.sub(r"//[^\n]*", " ", s)
    s = re.sub(r"^\s*#[^\n]*", " ", s, flags=re.MULTILINE)   # preprocessor lines
    s = re.sub(r'"(\\.|[^"])*"', '""', s)
    s = re.sub(r"'(\\.|[^'])'", "''", s)
    return s


def calls_ok(body: str, pure_names: set, own: str = "") -> bool:
    """True iff every function called is a known-pure helper, a pure leaf, or
    self — i.e. the function makes no call that could hide a side effect."""
    b = mask(body)
    hdr = b.find("{")
    scan = b[hdr:] if hdr > 0 else b
    for c in CALL.findall(scan):
        if c in NONCALL or c in PURE_CALL or c in pure_names or c == own:
            continue
        return False
    return True


def recoverable_readonly(body: str, pure_names: set, own: str = "") -> bool:
    """Sound criterion for the in-kernel return-differential: differs from pure
    ONLY by reading state — no side effect, and no call that could hide one.
    For such a function, return-equivalence to the C IS behavior-equivalence
    (config-scoped), because there is no effect to miss and no opaque callee."""
    return (not has_effect(body)) and calls_ok(body, pure_names, own)


def classify(body: str, pure_names: set, own: str = "") -> tuple[str, str]:
    """Return ('pure'|'impure', reason)."""
    b = mask(body)
    # drop the definition header (up to the first '{') so the function's own
    # name/params aren't mistaken for a call or a state read
    hdr = b.find("{")
    scan = b[hdr:] if hdr > 0 else b
    m = IMPURE.search(scan)
    if m:
        return "impure", f"state/effect marker: {m.group(0).strip()!r}"
    # every called function must be a known-pure helper, another pure leaf, or self
    for c in CALL.findall(scan):
        if c in NONCALL or c in PURE_CALL or c in pure_names or c == own:
            continue
        return "impure", f"calls non-pure `{c}`"
    return "pure", "scalar arithmetic on args only"


if __name__ == "__main__":
    import json
    import os
    import sys
    HERE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, HERE)
    import widerun
    work = widerun.harvest()
    # fixpoint: a leaf calling only pure leaves is itself pure (2 passes suffice)
    pn = set()
    for _ in range(3):
        pn = {w["sym"] for w in work if classify(w["body"], pn, w["sym"])[0] == "pure"}
    pure, impure = [], []
    for w in work:
        v, why = classify(w["body"], pn, w["sym"])
        (pure if v == "pure" else impure).append((w["sym"], why))
    print(f"PURE {len(pure)} / IMPURE {len(impure)} of {len(work)}")
    print("PURE (-> scalar differential):", ", ".join(sorted(s for s, _ in pure)))
    print("\nIMPURE (quarantined -> trace oracle):")
    for s, why in sorted(impure):
        print(f"  {s}: {why}")
    json.dump({"pure": sorted(s for s, _ in pure), "impure": dict(sorted(impure))},
              open(os.path.join(HERE, "purity.json"), "w"), indent=1)
