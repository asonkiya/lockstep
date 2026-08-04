# PRE-REGISTRATION — Run 2 (amended per RUN1-REPORT.md decision rules)

Committed before launch. Amendments over Run 1's pre-reg, each traceable to a
Run-1 finding; everything not amended carries over from PREREG-RUN1.md.

## Amendments

1. **Invariant 4 restored** (Run 1's failing breach): checkpoint keys are
   file-qualified (`_key(kind, rel, fn)`, overnight.py) — same-named fns in
   two files each get a verdict. Demonstrated by `dream/tests/test_run_keys.py`
   on the exact Run-1 collision pair (`cache_contiguous`).
2. **The runner is frozen too.** `run1.sh` step definitions (with
   `N_LEAVES=0` on oracle steps) are part of the frozen surface; any mid-run
   edit to it invalidates the affected step.
3. **Cold start.** Run 2 archives `progress.json` + `verified/` before prep
   (fresh replication; Run-1 artifacts kept under `run1-archive/`).
4. **Leaves get a real front gate** (Run 1: 2/72 against an unscoped
   harvest): phase 1C now admits only purity-pure, TU-liftable leaves
   (purity.classify + hostdiff.tu_compiles, which learned the file's own
   include dir). Scoped denominator measured today: **7** — frozen at launch
   by the prep dump.
5. **Readers denominator = prepare-passing subset.** Run 1 exposed
   reach/harness incoherence: 40 of 78 could never build any candidate.
   The census-fix cycle closed the general classes (raw-ident keyword fields;
   errno/min/round_up/limits in PRELUDE; kernel-constant define resolution
   with front-gate refusal; file-scope-array refusal; duplicate mirror
   dedupe). prepare() is now the second gate layer; the prep step freezes
   `run2_readers.json` = the prepare-passing subset (expected ≈ 40).
6. **Negative controls**: same 8, plus one addition — a readers sabotage on a
   function from the newly-fixed classes (bitmap_check_region wrong-body →
   must DIVERGE), 9/9 required.

## Frozen denominators (final counts stamped by the prep step at launch)

| phase | worklist | n |
|---|---|---|
| readers | `run2_readers.json` (prepare-passing subset of the 78) | ≈40, frozen at prep |
| containers | `container_adt/reach_accepted.json` | 83 |
| efftrace | `efftrace/reach_accepted.json` (file-qualified keys) | 110 |
| alloc | `allocmodel/reach_accepted.json` | 23 |
| leaves | scoped harvest dump | ≈7, frozen at prep |

## Thresholds

Unchanged rules, re-derived numbers:

| endpoint | SUCCESS | PARTIAL | FAILED |
|---|---|---|---|
| oracles combined (n ≈ 256) | ≥85% | 70–85% | <70% |
| each oracle individually (incl. readers on its new denominator) | ≥70% | 50–70% | <50% |
| leaves (n ≈ 7) | ≥5 | 3–4 | ≤2 |
| phase-2 boot | ≥90% of freestanding verified; kernel boots | 75–90% | no boot |
| spend | ≤$5 (step caps sum $4.50) | $5–15 | >$25 |
| wall-clock | ≤48 h | 48–96 h | >1 week |

Two-partials rule is ARMED from Run 1: a PARTIAL on the combined-oracle
endpoint here means stop and redesign, per the pre-committed rule.

## Rationalization guards

All of Run 1's, plus: no counting Run-1 checkpointed solves (cold start); the
readers denominator shrink is pre-registered HERE, before results — any
further shrink after launch is denominator surgery and voids the endpoint.
