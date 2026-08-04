# RUN 1 — graded against PREREG-RUN1.md (no re-grading, no denominator surgery)

Run window 2026-08-04 ~15:5x–16:51 local, steps via `run1.sh` (prep → readers
→ containers → efftrace → alloc → leaves → boot). Code frozen at `36bab4b`
(gates/harnesses/oracles); `run1.sh` itself was amended mid-run (`7b2334c`,
launch config only — disclosed below). Total model spend **$0.41**, total
wall-clock **~2.5 h** on the M2 Max alone.

## VERDICT: **FAILED (invariant 4 — one silent drop)**

One worklist entry received no verdict from the run's own instrumentation:
`cache_contiguous` appears in TWO files in the efftrace worklist, and the
done-key scheme (`efftrace_<fn>`, no file) collided — the second instance was
silently skipped after the first solved. It was caught by an audit between
steps, not by the machine. Pre-reg invariant 4 defines exactly this as an
instrumentation failure grading the run FAILED, and the clause exists so we
don't lawyer it. The endpoint measurements below remain real (every recorded
verdict is a genuine differential result; the breach is completeness
accounting, not soundness) — but the run's grade is the grade.

Invariants 1–3, 5: intact. Negative controls 8/8 rejected at launch
(`run1/controls.txt`). No gate/harness/oracle edits (36bab4b throughout).
Flags reported below. The `run1.sh` mid-run amendment (`N_LEAVES=0` on oracle
steps) changed launch config because phase 1C has no env gate and leaked into
the readers step — disclosed, judged not a gate edit; reasonable people may
disagree, and Run 2's pre-reg freezes the runner too.

## Endpoints (as pre-registered)

| endpoint | pre-reg SUCCESS | measured | grade |
|---|---|---|---|
| oracles combined (n=294) | ≥250 (85%) | **212 (72%)** | PARTIAL band |
| readers floor (n=78) | ≥70% | **11 (14%)** | FAILED its floor |
| containers (n=83) | ≥70% | **70 (84%)** | SUCCESS |
| efftrace (n=110) | ≥70% | **108 (98%)** | SUCCESS |
| alloc (n=23) | ≥70% | **23 (100%)** | SUCCESS |
| leaves (frozen n=72) | ≥70% | **2 (3%)** | FAILED |
| phase-2 boot | ≥90% of freestanding verified | **8/8 (100%)**, kernel boots | SUCCESS |
| spend | ≤ $5 | **$0.41** | SUCCESS |
| wall-clock | ≤ 48 h | **~2.5 h** | SUCCESS |

Secondary (report-only): **8 Rust functions boot-verified in one woven
kernel** this run (the freestanding bank; includes prior-session artifacts of
the same frozen machine).

Scope flags across solves (invariant 5): locks_stripped 42,
alloc_stripped 22, kmalloc_zero_modeled 2, logging_stripped 0 in solves.
Every "verified" above is scoped by its flags — state-transition claims, with
lock/alloc-failure/list halves deferred to their composition gates.

## Failure censuses (feed Run 2 — they do not re-grade Run 1)

**Readers 11/78** — first-ever measurement of this phase (never live-fired):
43 gate-rejected through both ladder rungs (real translation failures against
the mirror differential), 24 prepare-fails (unmappable mirror types —
`cpumask_var_t`, nested typedef'd structs — and 12 config-dependent `#if`
layouts), 11 solved. The mirror-type coverage gap is Tier-B/ksdk territory;
the 43 synth failures need a census of DIVERGE vs BUILD_FAIL before Run 2.

**Leaves 2/72** — the threshold was mis-calibrated at pre-reg time: 70% was
derived from the wide run's "74% first-attempt", which conditioned on
TU-liftable, provably-pure functions; the frozen harvest is neither. Measured
decomposition: most misses are `CC_TU_FAIL` (hostdiff can't compile the TU —
local headers like `block/blk.h` not on the include path, so the model was
never meaningfully in the loop) plus impure/MMIO fns (`__refrigerator`,
`cs5535_gpio_isset`) the router-backed gate can never soundly MATCH — honest
refusals sitting in a dishonest denominator. Run 2: harvest pre-filters by
purity route + TU-liftability (or hostdiff learns `-I<file's dir>`), and the
threshold is re-derived against that denominator.

**Efftrace 108/110** — 1 genuine miss (`rq_depth_calc_max_depth`, unsolved
through the full ladder), 1 the silent drop above.

## Decision (pre-committed rules applied)

- Invariant-4 consequence: **freeze**; restore the invariant — file-qualified
  done-keys (`efftrace_<file>_<fn>`) + a fixture test for same-name fns in
  two files — and demonstrate it before ANY further runs.
- Then the PARTIAL path: exactly one census-fix cycle (readers mirror types +
  leaf harvest scoping + the key fix), then **Run 2 under an amended pre-reg**
  (which also freezes the runner script and re-derives the leaves threshold).
- Per the two-partials rule: if Run 2 also lands PARTIAL on the combined
  oracle endpoint, stop running and redesign.

## What Run 1 actually established (within its failed grade)

The machine ran end-to-end unattended in resumable steps for $0.41: three of
four oracles replicated at or above their live-fire baselines against frozen
denominators, the sound gates rejected 8/8 sabotages plus every wrong
candidate en route, and the woven kernel booted with 8/8 verified. The two
component failures are measurement/coverage artifacts with written root
causes, not soundness breaks — and the pre-registration caught a real
instrumentation hole (the done-key collision) that every previous ad-hoc run
would have absorbed silently. That is the pre-reg working as designed.
