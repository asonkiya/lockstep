# Differential-oracle harness — manufacturing the missing oracle

The research pass (`dream/RESEARCH.md`) found the binding constraint on the whole
dream: ~73% of the kernel (mostly drivers) has **no functional oracle**, so the
only gate is "still boots, no new KCSAN" — which certifies *didn't crash*, not
*is correct*. A transplant that returns wrong data but doesn't crash sails
through. This harness manufactures the missing oracle from the code's own past:
**the C original is the spec.**

## Method

Both implementations are linked into one kernel — the C original (`diff_ref.c`,
symbols suffixed `_ref`) and the candidate transplant (the model's Rust). A fixed
64-op script (settime / adjfine / adjtime / gettime, values chosen to stress the
rewritten arithmetic) runs against each from identical state over a
**deterministic cyclecounter** (a fixed-step counter, not `ktime_get_raw` — so
there is zero real-time nondeterminism). Every observable — each gettime return,
and `tc->nsec` / `cc->mult` after every op — is recorded into a trace vector. The
two vectors must be **bit-identical**. `dream/diffgate/gate.sh`, two legs:

| leg | candidate | result |
|-----|-----------|--------|
| correct | the M4-verified winner | `DIFF_PASS` — `ref_hash == cand_hash` (0x125c7c5e7ec63593), firstdiff=−1 |
| wrong | a behaviorally-wrong-but-non-crashing variant | `DIFF_FAIL` at `trace[13] op#4 field=nsec ref=1002432 cand=1003432` |

## Why this is the point

The wrong candidate (`mutate.py adjtime-drift`: each adjtime adds an extra +1000 ns)
is **not a crash and not a data race** — it's just *wrong*. It **booted cleanly and
ran to the late_initcall probe** (t=160 s), so the weak gate (boot-survival +
KCSAN) would have **passed** it. The differential oracle **rejected** it, and
localized the divergence to the exact operation and field:

```
DIFF_PROBE: ops=64 ref_hash=0x125c7c5e7ec63593 cand_hash=0xcc5c5d1623fa5c93 firstdiff=13 verdict=DIFF_FAIL
DIFF_PROBE: at trace[13] op#4 field=nsec ref=1002432 cand=1003432
```

That gap — boots fine, but provably wrong — is exactly the ~73% of the kernel
this capability exists for. It converts a driver transplant from "didn't crash"
to "behaves identically to the C it replaced," with a pinpointed counterexample
when it doesn't.

## What it costs to manufacture, per subsystem

The reusable machinery is the probe pattern (deterministic input source +
trace-and-hash + both-impls-linked). The per-subsystem cost is: (1) a deterministic
stand-in for the subsystem's nondeterministic inputs (here, the cyclecounter;
for a real device, a recorded/mocked register-read trace), and (2) an operation
script covering the surface. Both are far cheaper than writing a correctness spec
— which is the whole appeal: the spec already exists, as the C.

## Honest limits

- The oracle proves *equivalence to the C original*, not *correctness in the
  abstract* — if the C had a bug, a faithful transplant reproduces it (which is
  the correct behavior for a transplant).
- It requires the subsystem's nondeterminism to be controllable (mockable clock /
  recorded I/O). Purely reactive, hardware-timing-dependent code is harder and
  may fall back to the weak gate.
- Coverage is only as good as the op script; it is a differential *test*, not a
  proof. But it is a strong, cheap, per-subsystem test where none existed.

## Status: the load-bearing capability is proven

- Verified transplant accepted (bit-identical trace to the C original). ✅
- Wrong-but-non-crashing transplant REJECTED with a localized counterexample,
  where boot-survival + KCSAN would have passed it. ✅
- This is the tool that makes the driver mass (the bulk of the dream) meaningfully
  gateable, not just weakly attested.
