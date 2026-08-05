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
A4 lift proofs: 13 PROVEN (lift equivalence + panic-freedom over the FULL
domain), 1 PANIC_RISK, 0 LIFT_FAILED, 0 error
```

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
(rustc's default outside debug), so today they WRAP, matching the C (the kernel
builds `-fno-strict-overflow`). The risk is **latent, not shipped**: it becomes
a hang for anyone building these objects with overflow checks or debug
assertions. The correct fix is to make the transpiler emit explicitly wrapping
arithmetic so the semantics are pinned regardless of build flags — queued, not
silently ignored.

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
