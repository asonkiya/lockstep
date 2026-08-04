# PRE-REGISTRATION — Sweep 1 (tree-wide harvest + solve, local M2 Max)

Committed while the harvest is MID-FLIGHT with zero accepted-counts observed —
every threshold below is set blind. Code frozen at `48925c4` (gates, harnesses,
mirror bank, config-pinning, sweep harness). Any edit to a gate/harness/oracle/
sweep-harness after this commit invalidates the affected phase.

## What this sweep tests (two claims, graded separately)

**A. Coverage growth** — does tree-wide widening (28,679 files, drivers
UNSAMPLED vs the old 1/12 sample) meaningfully grow the gate-admitted set?
**B. Solve robustness** — does the machine hold its Run-2 solve rates on the
messier tree-wide population, or were those rates a curated-corpus artifact?

## Frozen denominators

The harvest output (`sweep/{readers,containers,efftrace,alloc}.json`) at
harvest completion IS the freeze — deterministic given the tree + frozen gates.
Rules: dedup by (file, fn); prepare-refusals and REFUSED_COVERAGE are MISSES;
an incompletely-harvested gate is INCOMPLETE, never "done with a smaller
denominator"; no post-hoc exclusion of any subsystem/directory.

## Endpoint A — coverage growth (baseline: 256 + 84 readers ≈ 340 gate-admitted)

| grade | total tree-wide accepted (4 gates, deduped) |
|---|---|
| SUCCESS | ≥ 680 (≥2× baseline) |
| PARTIAL | 408–679 (1.2–2×) |
| FAILED | < 408 — finding: the gates are subsystem-bound; drivers refused wholesale; pivot to census before any solve-scaling |

## Endpoint B — solve rates on the frozen harvest

Bands derive from Run 2's measurements (containers 86%, efftrace 98%, alloc
100%, readers 57%) minus a pre-declared tree-wide unknown-territory margin:

| endpoint | SUCCESS | PARTIAL | FAILED |
|---|---|---|---|
| combined solve rate | ≥75% | 60–75% | <60% |
| containers / efftrace / alloc each | ≥70% | 50–70% | <50% |
| readers | ≥50% | 35–50% | <35% |
| model spend | ≤$5 (HARD cap — the ladder stops, never overdrafts) | — | cap hit with <50% of worklist attempted = economics finding |

Wall-clock: REPORT-ONLY this time (laptop sleep makes it unownable; the
checkpoint design is the mitigation, and resume-count is reported).

## Invariants (unchanged from Run 2 — any breach = sweep FAILED)

1. Zero false passes.
2. Negative controls 9/9 (the run2.sh set) run and recorded BEFORE the solve
   phase starts, at the frozen HEAD.
3. No mid-sweep gate/harness/oracle/sweep-harness edits.
4. Accounting: every harvested (file, fn) has a verdict or a logged
   skip-reason at solve end (file-qualified keys). Audit before grading.
5. Scope flags reported per solve; % of solves per flag in the report.

## Rationalization guards

- Harvest counts are whatever the frozen gates produce — no re-running a gate
  because its number "looks wrong."
- If harvest and solve disagree on a fn (harvested but prepare-refuses), it is
  a MISS in the denominator, logged by class — not dropped.
- Endpoint A and B are graded independently; a great solve rate on a failed
  harvest (or vice versa) is reported as exactly that, not averaged into a
  narrative.
- Solve-phase resumes (after sleep/interrupt) are allowed and counted; a
  resume is NOT a new attempt quota — the ladder's per-fn retry policy is
  whatever the frozen code does in one pass over the worklist.

## Decision rules (pre-committed)

- **A SUCCESS + B SUCCESS** → tree-wide numbers become the new baseline; next
  lever is workers (infra/), not wideners.
- **A SUCCESS + B PARTIAL/FAILED** → the machine doesn't transfer to the tree
  population: census the miss classes, ONE fix cycle, re-solve misses only.
- **A FAILED** → tree-wide widening isn't the lever; write the census of what
  drivers trip, decide widen-vs-pivot BEFORE spending solve budget beyond the
  controls (solve MAY still run on the harvest for information, budget-capped).
- **Any invariant breach** → freeze, restore, demonstrate, only then continue.
