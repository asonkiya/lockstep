# Lockstep

**Verified region-level transplant of concurrent C into Rust.**

The companion to [CGIR](https://github.com/asonkiya/llm-semantic-compilers).
CGIR rewrites a codebase's pure-computational core, function by function,
verified end-to-end. Lockstep targets the part CGIR deliberately cannot reach:
the concurrent, stateful, memory-disciplined code that is most of an operating
system by volume — where a function's meaning is its locking, ownership, and
memory ordering, not its arithmetic.

## The idea in one paragraph

A function boundary is the wrong seam for concurrent code: the invariants span it
(a lock taken here protects a field touched there). So Lockstep's unit of rewrite
is a **semantic region** — a critical section, an RCU epoch, an ownership span —
transplanted into the Rust-for-Linux abstraction that *encodes* the invariant in
its type system (a `SpinLock<T>` guard, an `Rcu<T>` pointer, a `KBox<T>`), and
verified not by byte-identical output (wrong equivalence for concurrent code) but
by a **dynamic sanitizer battery** — KCSAN, lockdep, KUnit, syzkaller — run
stock-vs-transplant under adversarial load. A transplant is accepted only if it is
race-clean and deadlock-clean where stock is, with the same functional behavior.

## Why it's plausible now

CGIR just proved the foundation: a cheap model produces correct, verified Rust
rewrites of pure kernel functions at ~$0.007 each with zero false passes, and a
Rust object now links into `vmlinux` and is verified **inside a booting Linux
kernel** by the kernel's own execution — with a negative control that caught a
vacuous gate before it could lie. Lockstep inherits that harness (containerized
kbuild + QEMU) and that discipline (the wrong candidate is what proves the right
one means something).

## New to the concurrency side?

The from-zero learning course (syllabus + conversational lessons with coding
exercises) lives in its own repo: **[lockstep-course](https://github.com/asonkiya/lockstep-course)**.


## Status

**Design.** See [`docs/design.md`](docs/design.md) for the architecture, the
concurrency-aware IR, the oracle stack, the milestone ladder (M0–M5), and — most
importantly — the honest accounting of the hard problems and the ceiling.

## Relationship to Rust-for-Linux

R4L is the landing zone, not a competitor: humans design the safe abstractions;
Lockstep applies them at scale to existing C, gated by the kernel's own
sanitizers, emitting maintainer-reviewable patches. Where a region is too subtle
for the machine, it falls back to exactly the human process R4L already runs.
