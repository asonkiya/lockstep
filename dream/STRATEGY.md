# Strategy: the 17% campaign, and why mirror-style is the on-ramp, not the destination

Written 2026-08-05, after the realized batch weave (64 Rust fns in a booting
defconfig kernel, eec8f9f). Two assessments to hold ourselves to later, plus
the strategic point that frames both.

## 1. Odds on the full 17% (~4,100 strongly-verifiable fns), stated before we go

Three bars, three different probabilities — pre-registered so future-us can't
grade on a curve:

- **Mechanism failure: ~5%.** Every pipeline stage exists and survived
  adversarial testing (0 false passes across ~25k candidates, 300+ test wall).
  Nothing on the 17% path is unproven machinery.
- **A field-leading multi-thousand-fn verified ratchet: ~85–90%.** The grind is
  mechanical, per-class yields are measured (74–92% scalar first-attempt,
  75.6% realize pre-v2). Threats are boring: config coverage, wall-clock,
  estimate creep (assume 2–3×).
- **Literally reaching 17%: ~50–60%.** The census-shrinkage law — OUR OWN
  repeated finding — says every population number shrinks 2–5× on contact
  (437→~60 mirrorable; 104 readers→10 present; 86 leaves→6 TU-compilable).
  We are at 4.6% verified AFTER tree-wide sweeps of the easy classes. Realistic
  landing zone with current machinery + grind: **8–12%**; the full 17% likely
  needs 1–2 new reach mechanisms (interprocedural depth is the top candidate).

**Pre-registered decision gate: milestone at 8% verified.** At that point,
grade the yield curve: if the marginal census-fix cycle still returns >50
fns/cycle, push on; if it has flattened to single digits, the wall is real —
name it and change levers instead of rationalizing. (Two-partials rule
applies.)

Cost envelope (from measured unit economics): a few hundred dollars + roughly
a month of autonomous grind to the 8% gate. Presence-in-one-vmlinux will lag
verified throughout; report both, never conflate them.

## 2. Mirror style vs TRACTOR style — different artifacts, different claims

**Mirror style (ours):** `#[repr(C)]` layout mirrors, C ABI signatures,
value-logic near-verbatim, `unsafe` pointer access. Correctness claim =
**behavioral equivalence to the C**, and that claim is CHECKABLE — the
structural closeness is exactly what makes the differential oracle work, and
layout guards make the ABI coupling fail-closed. Buys: drop-in weaving into a
live kernel, function-at-a-time independence, no API redesign. Does NOT buy:
memory safety. Unsafe-heavy Rust over raw pointers is not what rustc proves
things about.

**TRACTOR style (idiomatic/safe):** ownership, slices, Result. Eliminates the
memory-bug classes BY CONSTRUCTION — the actual security payoff. Costs exactly
what our pipeline depends on: structural closeness (equivalence gets hard to
check), function independence (ownership is whole-program), ABI compatibility
(needs hand-built abstraction layers — the thing R4L spends its effort on).

**They compose in one order only: mirror first, idiomatize second.**
Mirror-translate → equivalence pinned by an oracle → idiomatization becomes a
**Rust→Rust refactor gated by the SAME differential**, each safety-lifting
step re-verified against pinned behavior. TRACTOR-first on kernel code means
verifying a structurally alien translation with no cheap oracle — the exact
gap where compile+test-suite acceptance is weakest and ours is strongest.

## 3. The point that frames everything (the user's, and it's right)

**The reason people want Rust in Linux is memory safety — safer code under
management, fewer CVE classes, less review burden on unsafe patterns.** That
is what "Rust in the kernel" MEANS to its constituency.

Consequences we must not lose sight of:

- **Mirror-style % is not the deliverable.** A majority-mirror-Rust kernel is
  evidence + infrastructure (the oracles, the ratchet, the proof chain), not
  the thing anyone asked for. Nobody wants 17% of the kernel rewritten as
  unsafe Rust for its own sake.
- **The differential-gated idiomatization ratchet IS the deliverable.** The
  unique asset we hold is the acceptance oracle; its highest use is making
  safety-lifting *verifiable*: mirror-pin behavior, then lift to safe idioms
  step by step with every step re-gated. That answers the actual motivation
  (safety) with the thing the field lacks (a behavioral gate for each lift).
- **Metric to report going forward: not just %-Rust, but %-Rust × safety
  tier** — (a) mirror/unsafe equivalence-pinned, (b) partially lifted (safe
  logic, unsafe boundary), (c) fully idiomatic behind an abstraction. Progress
  on (a) alone is pipeline progress, not mission progress; the mission needs
  (a)→(b)→(c) flow.
- Positioning vs TRACTOR: complementary, not competitive. Their translators
  can feed our synth rung; our in-kernel concurrency-aware gate (the empty
  whitespace in PRIOR-ART.md) is what makes anyone's idiomatic translation
  *acceptable* into a live kernel.

Follow-on when the 17% campaign spins up: build the idiomatization rung (M5's
sketch, now with the realize/weave machinery to gate it) on a handful of
already-woven fns — prove the (a)→(c) lift on real kernel code with the
differential holding the behavior fixed. That demo, small as it is, is worth
more strategically than another hundred mirror fns.

**Amended 2026-08-05 after the research pass (see RESEARCH-SAFE-RUST.md):**
the tier-(b) boundary must use FIELD-GRANULAR borrows with a per-field
concurrency audit — whole-struct `&mut Mirror` over-claims exclusivity
(padding bytes cover other real fields; kernel-benign races are Rust UB).
The safety-tier metric should be reported alongside unsafe-LOC% for
literature comparability; the forbid-module tier itself is unclaimed in any
published work, as is re-running a manufactured differential after a lift.
The in-kernel acceptance-loop whitespace remains empty as of 2026-08.

## 4. The scheduler rule (generalization slice, 2026-08-09)

Case-by-casing classes was the right way to BUILD the machinery; it is the
wrong way to RUN it at kernel scale. Two standing systems replace the ritual:

**The coverage precondition.** Every realize-gate MATCH now requires that the
C reference observed every guard at BOTH polarities and every op executing
(`container_realize._cov_enforce`; refusals `coverage:unexercised_branch:*` /
`coverage:dead_op:*`). The whole workload-hole defect class — pnull models
verified without a null row, `id != 0` passing as a null check, flip_guard
no-oping on one emission shape — is structurally impossible from here: a bad
workload yields a named refusal, never a false MATCH. Measured proof: the
historical holes, reconstructed via `run_gate(..., probe_flags=...)`, are
refused by the coverage check alone (test_container_coverage.py).

**The refusal ledger** (`ratchet/ledger.py`). Every stage refuses by name;
the ledger aggregates the persisted tallies (container censuses, efftrace
census.jsonl, cweave residuals) into one ranked table, estimates unlock per
class (realize-stage: count toward REALIZED; weave-stage: count x the
measured eligibility fraction toward PRESENT — currencies labeled, never
summed), and the funnel dashboard prints the top three.

**THE RULE: the campaign's next slice is the ledger's top lever unless a
human overrides with a written reason.** New oracle TYPES (multi-member
arena, break-variant semantics, Summit-3 state differentials) stay
hand-driven research with a human reading the negative controls; everything
around them — enumeration, freeze, re-pass, disposition, funnel — is the
machinery above.
