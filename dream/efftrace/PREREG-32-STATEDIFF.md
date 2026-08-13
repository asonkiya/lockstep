# PREREG — Summit 3.2: the in-kernel state-differential oracle (proof rung)

Frozen 2026-08-13, BEFORE any implementation. Proof-first (the `*/proof.py`
discipline): a SYNTHETIC subject with correct→PASS / sabotaged→REJECT / a
vacuity control, BEFORE any real-tree pilot. This is the ceiling-breaker for
the ~54% genuinely-unbounded-state tail (INTERPROC_RESULTS.md); it is NOT a
config or presence lever.

## Why a new oracle (what the existing ones do NOT observe)

- **hostdiff / structdiff / efftrace** verify a function whose read/write
  **footprint is statically BOUNDED** — efftrace shadows `memory[i]` per call
  and compares the full footprint. Measured reach: bounded_state ≈ 3% of core
  (1,230 fns, interprocedural). Sound, but only where the footprint is a
  fixed, enumerable set.
- **The 54% tail is unbounded**: the touched state is dynamically sized
  (lists/trees that grow with input, runtime allocations, state that spans
  opaque callees). You cannot statically shadow it, so efftrace refuses it to
  the hard fallback. Return-value differential OVER-CREDITS it (the measured
  `__refrigerator`/`probe_irq_mask` trap: identical return, dropped effect).
- **Ring-3** already proved the shape one level over: run C-ref and Rust-cand
  in a booting kernel under a deterministic workload, compare the *trace* at
  seam boundaries (register accesses), not the return. It caught a
  value-identical status-poll drop. **3.2 = Ring-3's trace idea applied to
  MEMORY STATE instead of MMIO registers**, so it reaches dynamically-sized
  state that can only be observed at runtime.

## The mechanism (proof rung, synthetic)

Link into a booting kernel: a C reference subject, a Rust candidate, and a
**state-snapshot probe**. The probe drives a fixed deterministic workload (a
recorded operation sequence) through each implementation and, **at each seam
boundary** (after each operation), serializes the subject's full reachable
mutable state into a canonical byte stream — walking dynamic structure
(list/tree nodes, allocated buffers) by traversal, NOT by a static footprint.
Compare the two snapshot STREAMS:

- identical stream  → **DIFF_PASS**
- divergent stream  → **DIFF_FAIL**

The snapshot is canonicalized (address-independent: serialize by traversal
order + value, never by pointer bits — same discipline as the T3 chain-digest,
which already made a growing linked structure comparable across two runs).

## The synthetic subject (unbounded by construction)

A minimal dynamically-sized ADT the arena cannot statically bound: an
insertion-ordered singly-linked accumulator with a per-node running reduction
(e.g. push(v) appends a node holding v and a folded checksum of all values so
far). State size grows with the workload — exactly the property that walls
efftrace. The workload: a fixed script of N pushes/pops/reorders with values
chosen so intermediate STATE differs even when a final return would not.

## Blind bars (frozen before any code)

- **B1 correct→PASS**: the faithful Rust translation produces a byte-identical
  snapshot stream across the whole workload. REQUIRED.
- **B2 sabotage→REJECT (≥3 distinct, each compile-clean & value-masked)**:
  1. *order bug* — appends in wrong position; final fold identical, mid-stream
     state differs → REJECT.
  2. *dropped-effect* — skips one state update whose value never surfaces in a
     return → REJECT (the over-credit trap, reproduced and caught).
  3. *late-corruption* — corrupts a field read only by a later op → REJECT.
  Every sabotage MUST compile and MUST return the same values as correct — a
  control that only BUILD_FAILs or changes a return proves nothing (the
  vacuous-control lesson, LESSONS.md).
- **B3 vacuity/liveness**: the snapshot stream MUST be non-constant across the
  workload (proof the probe observes real evolving state, not zeros), AND a
  semantically-equivalent-but-textually-different correct variant MUST still
  PASS (proof we reject on STATE, not on surface form). REQUIRED — an oracle
  that also rejects a valid rewrite is not sound-for-use.

## Grading & scope

- **SUCCESS**: B1 ∧ B2(3/3) ∧ B3 — the mechanism is proven; only THEN write the
  real-tree pilot PREREG (one real unbounded fn from the tail, e.g. a genuine
  list/tree mutator efftrace refused).
- **PARTIAL**: B1 ∧ B3 but a sabotage escapes → the snapshot is too coarse
  (missing a reachable region); document what escaped, refine traversal, re-run.
- **LEVER-DEAD**: cannot make a compile-clean value-masked sabotage divergent
  → the runtime snapshot can't see the state that matters; document honestly
  and the 54% tail stays hard (negative result is a deliverable).

## Cost / model note

Proof rung is synthetic — one small kernel build + boot per variant (correct +
3 sabotages + 2 vacuity ≈ 6 boots), HVF if wired. $0 model. Per NEXT.md this is
hand-driven research reserved for the strongest model; the design + this PREREG
are done now, execution proceeds proof-first and STOPS at the real-tree pilot
boundary for a fresh decision.
