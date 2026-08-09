# LESSONS — what building the verified C→Rust kernel ratchet actually taught us

Distilled, measured lessons (not a changelog). Each is something we got wrong or
learned the hard way, with the number that settled it. Cross-refs to the doc that
holds the detail.

## The one meta-lesson: MEASURE before you build

Every reach estimate was optimistic until an actual analysis ran. The honest-broker
discipline — measure the real number before committing to a build — repeatedly
saved wasted work and corrected public claims:

- effect-trace on `bounded_state`: quoted **35.5%** (kind-based) → syntactic footprint
  **729** → interprocedural closure **1,230** (~3% of the core). A 12× over-claim,
  corrected. `dream/efftrace/{FOOTPRINT,INTERPROC}_RESULTS.md`.
- struct-driven branch reach: "**437**" (parseable+mirrorable) → **0** over the MMIO
  census (impure by construction) → the pure class lives in a *different* corpus
  (~450 tree-wide, 78 measured). `dream/structdiff/reach_accepted.json`.
- "$7 should do 100×": the first run did 8 fns for 8¢; the ambitious $7 run hit a
  **real ceiling** — the simple-differential-verifiable classes (scalar leaves +
  struct-readers) are *small* (low tens each), not a budget problem.

Corollary: **a bounded sample + honest extrapolation** beats a full 100k-fn pass;
and **persist the measurement** (worklists like `reach_accepted.json`) so it's never
recomputed.

## Soundness lessons (the non-negotiable)

- **Mechanism-proof-first.** Every oracle is proven on a *synthetic* subject before
  any real extraction is built on it (`*/proof.py`: structdiff, efftrace,
  container_adt, gpio_family). Cheap, catches design flaws early.
- **The over-credit trap → strictly-stronger oracles.** A return-value differential
  *passes* a candidate with identical RETURN but wrong STATE (`__refrigerator`,
  `probe_irq_mask`). The effect-trace oracle catches it; the ADT oracle is strictly
  stronger than an op-count check; the MMIO trace catches a wrong register program
  that returns the right value. Always ask: *what does my oracle NOT observe?*
- **The vacuous-gate trap.** A `BUILD_BUG_ON` inside a `static __maybe_unused`
  function is dead-code-eliminated at `-O2` — the kernel leg of the mirror gate was
  silently vacuous. Switched to file-scope `static_assert`. **Always run the
  negative control** (corrupt the input, confirm the gate REJECTS) — a gate you
  haven't seen fail may not be checking anything.
- **0 false passes, by construction.** 3,513 adversarial candidates in the wide test,
  0 false passes. A cheaper/worse synthesizer is fine *because* the gate can't be
  fooled — synth quality is a wall-clock knob, not a correctness knob.
- **The soundness ladder (assurance tiers).** Reaching harder classes costs
  *assurance*, honestly: bit-exact-exhaustive (pure/struct) → in-kernel-exact
  (bounded_state) → coverage-gated / fuzzing-grade (container/alloc/external tail) →
  environment-gated (reactive) → by-design-C (arch asm). "Reachable" ≠ "provable to
  the same standard." `dream/efftrace/UNBOUNDED_RESEARCH.md`.

## What makes a function verifiable — the 7 entanglement classes

The entangled core is not a monolith; it's classes, each with its own oracle
(`dream/router/entangle.py`, measured over 40,065 core fns):

| class | share | oracle | status |
|---|---|---|---|
| pure_leaf | 22.9% | scalar differential (hostdiff) | DONE |
| struct_reader | 8.2% | mirror differential (structdiff) | DONE |
| mmio | ~0% | record/replay trace (Ring 3-5 + family) | DONE |
| concurrent | 13.9% | loom + KCSAN (concgate) | PROTOTYPED |
| bounded_state | 35.5%* | effect-trace (CGIR effects + replay) | mechanism DONE |
| unbounded_state | 15.4% | in-kernel differential under workload | HARD |
| arch_asm | 4.0% | keep as `unsafe`/`global_asm!`, boot-digest | FLOOR |

\* upper bound; interprocedurally ~3% is cleanly bounded. **Route by what makes a
function hard, not by hope.** The "unbounded" tail is ~94% attackable
(container-ADT / allocator-model / annotation) but at coverage-gated assurance.

## The factorization lessons (why it's affordable)

- **The driver mass factors.** GPIO drivers → ~3 register idioms → in fact the kernel
  already factored them into `gpio-mmio.c`; container ops are a **finite closed
  vocabulary** (list/rbtree/xarray) backed by shared cores (`lib/rbtree.c`, …) with
  **Rust-for-Linux abstractions as ready rewrite targets** (`List`, `RBTree`).
  Cost is **O(idioms) + O(shared-libraries)**, not O(functions). regmap alone
  underlies 1,815 driver files. `dream/family/`, `dream/efftrace/UNBOUNDED_RESEARCH.md`.
- **Template synthesis is $0.** An idiom-recognizable driver's transplant is
  deterministic codegen from (idiom + offsets) — no model call. `dream/family/template_synth.py`.
- **The CGIR↔lockstep seam, precisely.** CGIR's `effects` layer gives effect *kinds*/
  purity — NOT the location read/write footprint (that's a PDG/reaching-defs
  question, computed in lockstep). Interprocedural closure recovers opaque callees
  but only partly (54% of `bounded_state` are genuinely unbounded a step down).

## The cost lessons

- **Synthesis cost is noise; verification wall-clock is the game.** Lifetime model
  spend across the whole project ≈ $0.25. The 900× lever was host-native
  differential (kill the boot). `dream/COSTDOWN.md`.
- **The synth ladder, cheapest-first:** template ($0) → c2rust ($0) → local Qwen
  ($0) → Haiku (paid tail, budget-capped). Most work is $0.
- **On owned hardware the cash floor is < $100**; the real cost is wall-clock +
  per-shared-library engineering. The first official rewrite: 8 boot-verified Rust
  fns in 8 min for **8¢**, 0 false passes (`dream/firstrun/`).

## The census-shrinkage law (name it, budget by it)

Stage populations shrink **2–5× on contact**, every time, and the shrinkage is
the finding — each drop names a real semantic boundary:

- or_null truthiness: census **25 → 18** surviving → **14** in-scope (14/14 MATCH).
- tokf equality: census **22 → 16** conditional-iteration → 10 member-compare →
  **7** in-scope (7/7 MATCH); the 3 break-variants refused for a real reason
  (delete-first-and-stop ≠ delete-all under duplicate tokens).
- weave eligibility: 289 realized → **44** defconfig-eligible (config linking, not
  machinery).

Corollary: price levers by the *post-shrinkage* number; pre-register the ladder
(census → shape → in-scope → verified) so the shrinkage is disclosed, not hidden.

## The workload-hole lessons (why coverage is now a gate precondition)

One root cause produced every model-bank defect class: **the gate declared MATCH
for functions whose predicates were never exercised on both sides.**

- pnull models shipped dead null-guards and verified — the workload had **no null
  row** (acpi_scan_add_handler; + 2 more only the new null rows caught:
  mmc_pwrseq_register wrong-return-on-NULL, nand_ecc panic-on-NULL).
- `id != 0` passed as a null check — the fresh pool **never held id 0**.
- flip_guard **no-oped on one emission shape** — a vacuous negative control (the
  vacuous-gate trap again, one level up: controls must be *seen to fail* per
  emission shape, not per mechanism).

The generalization (6ee6534): coverage counters in the C reference; **no MATCH
with a single-polarity guard or dead op** — fail-closed. Proven by reconstructing
the old bad workloads: the historical false-passers are refused by the coverage
check *alone*. "Did we think of the case" became a measured precondition.

Two codicils from the same repair (1512727, bank 344/344 clean, $0.08):
- **10 of the 29 "model defects" were tooling** — the verifier couldn't parse the
  correct answer (`del_m` dialect; over-strict `INIT_LIST_HEAD`). Before
  re-synthesizing a "bad" model, check the checker.
- **Some defects are workload-unkillable by construction** — spurious-del
  (esp_put_ent): within the caller contract no input distinguishes it (an
  already-linked node into `list_add` is corruption, not a differential). Caught
  structurally by correspondence-in-the-verify-loop. Differential and structural
  checks are complementary; neither subsumes the other.

## The presence lessons (what the weave batches taught)

- **`.o`-exists ≠ linked-into-vmlinux.** Our own probe passes force-build orphan
  objects (cxgb4_mps.o existed with CONFIG_CHELSIO_T4 unset) → a weave can ship
  into an object the kernel never links. Eligibility now checks the object's
  first defined global against vmlinux; a batch-time **seam-reference check**
  counts any unreferenced `_rs` as ABSENT. The stricter symbol-level fix was
  tried and **rejected on evidence** — it would have dropped 21 boot-verified
  inlined statics.
- **Batched probe make needs `-k`.** One CONFIG-gated field's probe failure
  aborted the whole `-j` make and *collaterally broke two previously
  boot-verified priors*. The blind bar (all priors present) caught it same-day —
  pre-registration is a regression detector, not ceremony.
- **Batch honestly skipped is a result.** When the re-frozen denominator came
  back identical (D=40), the batch was skipped per the pre-registered rule —
  re-weaving the same set proves nothing.
- **Config coverage is the presence wall, measured:** 843 realized vs 107
  present (0.44% of the 24,194 census). The lever is more volumes, not more
  verification.

## The scale lessons (the sweep + wide run)

- **The funnel is the honest headline** — 24,194 census / ~17% strongly-verifiable
  / ~11% C-forever; banked ≠ realized ≠ present ≠ tier-b, each with provenance
  (`ratchet/funnel.json`). Never quote one stage as another.
- **Wall-clock multipliers are real and multiply:** HVF boot 216→5.3 s (41×),
  2 workers = 1.99×, batched boots (4 leaves/1 boot/~292k comparisons).
  Bottleneck is provisioning, not capability.
- **Refusal taxonomy is the product.** The ledger (6af74ec) ranks refusal classes
  by unlock; its first ranking independently reproduced the hand-picked campaign
  queue. Scheduling is now data (`ledger.py levers`); new *oracle types* stay
  hand-driven research.

## The operational lessons (things that bit us)

- **Env/PATH traps cost a whole run.** Prepending `/opt/homebrew/bin` shadowed the
  `.venv` python (which has `anthropic`) with Homebrew's (which doesn't) → all Haiku
  calls threw `No module named 'anthropic'`, $0 spent, run underperformed. Lesson:
  **pin the interpreter**; don't reorder PATH blindly.
- **Module-name collisions.** Several oracle dirs each ship a `proof.py`; `sys.modules`
  cached the first and handed tests the wrong one (silent KeyError/ValueError). Load
  by explicit path under a unique name (`importlib`).
- **Orchestration economy.** The agent's own token bill dwarfs the pipeline's — route
  scans/measurements to Haiku, reserve the top model for design, keep I/O terse,
  check disk before scanning. `[[orchestration-economy]]` / `dream/COSTDOWN.md` §7.
- **Widening source ≠ more boot-woven fns.** The minimal `.config` links few symbols,
  so whole-tree leaves verify boot-free but get *dropped* from the weave. The lever
  for a bigger *booting-majority-Rust* kernel is a broader **config**, not corpus.
- **Foreign-arch gating has an ABI trap.** Gating for an arm64 kernel on an x86
  host flips plain-`char` signedness (arm64 unsigned, x86 signed). Fix: a `cc`
  shim pinning `-funsigned-char` on every gate compile (`infra/gpu3080/`), plus a
  fatal check that a stray ccache dir can't shadow it. Check host/target ABI
  before trusting a differential run on borrowed hardware.
- **Detached long-runs strand their agents.** Three sessions in a row, a worker
  parked "waiting" on a batch/census process that could never wake it; the
  coordinator hand-wired pid watchers each time. Long runs must be polled
  synchronously, handed to a watcher that CAN wake the owner, or checkpointed.
- **Don't pipe batch logs through `tail`.** It ate a full batch log once; capture
  to a file, read the file.
- **Shared docker volumes are exclusive.** A census run died colliding with
  concurrently-running suites in the same kbuild volume; gate re-passes run solo.
- **Check the substrate before trusting nulls.** A sparse KSRC checkout made a
  test pin reference a function that didn't exist locally; earlier, a macOS-cc
  header-lift "win" (57 fns) was a Darwin-vs-kernel type-collision artifact.
  Rewrite-in-place + boot-gate beats lift-to-TU for kernel code.

## The component map (by arc)

- `dream/mirror/` — nested/bitmap mirroring, **in-kernel opaque-primitive sizing**
  (probe real `sizeof` from the ELF symbol table), multi-declarator fix.
- `dream/structdiff/` — pure struct-reader mirror-differential + reach gate +
  `harness.prepare/close` (now mutator/void-aware).
- `dream/router/entangle.py` — the 7-class entanglement router.
- `dream/efftrace/` — effect-trace oracle proof + footprint + interprocedural closure
  + unbounded-tail sub-census.
- `dream/container_adt/` — representation-independent ADT oracle (LIST + rbtree),
  PRODUCTIZED: reach gate + harness over real fns (9/9 live-fire solves, $0.005;
  op-site coverage gate; locks/kfree stripped-and-flagged). Lesson repeated: the
  naive vocabulary (straight-line ops) measured ~zero real targets — real mutators
  iterate/anchor-on-globals/lock/free, and a gate-feedback repair round took the
  $0 local model from 0/5 to most solves.
- `dream/family/` — GPIO family trace oracle + template synth.
- `dream/firstrun/` — the autonomous runner (`overnight.py`, `ambitious.py`), the
  first official rewrite + the wide soundness test (`dream/widetest/`).

Added since (2026-08):
- `dream/realize/` — the deterministic $0 realize engine (efftrace v2 label-break
  transpiler 549/635; container realization T1–T3 + the three conditional
  classes, 289/344) + `CONTAINERS-FEASIBILITY.md`, the class-by-class ledger of
  record.
- `dream/container_adt/` (extended) — composed T3 gate (chain digest + ordered
  free-log, strictly stronger than the ADT retire-log — UAF ordering visible),
  executable predicates (list_empty / tokf / pnull at probed offsets),
  coverage-gated MATCH, bank-wide `reverify.py`.
- `dream/ratchet/` — `weave_containers.py` (whole-body weaves: real lock/kfree
  symbols, address-arithmetic list surgery, in-tree `_Static_assert` +
  LIST_POISON guards), `cweave_census.py` (frozen weave denominators + ORPHAN
  detection), `funnel.json` + dashboard, `ledger.py` (the refusal-ledger
  scheduler), PREREG-*/RUN-*-REPORT (blind bars + graded outcomes).
- `dream/infra/` — grinder (always-on $0 box), hetzner burst fleet, HVF boot
  (41×), gpu3080 (borrowed-GPU big pass, char-signedness shim).
- `dream/localmodel/` + the synth ladder in `overnight.py` — measured $0 local
  rung (qwen2.5-coder:14b = 62.5% first-pass, ~85% of Haiku on the battery).

The through-line: **the entangled core is far more automatable than "research-months
for all of it," but not almost-entirely — ~48% strongly, ~40% at coverage-gated
assurance, ~6–10% a by-design floor — and every reach number must be measured, not
hoped.**
