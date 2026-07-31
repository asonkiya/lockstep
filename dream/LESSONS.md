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

## The components this session added (the map)

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

The through-line: **the entangled core is far more automatable than "research-months
for all of it," but not almost-entirely — ~48% strongly, ~40% at coverage-gated
assurance, ~6–10% a by-design floor — and every reach number must be measured, not
hoped.**
