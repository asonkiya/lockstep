# Ring 5 — the fleet loop: automation, parallel synth, one-boot verification

Rings 0–4 set up each transplant by hand. A full-kernel rewrite is not hand-work;
it is a **loop**: take a worklist of real functions, synthesize them in parallel,
generate one batch probe, verify the whole fleet in one boot, report. Ring 5 is
that loop running as a driver (`fleet.py`) — the wall-clock levers the research
called decisive (parallel synth + batched boot), made concrete.

## The fleet

Six real, exported, pure lib functions — two fresh transplants (`lcm`,
`lcm_not_zero`, which inline their own `gcd`) plus four re-verified through the
same automated loop (`int_pow`, `gcd`, `int_sqrt`, `__sw_hweight32`).

## Parallel synthesis — one wall-clock round-trip for the whole fleet

`fleet.py synth` fires all six model calls concurrently (`ThreadPoolExecutor`):

```
✓ cgir_lcm ($0.0010)  ✓ cgir_lcm_not_zero ($0.0015)  ✓ cgir_int_pow ($0.0009)
✓ cgir_gcd ($0.0006)  ✓ cgir_int_sqrt ($0.0009)       ✓ cgir_sw_hweight32 ($0.0005)
synth: 6/6 compiled, $0.0054
```

Six transplants for half a cent, in the time of one. Model generation is not the
bottleneck and never was — this is the point the cost model made, running.

## One boot verifies the whole fleet — and catches a bad transplant

`fleet.py gate` compiles all six candidates into one kernel (panic handlers
localized — the N-object fix from Ring 1), generates a single probe that compares
each `cgir_*` against the live exported kernel symbol over wide input ranges, and
boots ONCE. First pass:

```
FLEET_PROBE: lcm          n=160801 bad=0     DIFF_PASS
FLEET_PROBE: lcm_not_zero n=160801 bad=0     DIFF_PASS
FLEET_PROBE: int_pow      n=1365   bad=0     DIFF_PASS
FLEET_PROBE: gcd          n=160801 bad=0     DIFF_PASS
FLEET_PROBE: int_sqrt     n=40001  bad=39772 DIFF_FAIL   <- caught
FLEET_PROBE: __sw_hweight32 n=200000 bad=0   DIFF_PASS
FLEET: 5/6 passed, 1 failed
```

**This is the most important result in Ring 5.** The parallel-synthesized
`int_sqrt` candidate chose a *different* sqrt algorithm (`y+m <= x/m`) that is
subtly wrong — `int_sqrt(4)` returns 1, not 2 — and the fleet gate **rejected it**
while accepting the five correct transplants. At full-kernel scale a fraction of
transplants will be wrong; the gate's job is to never let one through, and here it
did exactly that, from `x=4`, over one shared boot.

## The retry loop — caught, fed back, recovered

The loop's response to a rejection is to re-synthesize the failure with the
counterexample as feedback. Re-prompted with the kernel's exact algorithm (and
"int_sqrt(4) must be 2, not 1"), the model produced the correct transplant
($0.0012), and the re-gate is clean:

```
FLEET_PROBE: int_sqrt n=40001 bad=0 DIFF_PASS   -> FLEET: 6/6, RING 5 FLEET GATE: PASS
```

Synthesize → gate → catch → feed back → re-gate: the actual shape of an
autonomous rewrite loop, not a one-shot. Every function verified against the C it
would replace, in single ~5-minute boots. That is the batching lever: the
expensive step (boot) is amortized across the whole fleet, and fleet size is
bounded by link/compile, not by boots.

## A workflow principle Ring 5 surfaced

The first gate attempt failed to *build*: three of the fleet functions
(`int_pow`, `int_sqrt`, `hweight32`) were still woven into the volume from Rings
0–2, so their `cgir_*` seams and the C originals were gone — you cannot
differentially test a function against a C reference that has been replaced by
Rust. The fix and the principle: **gate on a pristine tree; weaving is a separate,
later step.** The volume is a scratch build cache; the manifest is the ratchet's
truth, and `weave.py apply` reproduces the woven kernel from pristine on demand.

## Why this is the shape of the whole job

The research's wall-clock math was: naive one-boot-per-function is ~800 days;
batches of N with parallel workers is days. Ring 5 is one batch of that pipeline,
automated end to end: worklist in, parallel synth, one boot, per-function
verdicts out. Scaling to the whole kernel is more worklist rows and more parallel
QEMU workers — the same loop, wider. Nothing about the mechanism changes; only the
count.

## Status

- The pipeline runs as an automated fleet loop, not hand-setup. ✅
- 6 real functions synthesized in parallel for $0.0054, one wall-clock round-trip. ✅
- Whole fleet verified against the kernel's own symbols in ONE boot. ✅
- The gate CAUGHT a wrong parallel-synth (int_sqrt, from x=4) and the loop
  recovered it via counterexample feedback — selectivity + retry, the real loop. ✅
- Two fresh transplants (lcm, lcm_not_zero); batching + parallel-synth levers at
  fleet scale — the shape a full run repeats, wrong candidates and all. ✅
