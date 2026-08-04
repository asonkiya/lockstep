# RUN 2 — graded against PREREG-RUN2.md

Run window 2026-08-04 ~17:30–18:19 local, cold start (Run-1 checkpoints
archived), runner frozen at `5e821c5`, gates/harnesses at `014f257`. Steps:
prep → readers → containers → efftrace → alloc → leaves → boot.

## VERDICT: **SUCCESS** (primary endpoint cleared; one component PARTIAL)

## Invariants — all five INTACT

1. Zero false passes known; every solve is a differential MATCH.
2. Negative controls **9/9 rejected** at launch (8 named + the new readers
   sabotage), recorded in `run2/controls.txt` before phase 1.
3. No mid-run edits — runner and gates at their pre-launch commits throughout.
4. **Accounting audit: zero silent drops.** Every entry of every frozen
   denominator has a logged verdict — including both `cache_contiguous`
   instances (110/110 efftrace verdict lines), the exact Run-1 breach, now
   caught by the machine itself under file-qualified keys.
5. Flags reported (below).

## Endpoints

| endpoint | pre-reg SUCCESS | measured | grade |
|---|---|---|---|
| **oracles combined (n=256)** | ≥85% | **225 (87.9%)** | **SUCCESS** |
| readers (frozen n=40) | ≥70% | 23 (57%) | PARTIAL band |
| containers (83) | ≥70% | 71 (86%) | SUCCESS |
| efftrace (110) | ≥70% | 108 (98%) | SUCCESS |
| alloc (23) | ≥70% | 23 (100%) | SUCCESS |
| leaves (frozen n=7) | ≥5 | **6** (int_sqrt missed) | SUCCESS |
| phase-2 boot | ≥90%, kernel boots | **6/6**, boots | SUCCESS |
| spend | ≤$5 | **$0.24** | SUCCESS |
| wall-clock | ≤48 h | **~50 min** | SUCCESS |

Scope flags across solves: locks_stripped and alloc_stripped carried as in
Run 1's profile (containers/efftrace/alloc lines in `run2/*.log`); every
"verified" is a scoped state-transition claim per its flags.

## Run 1 → Run 2, same machine family, honest deltas

| | Run 1 | Run 2 (cold start) |
|---|---|---|
| verdict | FAILED (invariant 4) | SUCCESS |
| combined oracles | 212/294 (72%) | 225/256 (87.9%) |
| readers | 11/78 (14%) | 23/40 (57%) |
| silent drops | 1 (caught by audit) | 0 (caught by design) |
| leaves | 2/72 vs a dishonest denominator | 6/7 vs the front-gated one |

The census-fix cycle did exactly what the decision rules bought it for: the
readers harness went from 40-unwinnable-by-construction to fully coherent
(solves doubled on half the denominator), and the leaves phase now has a real
front gate instead of a mis-scoped threshold.

## Residuals (Run 3 backlog, in census order)

- **Readers 17 misses** (the PARTIAL): mostly the 16-DIVERGE-capable set from
  the coherence census — genuine translation difficulty against the mirror
  differential, plus the 10-fn residual tail (const-array/lookup-table
  emission is the named widener). Trajectory FAILED→PARTIAL; per the rules,
  one more census cycle before its next measurement. Not the two-partials
  trigger (that is armed on the COMBINED endpoint, which succeeded).
- `int_sqrt` (leaves): loop-heavy; solved historically in Ring 1 with a
  richer prompt — ladder prompt depth is the suspect.
- 2 efftrace misses, 12 container misses: standing census classes.

## Decision (pre-committed)

**SUCCESS → proceed to Milestone B prep: the ksdk mirror factory** — the only
lever that moves the percentage (Tier-B 48%) rather than the count. These
numbers are the baseline row: the machine converts its full gate-admitted set
at 87.9% for $0.24 in under an hour, cold, with its own instrumentation now
proving completeness.
