# Concurrency gate — the in-kernel race oracle, on the realistic bug

This is the direction the prior-art survey identified as the one genuinely
unclaimed contribution: **an in-kernel, concurrency-aware acceptance gate** — the
kernel's own KCSAN data-race detector, plus a coupled invariant, judging a
transplant under real SMP load. Every translation system in the literature gates
on single-threaded I/O comparison, which is blind to exactly this bug class.

Where M4 used a *gross* negative control (drop the lock entirely), this uses the
**realistic** one: a **narrowed critical section** — the transplant keeps the lock
but lets one field of a coupled invariant escape it. That is the mistake a real
region transplant actually makes, and it is invisible to a return-value check.

## Subject

`struct acct_fields { long count; long mirror; }` with the invariant
`mirror == count` at every lock release. The Rust transplant holds the kernel's
REAL spinlock (out-of-line `_raw_spin_lock`, lockdep-visible) and updates both
fields in one guard scope. Probe: 4 SMP pushers hammer `acct_add(+1)` under load
while a reader kthread takes the same lock and reads both fields with plain
(KCSAN-instrumented) loads, checking `mirror == count` on every read.

## Two legs, two oracles

```
correct : ACCT_PROBE count=1600000 mirror=1600000 inv_violations=0       FUNC_PASS ; probe-KCSAN=0
subtle  : ACCT_PROBE count=1600000 mirror=1599985 inv_violations=420286  FUNC_FAIL ; probe-KCSAN=1
          KCSAN: race in reader+0x50 (read of mirror), "value changed 0x52521->0x52522",
                 race at unknown origin = the uninstrumented Rust writer (M4 detection path)
```

- **correct** — both fields under the guard: 1.6M coupled updates across 4 CPUs,
  428k invariant-checked reads, `mirror==count` every time, KCSAN silent. Clean.
- **subtle** — `mirror` moved just past the guard's end (`} unsafe { (*f).mirror
  += delta; }`): the mirror write now runs with no lock held. Caught **two
  independent ways** at once — KCSAN reported the data race on the reader's mirror
  load, and the invariant broke 420,286 times. And the final `mirror=1599985 !=
  count=1600000` shows the escaped writes actually **lost updates** (real
  corruption), not just transient skew — a return check that only compared final
  counts would still have missed it, but the coupled read caught it live.

## Why this is the point

A return-value differential — the entire field's acceptance oracle — cannot see
this bug: `acct_add` returns nothing, and given enough time the final totals even
converge. The defect is purely in the *ordering discipline*, and only a dynamic
race detector wired in as the accept/reject gate catches it. That gate, inside a
booting kernel, on a realistic narrowed-critical-section bug, is the whitespace
the prior-art survey found empty across the translation literature and R4L alike.

## Status

- Real kernel spinlock, coupled-invariant region, transplanted to Rust, verified
  under KCSAN + invariant across 4-CPU load — clean. ✅
- The realistic transplant bug (narrowed critical section, not a dropped lock)
  REJECTED by both KCSAN and the invariant — a return check would pass it. ✅
- Concretely occupies the one contribution the field hasn't: concurrency-aware,
  in-kernel translation acceptance. ✅
