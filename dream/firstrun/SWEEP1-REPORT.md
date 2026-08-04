# SWEEP 1 — graded against PREREG-SWEEP1.md

Tree-wide harvest + solve, local M2 Max, 2026-08-05. Code frozen at `48925c4`
throughout (the only later commit before results, `525c8fa`, is the pre-reg
document itself). Harvest ~3 h over 28,679 files; solve ~2.5 h, 0 resumes.

## Endpoint A — coverage growth: **SUCCESS**

**1,822 gate-admitted functions tree-wide** vs the ~340 baseline — **5.4×**
(blind bar: 2×). Per gate: readers 613 (7.3×), efftrace 744 (6.8×),
containers 424 (5.1×), alloc 41 (1.8×). The gates transfer to the unsampled
driver mass far better than the conjunction history predicted.

## Endpoint B — solve robustness: **PARTIAL** (combined), split verdicts

| endpoint | measured | band |
|---|---|---|
| combined | **1,103/1,822 (60.5%)** | PARTIAL (60–75) |
| containers | 335/424 (79%) | SUCCESS |
| efftrace | 625/744 (84%) | SUCCESS |
| alloc | **41/41 (100%)** | SUCCESS |
| readers | 102/613 (16.6%) | **FAILED** (<35%) |
| spend | **$1.78** of the $5 hard cap | SUCCESS |
| wall-clock (report-only) | ~5.5 h total, 0 resumes | — |

Ladder split: 610 local ($0) / 493 Haiku. Scope flags on solves:
locks_stripped 220, alloc_stripped 129, kmalloc_zero_modeled 5.

## Invariants — all five INTACT

Controls 9/9 rejected and recorded before solve (`sweep/controls.txt`); no
mid-sweep code edits; **accounting audit: 0 of 1,822 without a verdict line**
(the file-qualified keys held at 6× scale); flags reported above.

## The finding (what PARTIAL decomposes into)

Three of four oracles TRANSFER to the tree population at or above their
curated-corpus rates — containers 79% (Run 2: 86%), efftrace 84% (98%),
alloc 100% (100%). The state-differential machinery is population-robust.

Readers is the entire gap: 511 misses = ~378 prepare-refusals (the mirror
still can't lay out driver structs: **unions 59+**, file-scope lookup tables,
cross-header constants like V4L2_PIX_FMT_*, exotic field types) + 133 genuine
ladder misses. This is a struct-layout coverage wall, not a differential or
model wall — the same census classes as the minimal corpus, at driver scale.

## Decision (pre-committed rule applied)

A SUCCESS + B PARTIAL → **one census-fix cycle, then re-solve misses only.**
The census names the cycle: union host-layout is now measurably the #1 single
class (59+ in readers alone; also #1 in the mirror-factory registry). Second:
file-scope const-array emission (the match_token class, now at scale). Both
were already on the factory backlog — the sweep converts them from "backlog
items" to "measured, ranked levers."

## The ratchet number

**1,103 differentially-verified Rust translations of real kernel functions**
now banked from one $1.78, half-day, single-laptop sweep — 3.7× the entire
Run-2 count. Boot-weave composition and the in-kernel gate remain the next
integration step for the non-leaf classes (the standing weave-engineering
frontier), and the worklist for it is now four-digit.
