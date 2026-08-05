# Research pass: the safe-Rust plan vs the field (2026-08-05)

Three parallel sourced passes, run immediately after the tier-(b) lift shipped
(d4432ee): (1) unsafe→safe lifting prior art, (2) Rust aliasing soundness in
kernel context, (3) verified-refactoring prior art + landscape delta since the
July survey. Full sourced reports in the session; this file keeps the
load-bearing findings and the plan amendments they force.

## Pass 2 first, because it found a real hazard in what we shipped

**Our `&mut Mirror` boundary over-claims.** `&mut T` from a raw pointer
asserts EXCLUSIVE access to every byte of T for the whole call (rustc emits
LLVM `noalias` + `dereferenceable`; LLVM will reorder, coalesce, and can
insert spurious loads — the mutable-noalias miscompile history is real, not
theoretical). Consequences for us:

- Our padded mirrors span offset 0 → last accessed field, so the `[u8; N]`
  padding covers OTHER REAL FIELDS of the kernel struct. A concurrent access
  to any padding-covered field during our call is a foreign access inside our
  noalias scope → UB, even though our code never touches those bytes.
- **Kernel-"benign" races are still Rust UB.** KCSAN tolerates plain-access
  races via `data_race()`; Rust has no benign-race category. A struct that is
  fine by kernel standards can be UB to hold a Rust reference over.
- `UnsafeCell` inside the mirror does NOT rescue `&mut` (only `&` loses
  noalias); the real opt-out is `UnsafePinned` (unstable). This is exactly why
  Rust-for-Linux wraps C-shared structs in `Opaque<T>` and never takes
  whole-struct `&mut`; their `Guard` hands out `&mut` ONLY when the lock
  serializes every access to those bytes.
- When IS it sound: no other pointer touches any borrowed byte between call
  entry and return. Lock-held callers deliver this iff the lock covers ALL
  borrowed bytes. Single-threaded windows (early init/probe/teardown) are fine.

**Amendment A1 (implement before growing the lifted set): field-granular
borrows + per-field concurrency audit.**
- Boundary derefs each accessed field individually
  (`&mut *((p as *mut u8).add(OFF) as *mut i32)`) — exclusivity shrinks to
  exactly the bytes the C body accessed under the caller's discipline (the
  R4L Guard precedent), and the padding hazard vanishes structurally.
- Safe core takes `&mut i32`-style per-field params; still
  `#![forbid(unsafe_code)]`.
- Static audit per function: grep the kernel tree for `atomic_t`/`READ_ONCE`/
  `WRITE_ONCE`/`data_race`/RCU markers on the ACCESSED fields; any hit →
  demote to tier (a) or route to an atomic-typed member (LKMM `Atomic<T>`
  pattern) — fail-closed, tallied.
- Honest reporting note until A1 lands: the 51 lifted fns' boot is evidence of
  integration, not of aliasing soundness (UB is not boot-detectable). The
  tier-(b) claim is "machine-checked safe core"; the boundary's exclusivity
  invariant is currently ASSUMED, post-A1 it becomes audited-and-minimal.

## Pass 1: lifting prior art — where we sit

- **Mechanical lifting has proven low ceilings**: Laertes (borrow-checker-as-
  oracle rewriting) reaches only ~11% of pointers (their own OOPSLA'23
  follow-up); Crown 37% median; PR2 18.6%. LLM-rewrite-then-verify is the
  field's only route past the ceiling — which is our architecture.
- **Nobody re-runs a recorded/manufactured differential oracle after a lift.**
  The field's lift-verification axis: Laertes = c2rust cross-check on existing
  test inputs; C2SaferRust/ENCRUST = E2E test suites; SACTOR = FFI harness
  re-run at both stages; VERT = PBT + Kani; RustAssure = symbolic; REM2.0 =
  Coq proofs but SAFE-SUBSET ONLY (cannot touch unsafe). Our "same
  manufactured differential re-run after the transform" is unclaimed.
- **Our safety tier is an unclaimed metric.** Papers report unsafe-LOC% and
  raw-pointer counts; `forbid(unsafe_code)`-module + one audited unsafe deref
  appears in style guides but in NO paper as the measured artifact. It sits in
  the empty middle between "unsafe% went down" and "whole program safe"
  (Mini-C/Cpp2Rust, C-subset only). Name it, and report unsafe-LOC% alongside
  for comparability.
- **Steal list**: PR2's per-pointer decision-tree prompts (for lifting
  model-written reader bodies); ENCRUST's ABI-wrapper-then-deterministic-
  elimination (our boundary shim's future shrink path); VERT's Kani rung as an
  optional proof tier above the differential for loop-free lifted cores;
  OOPSLA'23's aliasing-failure taxonomy to PRE-classify fns that will never
  borrow-check as pure `&mut` (multi-borrow aliasing, fn-ptr signatures) and
  route them to redesign instead of retry.
- **Expected cost curve**: SACTOR's idiomatic stage drops 85%→52% vs faithful.
  Budget retries for the tier-(c) push; the lift cliff is real.

## Pass 3: refactor-under-oracle novelty + landscape delta

- "Refactor then re-run tests" is now STANDARD (C2SaferRust Jan-25 →
  Ship-of-Theseus Jul-26, which stages faithful→idiomatic with per-step
  behavioral gates on a 12.5k-SLOC userspace program). Do not claim the loop.
- **Claim the oracle**: a Feb-2026 differential-fuzzing study found 19–35% of
  LLM refactorings functionally non-equivalent and **~21% of the broken ones
  pass existing test suites** (arXiv 2602.15761). That number is the one-line
  justification for differential/trace-pinned gating over test-suite gating.
- Formal branch (REM/Aeneas/Coq) proves refactor equivalence but only inside
  safe Rust — our oracle is the instrument for the unverifiable fragment, with
  a natural handoff to proof-carrying refactoring once code crosses into the
  safe subset.
- **The whitespace is still empty**: no project does in-kernel, boot-gated,
  sanitizer-augmented differential acceptance of translated/lifted kernel
  code. Nearest neighbors: AUTODRIVER (QEMU-boot-gated LLM driver maintenance,
  but C→C and boot-only gate), LLMigrate (kernel modules, compile-gated).
  TRACTOR stays userspace (Battery-01/round-1 report out; performers publish
  interface-typing and agentic translation, no lift-with-reverification).
  R4L is now permanent/core with ~40k new hand-written Rust lines in 7.2 —
  all hand-written, none translated.
- **Move fast**: Ship-of-Theseus shows a single author can publish the staged
  methodology quickly; the defensible artifact is our running combination
  (manufactured differential oracles + batched boot + KCSAN conviction +
  machine-checked lift tier) — worth a preprint before a TRACTOR performer or
  the AUTODRIVER group extends into the kernel.

## Amendments queue (ordered)

1. **A1 field-granular borrows + per-field concurrency audit** (soundness;
   before growing the lifted set). Re-gate all 52 lifted forms, reweave, boot.
2. **A2 metrics**: report unsafe-LOC% + raw-ptr counts alongside the named
   tier system; state the boundary invariant per function (lock-held /
   init-window / audited-field).
3. **A3 readers lift** via SACTOR-stage-2 protocol (pin ABI, re-run the same
   structdiff harness) + PR2 decision-tree prompts; pre-classify with the
   OOPSLA'23 aliasing taxonomy.
4. **A4 optional Kani rung** over loop-free lifted cores (formal tier above
   the differential, machinery already installed from dream/formal).
5. **A5 publication**: preprint the in-kernel acceptance loop + lift tier;
   cite the 21% test-suite-escape number against test-gated competitors.
