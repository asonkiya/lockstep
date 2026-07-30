# Effect-trace footprint refinement — the honest reach, and the measured case for CGIR

`python3 dream/efftrace/footprint.py` refines the router's `bounded_state` class
(14,233 fns = 35.5% of the core) with an actual footprint extraction:

```
14233  bounded_state (router, effect-KIND upper bound)
  729  footprint_BOUNDED  (syntactic extraction -> effect-trace oracle applies)
12855  refute: opaque call        <- calls another function (footprint not
                                      syntactically bounded)
  474  refute: dynamic-index write (global array, non-constant index)
  175  refute: no escaping write   (read-only -> in-kernel diff, not effect-trace)
```

## The correction (honest broker)

My earlier "35.5% unlocked by the effect-trace oracle" was an **effect-KIND upper
bound**, and it was optimistic. The **syntactic lower bound is 729** (~1.8% of the
40k core) — because **90% of bounded_state functions call at least one other
function**, and a purely-syntactic pass must treat any callee as an opaque,
possibly-unbounded effect. So the true reach lives between 729 and 14,233.

## What moves it — and why it's exactly CGIR

The 12,855 refuted-on-a-single-opaque-call are **not** unbounded — most call small
pure/bounded helpers (`rlim_to_rlim64` writes two fields then returns; `resource_clip`
writes `res->start/end`; `acct_clear_integrals` zeroes a few `tsk->` fields). What
makes their footprint bounded-or-not is **the callees' effects** — an
*interprocedural* question. That is precisely CGIR's `effects` transitive-closure
(`calls_effectful` propagated over the CALLS graph): resolve each callee to
pure/bounded/unbounded, and a function whose callees are all bounded becomes
footprint-bounded and recoverable.

So this refinement **quantifies the CGIR contribution**: effect-trace reach is
**729 without interprocedural analysis, up to ~13k with it.** The gap (~12,855
functions) is the measured payoff of wiring CGIR's transitive effects into the
footprint extractor — the concrete reason CGIR and lockstep are a pair.

## The revised core rollup (honest)

- pure_leaf 22.9% + struct_reader 8.2% + mmio 0.0% = **~31% production oracle now**
- concurrent 13.9% = **+14% prototyped** (loom/KCSAN, needs scaling)
- effect-trace: **1.8% syntactic floor now**, climbing toward the 35.5% ceiling as
  CGIR interprocedural effects resolve the opaque-callee 90%
- unbounded_state 15.4% + dynamic-index/no-write refutations + arch-asm floor = the
  genuine hard/floor tail

The core is still majority-addressable — but the effect-trace oracle's reach is
**gated by interprocedural effects analysis**, so the next build is that CGIR
wiring, now motivated by a hard number, not the raw record/replay mechanism
(already proven, `proof.py`).

## Next build

Wire CGIR `effects` transitive closure (or a lockstep interprocedural pass on the
call graph) into `footprint.extract`: for each opaque callee, resolve pure/bounded
and fold its footprint in; re-run this refinement to measure how much of the
12,855 is recovered. That is the CGIR<->lockstep seam turned into reach.
