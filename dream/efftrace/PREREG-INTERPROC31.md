# PREREG — Summit 3.1: the opaque-callee discharge measurement

Written 2026-08-11, BEFORE any measurement runs. This is a measure-first slice
(STRATEGY §Summit-3.1): no integration gets built until the numbers earn it.

## The question

Of the **5,339** bounded_state fns refused on `unresolved (external callee)`
(INTERPROC_RESULTS.md, corpus of 38,400 core bodies), how many discharge with:

- **(a) corpus completeness** — callee bodies that exist as header inlines the
  corpus never ingested;
- **(b) an annotation table** — curated known-pure / known-bounded kernel APIs
  (memset/memcpy, ktime helpers, bitops, ...);
- **(c) CGIR call-graph/effects edges** — the re-entry candidate. CGIR earns
  integration ONLY for what it discharges **beyond** (a)+(b): if lockstep's own
  corpus extension gets the same fns, CGIR stays a separate product.

Secondary (free): does anything in the 7,664 genuinely-unbounded bucket
reroute under the annotation table? Expectation: ~none — record it.

## Method (in order; each step persisted)

- **M1** — dump the unresolved-callee NAMES with per-fn attribution; tally by
  frequency. Expectation (Zipf): the top ~100 callees account for a large
  share of blocked fns. Pure counting; $0.
- **M2** — classify the top callees by hand + script: header-inline vs
  true-external vs annotatable-API vs genuinely-opaque. This yields the
  ceiling for (a) and (b) before any closure re-runs.
- **M3** — re-run the interprocedural closure with (a)+(b) applied; count new
  BOUNDED. Then (c): CGIR (installed, 0.6.3) over the same corpus question —
  call-graph edges + effect kinds — counting only *additional* discharges.

## Soundness control (measured before M3 counts anything)

Annotation-table self-consistency: every table entry whose body IS in the
corpus must agree with the computed syntactic footprint; and a deliberate
poison entry (`kmalloc` annotated pure) must be CAUGHT by that check — the
validator has to be seen to fail. No table ships without this.

## Pre-registered bars (blind — written before M1)

Total newly-BOUNDED from (a)+(b)+(c), against the 1,230 baseline:

- **SUCCESS: ≥ +800** (→ ≥2,030 bounded, ~5% of core): build the integration
  (annotation table as a standing artifact + CGIR wiring if (c) carried ≥300
  of it).
- **PARTIAL: +300–799**: ship the annotation table alone; CGIR wiring only if
  (c)'s marginal share ≥ half the total; otherwise named LEVER-THIN.
- **LEVER-DEAD: < +300**: document in INTERPROC_RESULTS.md, close Summit 3.1,
  move the effort to 3.2 (in-kernel state differential). Negative results are
  deliverables.

Census-shrinkage law applies: M2's classified ceiling will exceed M3's
realized discharge (expect 2–5×); report the ladder, not the ceiling.

## Expectations on record

INTERPROC_RESULTS.md already predicts "low thousands, not tens of thousands."
The honest prior: (a) is the bulk (header inlines are the known corpus gap),
(b) is a few hundred, (c) is the open question this slice exists to answer —
CGIR's effects layer classifies KINDS, not location footprints, so its
marginal value should show up in callee *resolution*, not footprint precision.
If (c) ≈ 0 beyond (a)+(b), the contract-as-oracle lesson (LESSONS.md) closes:
CGIR remains scaffolding, honestly retired from the dream's critical path.
