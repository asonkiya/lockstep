# Sweep 1 — census-fix cycle 1 (union + enum wideners, re-solve)

Per PREREG-SWEEP1's decision rule (A SUCCESS + B PARTIAL → one census-fix
cycle, re-solve misses only). Frozen worklist unchanged (the 1,822 harvest);
only the mirror layer widened. Re-solves resume from the solved.json
checkpoint, so each retries only the prior misses.

## What was built (both sound, red-green, in-kernel gate re-certifies)

- **Union host-layout** (`1492f41`): union → max-member blob. Prepare-refusals
  **59+ → 1**.
- **enum-as-field → i32** (`d37a479`): the data-reranked #1 after union.
  Prepare-refusals **45 → 0**.

The pre-reg named union + const-array. The union re-solve's re-census
*re-ranked* the levers — enum (45) had overtaken const-array (~13) — so the
cycle followed the data, not the stale guess. That reranking is the loop
working as designed.

## Result: +20 solves, combined 60.5% → 61.6% — still PARTIAL

| kind | Sweep 1 | +union | +enum | Δ |
|---|---|---|---|---|
| readers | 102 | 103 | 104 | **+2** |
| containers | 335 | 340 | 343 | +8 |
| efftrace | 625 | 635 | 635 | +10 |
| alloc | 41 | 41 | 41 | 0 |
| **total** | **1,103** | 1,119 | **1,123** | **+20** |

Re-solve spend $0.82. Both wideners paid off in efftrace/containers (simpler
struct params, function mutates *other* fields) — barely in readers.

## The decisive finding: the readers wall is a hydra

Prepare-refusals dissolved for each field-type widener, but the readers
prepare-refusal TOTAL stayed ~378, and a **third** field-type refilled the
exact ~45 slot each time:

| after | new #1 readers prepare-refusal |
|---|---|
| sweep 1 | union (59) |
| +union | enum-as-field (45) |
| +enum | nested struct-by-value (45) |

Driver-reader structs are deep composites: each carries several exotic field
types, so a widener converts a prepare-refusal into the NEXT prepare-refusal,
not a solve. Solve-recovery per widener is single digits; this is the
field-level conjunction finding, now proven across two rounds.

## Decision (pre-committed spirit: stop grinding a diminishing lever)

**Stop the mirror-field-widener strategy for readers.** Two rounds prove
diminishing returns (union +16, enum +4, next predicted ~+2). The readers
driver population is a *structural* ceiling — 378 stacked-composite
prepare-refusals + 131 genuine solve-misses — not a widener-fixable gap. This
matches the two-partials principle: a second PARTIAL on the same endpoint via
the same lever means change levers, not iterate.

Where the lever actually goes next:
- **efftrace/containers/alloc already transfer at 79–100%** — the state-
  differential classes are not the bottleneck.
- The 1,123 verified translations are the asset; the standing frontier is
  **weave/integration** of the non-leaf classes into a booting kernel (Ring-3/4
  mechanism, four-digit worklist now), NOT more mirror-field wideners.
- Readers-on-drivers is documented as a ceiling: recognized here, not
  rationalized away with a third widener that the census predicts yields ~+2.

## Ratchet

**1,123 differentially-verified Rust translations banked** (+20 this cycle,
$0.82). Zero false passes; every widener re-certified by rustc + cc guards and
(at transplant) the in-kernel BUILD_BUG_ON.
