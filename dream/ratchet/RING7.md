# Ring 7 — parallel workers: the last wall-clock lever

The research's cost model reduced the whole dream to one bottleneck: verification
wall-clock, with two levers — **batching** (Rings 2/5, many functions per boot)
and **parallel workers** (independent build+boot pipelines, concurrently). Ring 7
exercises the second: two pristine kernel volumes, the fleet split into two
batches, both built and booted at the same time.

## Setup

`parallel.py` restores `cgir-kbuild` to pristine, clones it once to
`cgir-kbuild-w2` (~3.7 GB, one-time), and restores that too — two independent
worker trees. The Ring 5 fleet is split:

- **Worker A** (`cgir-kbuild`): `lcm`, `int_pow`, `int_sqrt`
- **Worker B** (`cgir-kbuild-w2`): `gcd`, `__sw_hweight32`, `lcm_not_zero`

Each worker installs its candidates, builds its own Image, and boots its own QEMU
— the two pipelines never touch. Run concurrently via a thread pool.

## Result

```
clone cgir-kbuild -> cgir-kbuild-w2: 17s (one-time)
worker A (lcm, int_pow, int_sqrt):        247s  all DIFF_PASS
worker B (gcd, __sw_hweight32, lcm_not_zero): 246s  all DIFF_PASS
=== PARALLELISM ===
  wall-clock (concurrent) : 247s
  sum of worker times     : 493s
  speedup                 : 1.99x on 2 workers
RING 7: PASS
```

Both batches verified (each `cgir_*` bit-identical to its kernel C), and the
concurrent wall-clock (247s) is essentially half the summed worker time (493s) —
**near-linear 1.99× on two workers.** The host had the cores to run two full
kernel builds + boots without meaningful contention; on N dedicated machines the
scaling continues at ~N×, which is the regime the research's estimate assumes.

## Why this finishes the wall-clock story

The research's arithmetic for a full run was: naive one-boot-per-function ≈ 800
days; **batches × parallel workers** ≈ weeks. Rings 2/5 demonstrated the batch
factor (up to ~292k comparisons per boot); Ring 7 demonstrates the worker factor.
Together they are the two multipliers in that estimate, both now exercised:

    wall-clock  ≈  (functions / batch_size) / workers  ×  boot_time

Scaling is provisioning — more volumes, more cores/machines — not capability. A
cloud run with 50 workers is 50 copies of exactly this, no new code.

## Status

- Two pristine worker volumes, fleet split, built + booted concurrently. ✅
- Both batches differentially verified; wall-clock < sum-of-workers. ✅
- The parallel-worker lever exercised — the second multiplier in the research's
  wall-clock model, alongside batching. ✅
- The full-run bottleneck is now entirely a provisioning/scale question, with both
  levers demonstrated. ✅
