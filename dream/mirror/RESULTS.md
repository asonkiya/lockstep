# The struct-mirror library — generated `#[repr(C)]` mirrors, proven ABI-correct

Ring 8 hand-wrote one `#[repr(C)]` struct mirror + one hand-computed `BUILD_BUG_ON` to
transplant a Tier-B function (one that reads struct fields). The gap analysis named the
Tier-B middle (~48% of functions) blocked on exactly that hand work: every struct a
transplant touches needs a Rust mirror whose layout is **byte-identical** to the kernel's,
and a wrong offset is a silent memory-corruption bug the differential can't see. This
library **generates** those mirrors from the real kernel struct and proves each one
correct two independent ways.

## How it works

1. **Parse** the real struct from its kernel header (`clk-provider.h`, `timecounter.h`, …).
2. **Lay it out** under the LP64 model (arm64/x86-64): per-field alignment padding,
   struct size rounded to max member align — the same rules the C ABI uses.
3. **Emit two artifacts** carrying that computed layout as *assertions*, not comments:
   - a Rust `#[repr(C)]` mirror with `const _: () = assert!(size_of::<T>() == N)` and a
     `const _: () = assert!(offset_of!(T, field) == K)` per field;
   - a C `BUILD_BUG_ON(sizeof(struct X) != N)` / `BUILD_BUG_ON(offsetof(struct X, f) != K)`
     guard block.

The build **passes iff `rustc-layout == generator-model == kernel-layout`**:

- rustc compiling the const-asserts proves *rustc's* real layout equals the generator's;
- the kernel compiling the `BUILD_BUG_ON`s **against the real headers, at kernel build**
  proves the *real kernel's* layout equals the generator's.

Two ends pinned to one middle ⇒ the two ends equal each other. A wrong mirror, or a config
that shifts a field, fails to build. The `BUILD_BUG_ON` the prior-art survey called
load-bearing is now emitted automatically, per struct, instead of hand-written once.

## Result (`gate.sh`)

```
generated clk_div_table -> ClkDivTable (size 8)
generated clk_duty      -> ClkDuty     (size 8)
generated cyclecounter  -> Cyclecounter (size 24)
generated timecounter   -> Timecounter  (size 40)
4 mirrors generated, 0 refused
  ✓ rustc-layout matches generator      (rustc compiled every const-assert)
  ✓ real kernel layout matches generator (kernel compiled every BUILD_BUG_ON)
MIRROR LIBRARY GATE: PASS (mirrors proven ABI-correct: rustc == generator == kernel)
```

`cyclecounter=24` and `timecounter=40` are the exact sizes Ring 8 computed by hand — now
produced by the generator and confirmed by both rustc and a real kernel build, end to end,
with no human in the layout loop. `timecounter` embeds `cyclecounter` by value plus a `u64`
and a `u64` fraction — the nested-struct offset the generator gets right is the realistic
case, not a toy.

## Conservative by construction — it refuses what it can't lay out soundly

A guessed layout is worse than no layout, so anything whose ABI isn't fixed by the C types
alone is **REFUSED**, not approximated:

- **bitfields** (`u32 x : 3`) — Rust `repr(C)` has no bitfields; refused.
- **unions** — `repr(C)` union needs a manual active-variant decision; refused.
- **`#if`/`#ifdef` fields** — layout depends on config, not fixed; refused.
- **nested struct-by-value** whose inner struct isn't itself a scalar/pointer shape —
  refused with "needs its own mirror" (mirror the inner one first, then compose).

`iphdr`/`ethhdr` are correctly refused (bitfields / macro array dims): an honest "I can't
prove this" rather than a plausible-looking wrong mirror.

## Scope + how it lands in-kernel

- The generator is a pure layout function; both proofs run in the existing kernel-gate
  Docker image (`cgir-kernel-gate` + the `cgir-kbuild` arm64 tree). No hand steps.
- What it covers: structs of scalars, pointers, function pointers, fixed arrays, and
  nested mirror-able structs — the shape the bulk of Tier-B leaf/near-leaf functions read.
- What it does **not**: bitfield/union/config-variant structs (refused, above) — those
  stay hand-reviewed, which is the honest edge.
- This unlocks Tier-B **one struct family at a time**: generate the mirror, the gate proves
  it, and every function that reads those fields is now transplantable against a layout
  that rustc *and* the real kernel agree is correct.

## Status

- Generator (LP64 layout) + dual emitters (Rust const-asserts, C `BUILD_BUG_ON`): built. ✅
- Both proofs pass on 4 real kernel structs (incl. a nested-by-value one). ✅
- Refuses bitfields/unions/#if/non-mirror-able nesting honestly. ✅
- The second of the three critical-path libraries from the gap analysis, delivered as a
  reusable generator — hand-mirroring (the Tier-B bottleneck) is now automatic + proven.
