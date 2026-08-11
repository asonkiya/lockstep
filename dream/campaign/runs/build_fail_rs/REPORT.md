# REPORT — BUILD_FAIL_RS emission-correctness class (16 fns)

Graded 2026-08-11T17:44:57 against the frozen PREREG. **SUCCESS** (4/4 bars).

| bar | pre-registered | measured | verdict |
|---|---|---|---|
| realize>=11 | >=11 of the 16 in-scope fns MATCH after emission fixes (shrinkage-aware; genuinely-unemittable shapes refused by name) | 16/16 frozen in-scope MATCH (+fabric_event_destroy laddered from cross-slot = 17 total rescued); no shrinkage — emission-correctness class | PASS |
| zero-diverge | zero unexplained DIVERGE across the full 635 re-pass | 0 DIVERGE / 0 BUILD_FAIL_RS across 635 | PASS |
| non-decreasing | all prior census totals non-decreasing (byte-identical emission for untouched fns) | MATCH 604 -> 621 (=604 prior +17); 14 refusals unchanged; full test_realize byte-identical (1 stale-name assumption in the collision detector refined, not a pin edit) | PASS |
| negctl | a compile-clean sabotage on each new emission path DIVERGEs (not merely BUILD_FAILs) | 3/3 compile-clean negctls DIVERGE: negative-literal +1, param-rename wrong-store, match-arm return +1 | PASS |

## Sub-shape census (measured before fixes; taxonomy lesson — the name is a bucket)

| rustc error | n | root cause | fix |
|---|---|---|---|
| E0600 unary-minus-on-unsigned | 3 | `(-1) as u16` types the literal from the cast target | `anchor_neg`: suffix bare negative literals with `i64` |
| E0614 deref-of-i64 | 5 | node-param real name (`ud`) collides with a model `let ud`, shadowing the pointer | rename node param to `__n{pi}` on a VALUE shadow |
| reserved-keyword param `priv` | 4 | node-param name is a Rust keyword, emitted raw as the fn param | same rename (keyword trigger) |
| keyword param `in` | 2 | same, 2-node fns | same rename |
| delimiter mismatch | 2 | `_labelize` over-captured `=> return X,` across the match `}` | lower match-arm return first, bounded to the arm |

**Ladder: 16 census -> 16 in-scope -> 16 MATCH** (+ fabric_event_destroy, the cross-slot +1, also rescued: its param — not a field — was named `priv`). No refusals: an emission-correctness class has no reach wall to shrink against.

**Correction of record:** the cross-slot report claimed `priv` had "no valid r#priv form" — measured false (`r#priv`, `r#in`, `r#fn` all compile). And fabric's block was a keyword PARAM, not a field. Both dispositions were wrong; the param-rename fixes the real cause.

## Byte-identical

Every fix is a no-op off its trigger: `anchor_neg` only touches bare negative literals; the param rename only fires on a Rust-keyword name or a non-handle `let name =` value shadow (a cross-slot `let x = a0` handle alias is explicitly excluded — caught by the rq_depth_scale_up pin, which forced that refinement); the match-arm subn precedes and cannot alter the statement-return path. Full test_realize.py 26/26.

## Result

efftrace realized **604 -> 621 / 635**; funnel + ledger regenerated (BUILD_FAIL_RS extinct; new top lever unknown_const_token 11). 8 new pins. $0.

