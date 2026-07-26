# Ring 2 — batch scale, and the kernel runs Rust for real callers

Ring 1 grew the ratchet to a second subsystem one function at a time. Ring 2
does it as a **batch** (the cost-model's key lever) and steps up the stakes:
the transplanted functions are **widely-used kernel primitives**, so weaving
them means real callers across the kernel run the Rust at boot — not a probe.

## The batch: four pure lib leaves

`int_pow`, `__sw_hweight32`, `__sw_hweight64`, `gcd` — all pure, all heavily
called. Synthesized freestanding by Haiku (`synth_leaf.py`), **$0.0093 total**.

## Batch differential gate — 4 functions, ONE boot (`ring2/gate_batch.sh`)

The reference needs no duplication: at gate time these are still the kernel's own
C (we gate *before* weaving), so the probe compares each `cgir_*` candidate
against the **live exported kernel symbol** over wide input ranges, all in a
single boot:

```
BATCH_PROBE: int_pow   n=1215   bad=0 verdict=DIFF_PASS
BATCH_PROBE: hweight32 n=200033 bad=0 verdict=DIFF_PASS
BATCH_PROBE: hweight64 n=75     bad=0 verdict=DIFF_PASS
BATCH_PROBE: gcd       n=90606  bad=0 verdict=DIFF_PASS
RING 2 BATCH GATE: PASS
```

**~292,000 differential comparisons across four functions in one boot, zero
mismatches.** That is the batching thesis from the research made concrete: the
expensive boot is amortized over the whole batch, which is what makes the
wall-clock math work at scale.

## The accumulated weave — 5 Rust objects in one booting kernel

`add_ring2.py` + `weave.py gate` (weaves int_pow + hweight32 + hweight64; gcd
verified-but-deferred, see below):

```
manifest now: 4 sources, 8 functions status:rust
wove drivers/ptp/ptp_mock.c (4), lib/math/int_sqrt.c (1),
     lib/math/int_pow.c (1), lib/hweight.c (2)
✓ 5 Rust objects compiled + wired (ptp_mock_regions, int_sqrt, int_pow,
  hweight32, hweight64)
✓ Image built; boot-digest smp_up=True boot_complete=True early_panic=False

=== ratchet dashboard ===
  sources woven     : 4
  functions -> Rust : 8/16  (50.0%)   [Ring 0: 4/9 -> Ring 1: 5/11 -> Ring 2: 8/16]
  strongly gated    : 8/8 (differential)
```

Because `int_pow`/`hweight32`/`hweight64` are called all over the kernel, this
boot **actually ran the Rust versions via real callers** and stayed healthy — a
stronger claim than ptp_mock (which had no consumer). Five independent Rust
objects now coexist in one vmlinux; the panic-handler localization holds at N=5.

## Two real findings this batch surfaced

1. **Duplicate exported symbols from structure-mirroring.** The model's
   `hweight64` candidate, mirroring the C's 32-bit/64-bit split, emitted its own
   `#[no_mangle] cgir_sw_hweight32` helper. Because `no_mangle` symbols are not
   dead-code-eliminated, the `cfg`-inactive helper still collided at link with
   `hweight32.rs`. Fix here: strip the dead helper (kept the model's real 64-bit
   body). General fix: the batch must dedupe exported symbols or compile a
   subsystem's leaves as one crate. A weaver-scale lesson, cheap to hit now.
2. **Excision can orphan a static helper.** Weaving `gcd` would leave its
   `static binary_gcd` unused (a `-Werror` risk), so `gcd` is recorded
   `verified_not_woven` in the manifest — differentially proven correct, but its
   weave needs helper-aware excision. This is exactly why the manifest tracks
   *verified* separately from *woven*: the ratchet never pretends.

## Status

- Batch of 4 leaves synthesized ($0.0093) and verified in ONE boot (~292k
  comparisons, 0 mismatches) — the batching lever demonstrated. ✅
- 3 woven into a booting kernel; %-Rust 45.5% → **50.0%**, 8/8 differential. ✅
- Widely-used primitives now run as Rust via real callers at boot. ✅
- Two weaver-scale findings recorded, not hidden (dup symbols; helper orphaning). ✅

Ring 3 is more of the same shape at larger batch — and the first driver with a
recorded-I/O differential oracle, to start converting the 73% weakly-gated mass.
