# PREREG — tok_guard (5) — confirm/refute hygiene

Frozen 2026-08-12T11:32:06, BEFORE the run. Graded in REPORT.md.

**Frozen denominator:** 5

## Blind bars

- **refuse-sound**: all 5 refuse-by-name with a SOUND reason (0 realized expected; break-variants are unsound to model as delete-all under the duplicate-token arena)
- **reasons-precise**: each refusal reason names the true blocker (or the imprecision is documented)
- **totals-nondecreasing**: container totals unchanged T2 180/184 + T3 109/131 = 289 (no realizer change)

## Required negative controls

- none new — this slice realizes nothing; the negative control is the existing tok arena proving break-variant != delete-all under duplicate tokens (test_tok_dropped_guard_diverges)
