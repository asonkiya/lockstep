# The ladder at n=72 — where the host tier's reach actually ends

The 8-function battery (`RESULTS.md`) measured the ladder's *shape*: c2rust swept
it 8/8 at $0. This run points the same ladder at the widerun's **72 tree-wide
harvested functions** — scalar exported leaves from `lib/`, `kernel/`, `mm/`,
`fs/`, `pci/`, `net/`, `sound/`, `block/` — to measure its *rates* on an
unbiased sample. The result is not a synthesis number. It's a map of a wall.

## Result

```
WIDERUN-72 LADDER: 7/72 solved | c2rust 7 | local 0 | haiku 0
  sound T0 verified (pure)                     : 5
  host-attested (quarantined; trace oracle owed): 2
  unverifiable on host (shim/header gap)        : 64
  genuine synthesis miss (TU compiled, RUSTC_FAIL): 1
  spend $0.0036 | wall 31s
```

The lone `RUSTC_FAIL` is the honest case: its TU *does* compile (it passed the
precheck), so it was correctly escalated — the candidate just didn't build. That
is the only function of 72 where a rung genuinely tried and failed; the other 64
were never host-verifiable to begin with.

The 7 solved (all by the deterministic rung):

| function | status | why |
|---|---|---|
| `__sw_hweight8/16/32` | verified_T0 | pure bit ops, self-contained TU |
| `int_pow`, `int_sqrt` | verified_T0 | pure math, self-contained TU |
| `gcd`, `intlog10` | host_attested | matches the shim-pinned C, but the purity router quarantines them (static-branch / table read) — they owe the in-kernel oracle, so NOT counted as soundly verified |

## The finding: the bottleneck is shim coverage, not synthesis or the model

All 65 unsolved failed for the **same reason**, and it is not the translator:
the real kernel TU **does not compile standalone on the host** with the minimal
shim. They `#include <trace/events/*.h>`, subsystem headers, and types the shim
(`hostdiff/kshim.h`) doesn't define. c2rust failed to transpile the identical 65
for the identical reason — a transpiler also needs a TU that preprocesses.

So `hostdiff` cannot build the *reference* for these functions at all. No
candidate — from any rung, correct or not — can be verified, because there is
nothing on the host to diff against. The 7 that solved are precisely the
`lib/math` + bit-ops TUs that are self-contained enough to compile with a small
shim.

**This re-proves the oracle wall from a new direction.** The census said ~89%
reachable; that's *synthesizable*. This says: the **boot-free host tier's**
verifiable reach is much narrower — roughly "functions whose translation unit
compiles standalone with a small shim," which is a `lib/`-shaped minority. The
driver and subsystem mass still needs either (a) the in-kernel differential
gate (`dream/diffgate/`, which compiles inside the real tree, include graph and
all), or (b) real per-subsystem investment growing the shim. Boot-free
verification is a **`lib`-tier accelerator, not a whole-kernel one** — exactly
what the tier table in `docs/architecture.md` predicts.

## The $0.25 lesson (now fixed in the harness)

The first pass had no verifiability precheck: it escalated all 65 shim-gap
functions to the local model *and then Haiku* (2 attempts each), every one
returning `CC_TU_FAIL` — because the reference TU never compiled, not because
the translation was wrong. That burned **$0.2491** proving nothing.

The fix (`host_tu_ok` precheck in `widerun72.py`): before spending a rung, try
to compile the shimmed TU. If it fails, the function is `UNVERIFIABLE_HOST` and
**all rungs are skipped, nothing spent on it**. The rule generalizes: **order
the verifiability gate before the paid generator.** A model can't rescue a
function the oracle can't judge, so don't pay it to try. Measured effect, same
72 functions: **$0.2491 → $0.0036 (69×), 1574s → 31s (51×)**, identical 7
solved. The $0.0036 that remains is the single genuinely host-verifiable
function that reached a rung and failed — exactly what you *do* want to pay for.

## What this says about the cost model

It sharpens, not contradicts, `COSTDOWN.md`. The token bill was never the
constraint — here the *entire* wasted spend chasing 65 functions was a quarter.
The constraint is oracle coverage: to verify the driver/subsystem mass you
invest in shims (host tier) or boots (kernel tier), and *that* is the
worker-hours line, not tokens. The ladder does exactly what it should — sweeps
the self-contained pure leaves for free — and honestly reports where it can't
reach.

## Files

`widerun72.py` (re-harvests the 72 via `widerun.harvest()`, routes by
`purity.json`, precheck-gates, runs the ladder, labels verified_T0 vs
host_attested vs unverifiable), `widerun72_results.json` (per-function machine
record). Companion: `RESULTS.md` (the n=8 shape), `../COSTDOWN.md`,
`../SWEEP.md` (the census this samples).
