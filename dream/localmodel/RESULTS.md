# Local models through the hostdiff oracle — what $0 synthesis is actually worth

COSTDOWN §3 claimed that with sound gates, synthesizer quality is a wall-clock knob,
not a correctness knob — so a free local model should convert real kernel functions
with zero external spend, just more retries than Haiku. This run measures it. Loop =
Ring 5's (synth → verify → counterexample retry), with **hostdiff replacing the boot**:
every reject is diagnosed in under a second, so a weak model's retries cost nothing.

Battery: 8 real kernel functions — the 7 boot-verified fleet fns (gcd, int_sqrt,
int_pow, __sw_hweight32/64, lcm, lcm_not_zero) + intlog2 (table-driven fixed-point
log, the class Haiku also missed first-try in the fleet runs). Models: qwen2.5-coder
7B and 14B via ollama on the M2 Max, temperature 0, ≤4 attempts. Oracle: hostdiff,
~500k differential cases per verify vs the REAL kernel C.

## Scorecard

| | qwen2.5-coder:7b | qwen2.5-coder:14b | Haiku (measured, fleet/widerun) |
|---|---|---|---|
| solved (≤4 attempts) | 4/8 | **5/8** | ~all of this class (with retry) |
| first-pass | 3/8 (37.5%) | **5/8 (62.5%)** | ~74% |
| wall-clock, whole battery | 143 s | 202 s | — (API latency) |
| external cost | **$0.00** | **$0.00** | ~$0.0014/fn |
| verify cost per attempt | ~0.3–0.9 s | ~0.3–0.9 s | 247 s (boot) before hostdiff |

The 14B solved everything it solved **first-pass** (its retries never rescued a
failure — more on that below). 62.5% free first-pass vs 74% paid is the headline:
**a $0 local model is ~85% of a Haiku on this class**, and every function it lands
is a function that never touches the API.

## Failure taxonomy (the interesting part)

The failures are not random — they're three crisp classes, identical across both
model sizes:

1. **C operator transliteration** (gcd, both sizes, all attempts): `r & -r` copied
   verbatim → `E0600: cannot apply unary operator - to u64`. The prompt states the
   `wrapping_neg()` rule, the compiler error names the line, and the model *still*
   re-emits `-r` for 4 rounds. Feedback does not fix what the model can't see as
   wrong.
2. **Width discipline** (int_sqrt, both sizes): `leading_zeros()` returns `u32`,
   mixed into `u64` expressions (`no implementation for u32 & u64`). Same
   feedback-immunity.
3. **Structural/long-output** (intlog2, both sizes): the 256-entry table + fixed
   point interpolation → truncated or malformed output (`NO_EXPORT`, rustc fails).
   This is also Haiku's fleet-miss class (intlog10) — it's a hard-function
   signature, not a local-model artifact.

What retries DID fix (7B): the typed-literal error (`let mut result = 1;` +
`wrapping_mul` → E0689) — int_pow converged on attempt 2 after the rule + error
feedback. What scaffolding fixed outright: the subtraction-gcd hang (attempt-1 HANG
in the first run) became first-pass PASSes after one generic prompt rule (O(log)
helpers / remainder-based Euclid).

And the oracle earned its keep beyond compile errors: the 7B once emitted a helper
**named `lcm` that computes gcd** — `lcm_not_zero(1,2)=1` instead of 2. Plausible,
compiles, wrong; hostdiff killed it in 0.68 s with the counterexample. Zero false
passes in ~50 verify runs across both benches.

## What this means for the ladder (COSTDOWN §3, now with data)

- **The ladder works, and the middle rung is real.** 14B lands ~60% of the turnkey
  class at $0/token, seconds per attempt, entirely on owned hardware. Those never
  hit the API.
- **The persistent failure classes are exactly the deterministic rung's domain.**
  `-x`-on-unsigned and width coercion are things **c2rust gets right by
  construction** (it models C's integer semantics; it doesn't "forget" a rule).
  gcd/int_sqrt — the local models' stuck cases — are trivially within a
  transpiler's reach. Ladder order confirmed: c2rust (operator/width semantics
  mechanical) → local 14B (idiom, naming, structure) → API model (the intlog2-class
  tail).
- **Retry-feedback has a ceiling for small models.** They fix what they understand
  (typed literals) and loop on what they don't (unary minus). Don't budget many
  retries per rung — escalate after 2, the next rung is cheap anyway.
- **Verification is no longer a cost anywhere in the loop.** Both full benches —
  8 functions × up to 4 attempts × ~500k differential cases each — finished in
  2–3.5 minutes *total* on the laptop. The same benches under boot-gating would
  have been ~2–5 *hours*.

## Honest scope

- n=8, one function family (pure lib/math + bit ops) — the turnkey Tier-A class,
  which is exactly where the ladder's free rungs are supposed to operate. Struct/
  driver classes escalate by design (mirrors/recorder + stronger models).
- Haiku's 74% is from a different-but-comparable battery (widerun's 72 scalar
  leaves); not a same-battery A/B. Close enough for routing decisions, not for a
  paper.
- qwen2.5-coder was the only family tested; qwen3-coder / deepseek-coder may move
  the free rung up. The harness takes `--model X` — testing another is one command.

## Files

- `localbench.py` — the bench: worklist, prompt (with the generic C→Rust pitfall
  rules that fixed the fixable classes), ollama loop, hostdiff verify, retry with
  compiler-error/counterexample feedback. `results_7b.json` / `results_14b.json`.
- Companion: `../hostdiff/` (the oracle that makes all of this free).
