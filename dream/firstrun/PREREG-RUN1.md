# PRE-REGISTRATION — Run 1, the first full-scale experiment

Committed BEFORE launch so the outcome is judged against criteria we cannot
quietly move. If a number below is missed, the run is reported as PARTIAL or
FAILED even if a later census explains the miss beautifully — explanations
feed Run 2, they do not re-grade Run 1.

Frozen at commit `36bab4b` (2026-08-04). Any change to a reach gate, harness,
oracle, or workload generator after launch INVALIDATES the affected phase for
this run (it may re-run under the amended pre-reg of Run 2).

## The run

One `overnight.py` invocation (sharded across available workers is fine — the
criteria are worker-count-independent):

    READERS=1 CONTAINERS=1 EFFTRACE=1 ALLOCMODEL=1 PHASE2=1
    N_LEAVES=200  BUDGET_CAP=5.00  RUNTIME_CAP_H=48

## Frozen denominators

| phase | worklist | n |
|---|---|---|
| struct-readers | `structdiff/reach_accepted.json` | 78 |
| container-ADT | `container_adt/reach_accepted.json` | 83 |
| effect-trace | `efftrace/reach_accepted.json` | 110 |
| alloc-init | `allocmodel/reach_accepted.json` | 23 |
| **oracle total** | (dedup by (file, fn)) | **294** |
| scalar leaves | `widerun.harvest()` output, dumped at launch to `run1_harvest.json` | fixed at launch |

Solve rates are computed against THESE denominators. Prepare-refusals and
REFUSED_COVERAGE count as MISSES (reported by class, but in the denominator).
No function may be reclassified "out of scope" after launch — scope was fixed
by the frozen gates.

## Hard invariants — ANY violation = run FAILED, regardless of counts

1. **Zero false passes.** Operationalized by negative controls (below) plus:
   any solve later shown wrong by any means fails the run, not just the fn.
2. **Negative controls: 8/8 rejected, run AT LAUNCH against the frozen HEAD**
   (they exist as pinned tests; the launch script runs exactly these and
   records the output before phase 1 starts):
   - efftrace: `test_over_credit_diverges`, `test_or_for_add_mistranslation_diverges`
   - container: the wrong-list and dropped-unlink bodies in `test_container_harness.py`
   - alloc: `test_no_init_over_credit_diverges`, `test_double_alloc_diverges_on_ret`
   - readers/leaves: the structdiff wrong-field body and a hostdiff off-by-one
     leaf sabotage
   Every one must produce DIVERGE (or REFUSED_COVERAGE). 7/8 is not a pass.
3. **No mid-run gate or harness edits** (hash-checked against 36bab4b).
4. **Every miss has a logged reason.** Silent drops (fn in worklist, no
   verdict recorded) = instrumentation failure = run FAILED.
5. **Flags are reported.** The final report must state, per solve, the scope
   flags carried (locks_stripped, logging_stripped, alloc_stripped,
   kmalloc_zero_modeled, list_init_stripped) and the % of solves with each.
   A claim of "verified" without its flags is a soundness violation.

## Pre-registered thresholds

Baselines they derive from: efftrace live fire 109/110 (99%), container 72/80
(90%), alloc 23/23 (100%), pure-leaf first-attempt ~74% (wide run), weave
boot-verify ~90%+ (rings 5–7).

| endpoint | SUCCESS | PARTIAL | FAILED |
|---|---|---|---|
| oracle phases combined (n=294) | ≥ 250 solved (85%) | 205–249 (70–85%) | < 205 |
| each oracle individually | ≥ 70% of its n | 50–70% | < 50% (systematic break) |
| scalar leaves (frozen harvest) | ≥ 70% solved+verified | 55–70% | < 55% |
| phase-2 weave+boot | ≥ 90% of freestanding verified leaves boot-verified; final kernel boots | 75–90% | kernel cannot boot with the woven set after panic-localization |
| model spend | ≤ $5 | $5–15 | > $25 (economics claim falsified) |
| wall-clock | ≤ 48 h background | 48–96 h | > 1 week (throughput claim falsified) |

**Primary endpoint**: the oracle-combined row. **Secondary**: phase-2 booted
Rust-fn count (report the absolute number; no threshold — first measurement).

## Rationalization guards (what we will NOT do)

- No denominator surgery; no "preparable subset" rates in the headline.
- No counting REFUSED_COVERAGE as success ("it would have passed").
- No retro-widening a gate to convert a miss class, then re-grading.
- No dropping a slow phase mid-run and reporting the rest as "the run".
- No averaging away a failed individual oracle inside the combined number —
  the per-oracle floor stands on its own.
- Duplicate (file, fn) pairs count once.

## Decision rules (pre-committed)

- **SUCCESS** → proceed to Milestone B prep (ksdk mirror families); Run 1
  numbers become the baseline row in COSTDOWN.
- **PARTIAL** → one census-fix cycle, then Run 2 under an amended pre-reg.
  If Run 2 is also partial on the same endpoint → systematic gap: stop
  running, redesign that component before any further spend.
- **FAILED (thresholds)** → no Run 2 until root cause is written up.
- **FAILED (invariant)** → freeze everything; the invariant is restored and
  demonstrated on the fixtures before ANY further runs.
