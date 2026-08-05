# A4 — the formal rung above the differential

The differential **samples** the safety lift (a workload of field values);
Kani/CBMC **proves** it. `lift_proof.py` emits, per lifted candidate, a crate
holding the two REAL artifacts verbatim — tier-(a) (unsafe, `(*p).field`) and
tier-(b) (`#![forbid(unsafe_code)]` core + field-granular boundary) — runs both
on identical SYMBOLIC struct state, and asserts identical return and identical
post-state in every mirrored field, **for all inputs**.

This is the escalation ladder the research pass recommended (VERT's shape:
differential → PBT → bounded model checking), applied to the *lift* rather
than to the translation.

## Results (14 woven tier-b candidates)

```
A4 lift proofs: 14 PROVEN (lift equivalence + panic-freedom over the FULL
domain), 0 PANIC_RISK, 0 LIFT_FAILED, 0 error
```

(First run was 13 PROVEN / 1 PANIC_RISK; the wrapping-arithmetic fix below
closed the last one.)

Proven functions span block/, clk/, i2c/, input/, net/, soc/fsl — e.g.
`qup_i2c_clear_blk_v2` with **10 symbolic fields**, `qbman_eq_desc_set_qd`
with 4. For each: no input exists on which the safe core differs from the
unsafe original.

**The proof is non-vacuous** (pinned in `test_lift_proof.py`): sabotaging the
tier-(b) core by one makes Kani FAIL, naming the field
(`Failed Checks: "lift changed field bd_writers"`).

## The finding the differential could not make

`seqbuf_seek` came back **PANIC_RISK**, and the distinction matters, so the
tool reports the two classes separately:

- `LIFT_FAILED` — a `lift changed …` assertion failed ⇒ the transform is
  WRONG. None occurred.
- `PANIC_RISK` — the lift IS proven equivalent, but Kani's default checks show
  BOTH forms can panic on some input. Here: `attempt to add with overflow` in
  `pos + offset` over the full i64 domain.

Why this is kernel-relevant rather than cosmetic: a freestanding Rust kernel
object's panic handler is `loop {}`, so a reachable panic is a **kernel hang** —
and the weave's link-repair loop already had to drop readers whose division
paths pulled in `core::panicking`. The sampled differential never picks values
that overflow, so it structurally cannot find this class.

**Honest severity**: the woven objects are built `-O` with overflow-checks OFF
(rustc's default outside debug), so they WRAPPED, matching the C (the kernel
builds `-fno-strict-overflow`). The risk was **latent, not shipped** — it would
become a hang for anyone building with overflow checks or debug assertions.

### FIXED — wrapping arithmetic pinned in the source

`realize.wrapify` now rewrites the verified bodies' bare `+ - *` into
`wrapping_add/sub/mul` **before** the helper rewrite, so tier-(a) and tier-(b)
receive the identical transform and stay provably equivalent. Semantics are now
pinned in the SOURCE regardless of build flags. The rewriter is precedence- and
paren-aware and **conservative**: anything it cannot split confidently is
returned unchanged (the candidate stays flagged, never silently altered);
comparisons, shifts, casts and unary signs are untouched.

Verified from four sides:
- `seqbuf_seek`: PANIC_RISK → **PROVEN**;
- full Kani batch: **14/14 PROVEN, 0 PANIC_RISK**;
- full re-census of all 635 candidates: **480 MATCH — identical to before**
  (zero regressions from the rewrite);
- woven objects still compile freestanding aarch64 (5/5 sampled).
- `test_wrapify.py` (6 tests) pins precedence, associativity, the untouched
  operator classes, and the conservative no-op behaviour.

## Scope (honest)

This proves **transform equivalence** (tier-a ≡ tier-b) and panic-freedom. It
does NOT prove tier-a matches the kernel C — that is the sweep differential's
claim, which this composes with. Loop-free scalar cores are CBMC's sweet spot;
a looping core needs an unwind bound (bounded-complete — VERT's documented
limit) and is flagged `SKIP_LOOP`, never silently under-proven.

## Use

```
lift_proof.py <file> <fn>     # prove one lifted candidate
lift_proof.py batch [N]       # prove up to N woven tier-b candidates
```

## Full sweep + final shipped state (2026-08-06)

Sweep over the whole woven tier-b set: **29 PROVEN, 0 LIFT_FAILED**, plus three
follow-ups all resolved:

- `pmc_next` was still PANIC_RISK — the first wrapping fix only covered `let`
  RHS, but its arithmetic sat in a `set_field()` CALL ARGUMENT. Now wrapped at
  the emission point (the transpiler already isolates the store value) →
  **PROVEN**.
- That fix regressed two candidates (480 → 478 MATCH), caught by diffing
  against the saved pre-fix census: a literal receiver is an ambiguous
  `{integer}` and cannot take a method (`(166666 * 2).wrapping_add(1)` = E0689).
  Guarded by `_is_const_expr`; census back to **480 exactly**.
- Two CBMC timeouts are now reported as `TIMEOUT` — the claim is
  **undischarged**, never counted as a pass or a failure. Those candidates need
  an unwind bound or a longer budget.

## Shipped state (re-woven after the fix, 2026-08-06)

The booting kernel now contains the wrapping-fixed objects, not just the fixed
source: re-weave + boot green, dashboard **38 tier-b (31 realized + 7 reader) +
26 tier-a = 64**, safe-logic 32%. Verified in the woven objects themselves:
**38 carry `#![forbid(unsafe_code)]` cores and 28 carry wrapping arithmetic**,
including `seqbuf_seek`'s `(pos).wrapping_add(a1)` — the exact expression Kani
required. Full test suite: **333 passed**.
