# PREREG — BUILD_FAIL_RS emission-correctness class (16 fns)

Frozen 2026-08-11T17:30:10, BEFORE the run. Graded in REPORT.md.

**Frozen denominator:** 16

## Blind bars

- **realize>=11**: >=11 of the 16 in-scope fns MATCH after emission fixes (shrinkage-aware; genuinely-unemittable shapes refused by name)
- **zero-diverge**: zero unexplained DIVERGE across the full 635 re-pass
- **non-decreasing**: all prior census totals non-decreasing (byte-identical emission for untouched fns)
- **negctl**: a compile-clean sabotage on each new emission path DIVERGEs (not merely BUILD_FAILs)

## Required negative controls

- anchor_neg: corrupt a negative-literal store value (+1) on a fixed fn -> DIVERGE
- param-rename: alpha-rename is behavior-preserving; a wrong-field store on a renamed-param fn -> DIVERGE
- match-arm return: corrupt the returned constant -> DIVERGE

Sub-shape census (measured before fixes): E0600 unary-minus-on-unsigned (3), E0614 param/body name collision (5), reserved-keyword param priv/in (6), _labelize match-arm return over-capture (2). All emission bugs, not reach bugs. r#priv/r#in confirmed compilable — parent-context claim of no-raw-form was wrong.
