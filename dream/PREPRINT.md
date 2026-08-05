# Differential-Gated Translation and Safety-Lifting of Linux Kernel C to Rust, Verified In-Kernel

**Draft preprint (A5). Status: complete draft, numbers current as of 2026-08-05,
commit `ed63173`. Not submitted.**

---

## Abstract

Automated C→Rust translation is a crowded field, but its acceptance gates stop
at the process boundary: translations are checked against test suites, FFI
harnesses, property tests, or bounded model checkers *in userspace*. Kernel code
has no `main()`, no stdin, and its correctness is dominated by state transitions
and locking discipline that return-value testing cannot observe. We present a
pipeline that (i) translates real Linux kernel functions to Rust under
**manufactured differential oracles** — state-footprint, container-ADT,
effect-trace and allocator-init differentials built per function rather than
borrowed from an existing test suite; (ii) **realizes** cell-model translations
into real-struct functions and re-certifies them with the same oracle;
(iii) **weaves** them into a real `vmlinux`, gated by in-tree layout assertions
and a boot digest; and (iv) **lifts** them to a machine-checked memory-safe
form, re-running the same differential across the lift and discharging it
formally with bounded model checking.

The result is, to our knowledge, the first body of C→Rust translations
*verified inside a booting Linux kernel*: **64 translated functions present in
a booting arm64 `vmlinux`, 38 of them with `#![forbid(unsafe_code)]` cores**,
and 14+ of those with the safety lift **proven** equivalent and panic-free over
the entire input domain. Across ~25,000 candidate evaluations the pipeline
recorded **zero false passes**, a property maintained by construction: every
gate is fail-closed, and each was shown non-vacuous by a negative control.

---

## 1. What is and is not novel

We are explicit about this because the field is crowded and moving fast.

**Not novel.** LLM-driven C→Rust translation (DARPA TRACTOR, ~$14M, ~6–7
teams); differential testing as the acceptance gate (Fluorine, Syzygy, SACTOR,
RustAssure, VERT); rule-based transpilation (c2rust); staged
faithful-then-idiomatic pipelines with per-step revalidation (C2SaferRust,
ENCRUST, Ship-of-Theseus); mechanical unsafe→safe lifting (Laertes, Crown,
PR2); bounded model checking of translations (VERT, Kani/CBMC). LLMs have
already touched kernel modules (LLMigrate: `math`, `sort`, `ramfs`, gated by
compile + manual review).

**Novel, and the contribution of this work:**

1. **In-kernel behavioral acceptance.** Translations are compiled freestanding,
   linked into a real `vmlinux`, and gated on a boot digest plus symbol
   presence. Prior work verifies in userspace or against a lifted oracle. The
   nearest neighbor (AUTODRIVER) boot-gates *C→C* driver maintenance, without
   an equivalence oracle.
2. **Manufactured oracles for code that has none.** Kernel functions have no
   test suite to reuse. We *build* the oracle per function class — full-footprint
   state differentials, MMIO record/replay traces, container-ADT equality,
   allocator-init models — with a coverage gate that refuses to certify an
   unexercised write.
3. **Differential-gated safety lifting.** The unsafe→safe transform is
   re-verified by the *same* oracle that certified the translation, then
   discharged formally. Prior lifting work validates with whatever tests exist;
   a 2026 study found ~21% of behaviorally-broken LLM refactorings pass
   existing test suites, which is precisely the gap a manufactured differential
   closes.
4. **A machine-checked safety tier as the reported metric.** The field reports
   unsafe-LOC% and raw-pointer counts. We additionally report the fraction of
   translated logic inside a rustc-enforced `#![forbid(unsafe_code)]` module
   with a field-scoped unsafe boundary — a claim the compiler checks, and one we
   found unclaimed in the literature.

## 2. Pipeline

```
census → harvest → synth (c2rust → local 14B → API tail)
       → DIFFERENTIAL ORACLE (per class, manufactured)
       → realize (cell model → real struct) → re-differential
       → weave (freestanding obj + in-tree layout asserts) → BOOT GATE
       → safety lift (forbid core + field-scoped boundary) → re-differential
       → Kani/CBMC proof of the lift
```

**Routing by provable class.** A purity router assigns each function to the
strongest *sound* oracle it qualifies for (host differential, one-boot in-kernel
differential, MMIO trace, effect trace, concurrency gate) and refuses to place
a function under a weaker one. This was added after we observed a return-value
differential *over-crediting* side-effectful functions — they reproduced the
return while dropping the effects. Routing makes that failure mode structurally
impossible.

**Realization.** Effect-trace candidates are verified as flat cell models, not
real-struct functions. Because their logic occupies a closed helper vocabulary
whose indices were derived from real struct fields, realization is a
*deterministic transpile* — not a re-synthesis — followed by re-running the same
differential against a real-layout arena. The transpiler is untrusted: a
transpile bug is a state divergence.

**Weaving.** Field offsets are probed by the kernel's own compiler (arrays sized
`offsetof(struct, field)+1`, read back from the ELF symbol table), and the woven
object carries dual layout guards: rustc `offset_of!` const-assertions and
in-tree `_Static_assert`s checked against real kernel headers at kernel build.
Layout drift fails the build; it cannot boot wrong.

## 3. The safety lift

A translated function starts as a faithful mirror: `#[repr(C)]` layout, C ABI,
raw-pointer field access. That form is *checkable* (structural closeness is what
makes a differential possible) but carries **no memory-safety claim**. The lift
moves the verified logic into a `#![forbid(unsafe_code)]` module and reduces the
unsafe surface to a boundary.

**Field-granular borrows.** The boundary passes one reference *per accessed
field* (`core(&mut (*p).f1, &mut (*p).f2, …)`), never a whole-struct `&mut`. This
matters: `&mut T` asserts LLVM `noalias` over *every byte* of `T`, and our padded
mirrors span padding that covers **other real kernel fields**; a concurrent
access to any of them during the call would be UB even though our code never
touches it.

**Per-field concurrency audit.** A field-scoped borrow is still UB if another
CPU touches that field during the borrow — and kernel-"benign" races
(`data_race()`, KCSAN-tolerated) are *still* Rust UB, since Rust has no benign-race
category. We therefore demote any function whose accessed fields appear in
`READ_ONCE`/`WRITE_ONCE`/`data_race` anywhere in the tree. Over 317 structurally
liftable candidates, **199 (63%) pass the audit; 118 (37%) are demoted** to the
mirror tier. This is why Rust-for-Linux wraps C-shared structs in `Opaque<T>`
and only hands out `&mut` behind a lock guard; we adopt the same discipline
mechanically.

**Reported tiers.** (a) mirror/unsafe, equivalence-pinned; (b) machine-checked
safe core with an audited field-scoped boundary; (c) fully idiomatic. Progress
in (a) alone is pipeline progress, not safety progress — a distinction we report
rather than blur.

## 4. The formal rung

The differential *samples* the lift; Kani/CBMC *proves* it. For each lifted
function we emit a crate containing both real artifacts, run them on identical
symbolic struct state, and assert identical return and identical post-state for
all inputs. Verdicts distinguish `LIFT_FAILED` (the transform is wrong) from
`PANIC_RISK` (the lift is proven, but both forms can panic).

That distinction paid immediately. `seqbuf_seek` proved lift-equivalent but
failed on `attempt to add with overflow`: `pos + offset` over the full `i64`
domain. In a freestanding kernel object the panic handler is `loop {}`, so a
reachable panic is a **kernel hang** — and a sampled differential structurally
cannot find it. The fix pins wrapping arithmetic in the source; the full census
re-ran with byte-identical results (no regressions) and the batch went to
**14/14 PROVEN**.

## 5. Results

| stage | measured |
|---|---|
| census of the arm64 build | 24,194 functions; ~89% reachable in principle, ~17% strongly verifiable, ~11% entangled C-forever floor |
| verified translations banked | 1,124 (104 reader, 635 effect-trace, 344 container, 41 alloc) |
| effect-trace realized + re-verified | **480 / 635 (75.6%)**, 0 divergences |
| structurally liftable (single-node) | 317 |
| **tier-b eligible after concurrency audit** | **199 / 317 (63%)** |
| present in a booting arm64 `vmlinux` | **64** (54 realized + 10 reader) |
| of those, machine-checked safe cores | **38** (31 realized + 7 reader) |
| safe-logic fraction of translated LOC | **32%** (139 / 434) |
| raw-pointer derefs | 214, all in field-scoped boundaries, **0 in cores** |
| Kani lift proofs | **14/14 PROVEN** (equivalence + panic-freedom, full domain) |
| false passes, ~25k candidate evaluations | **0** |
| test suite | 34 test files; every gate has a non-vacuity control |

Woven functions include core-path code: `kernel/sched/fair.c`
(`update_load_add/sub`), `mm/filemap.c`, `net/core/rtnetlink.c`, `kernel/pid.c`,
`fs/seq_file.c`, `block/bdev.c`, `kernel/time/timer.c`.

## 6. Honest limits

- **Percentages are small and we do not round them up.** 64 functions is ~0.09%
  of `vmlinux` text symbols and ~0.013% of `.text` bytes. The contribution is
  the verified pipeline, not the coverage.
- **Presence ≠ verification ≠ soundness.** These are reported separately
  throughout. A green boot is integration evidence; it does not establish
  aliasing soundness (UB is not boot-detectable), which is why the audit and the
  formal rung exist.
- **The concurrency audit is name-level** and therefore over-approximate: a
  same-named field on an unrelated struct forces a demotion. Safe direction,
  real cost.
- **Kani proves transform equivalence and panic-freedom, not equivalence to the
  C.** That is the differential's claim; the two compose. Looping cores need an
  unwind bound (bounded-complete) and are flagged, not silently under-proven.
- **Config coverage bounds integration, and we measured it the hard way.** A
  pre-registered defconfig experiment was graded LEVER-DEAD against its own
  frozen denominator: 83 of 104 verified readers live in files no arm64 config
  builds.
- **The mirror tier is not memory-safe**, and the safety-lifted tier's soundness
  rests on an audited invariant (the caller's locking discipline), not a proof
  of that invariant.

## 7. Method notes (negative results worth recording)

- **Census numbers shrink 2–5× on contact.** 437 struct-branch candidates →
  ~60 mirrorable; 104 verified readers → 20 linkable → 10 present; 86 harvested
  leaves → 6 host-compilable. We now pre-register denominators before measuring.
- **Pre-registration caught a rationalization.** One run was graded FAILED on
  an invariant breach (a silent drop from a name collision) that would otherwise
  have been reported as a partial success.
- **Vacuous gates are the recurring danger.** A layout guard inside a
  `__maybe_unused` function was dead-code-eliminated at `-O2`; a boot gate passed
  on a stale kernel image after a failed relink; a concurrency audit returned
  "0 racy fields" from a regex that silently matched nothing. Each was caught by
  demanding a *non-vacuity control* — the gate must be shown to fail on a known
  bad input before a pass is believed.

## 8. Availability

Pipeline, oracles, weave machinery, proofs, per-experiment pre-registrations and
graded reports are in the project repository (`dream/`). Every headline number in
§5 is reproducible from a committed script.
