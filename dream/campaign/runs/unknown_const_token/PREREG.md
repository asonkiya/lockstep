# PREREG — unknown_const_token — comment/string false positives

Frozen 2026-08-11T18:15:11, BEFORE the run. Graded in REPORT.md.

**Frozen denominator:** 11

## Blind bars

- **realize>=9**: >=9/11 fns MATCH after the comment/string-strip fix
- **no-diverge**: zero unexplained DIVERGE across the re-pass
- **floor**: efftrace realized non-decreasing (>=604 floor; was 621)

## Required negative controls

- injected live ALL-CAPS token still raises unknown_const_token (guard not over-weakened)
- compile-clean +1 store on a realized fn -> DIVERGE

Census (uct_census/uct_live): all 11 refusals were ALL-CAPS tokens in COMMENTS or STRING literals (WARN_ONCE narration, println! kernel logs, `// ...START_REG...`). No genuine live unresolved const in the set; live value-consts (FIELD_WIDTH_MAX, TAS2781_YRAM2_*) are resolved #defines already in rec['defines']. Fix: _strip_noncode before the guard scan. Bars written before the differential re-pass ran.
