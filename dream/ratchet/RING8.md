# Ring 8 — depth: the idioms/mirror crate that unlocks the Tier-B middle

You chose depth: build the reusable substrate that reaches the ~73% Tier-B middle,
not just pure-scalar leaves (Rings 2/5) and register drivers (Rings 3/4). The
research named the biggest wall to that middle: **struct context** — functions
that read fields of kernel structs and call inline/macro helpers with no symbol to
link. Ring 8 builds the substrate (`ksdk`) and transplants a real function that was
out of reach without it.

## `ksdk` — the shared kernel-idioms + struct-mirror crate

Built once, linked by every Tier-B transplant (the one-object shared runtime from
Ring 1). It carries:
- **`#[repr(C)]` struct mirrors** with **compile-time layout guards** — Rust
  const-assertions that are the BUILD_BUG_ON the research called load-bearing for
  config-dependent layout;
- **reimplemented inline/macro helpers** (`clk_div_mask`, `div_round_up_u64`) — the
  ~69% of callees that are inlines/macros with no linkable symbol;
- **`container_of!`** — the offset-subtraction primitive that is 17% of Tier-D.

## The Tier-B transplant

`drivers/clk/clk-divider.c`'s table-walk helpers — `_get_table_div` /
`_get_table_val` — iterate a `struct clk_div_table *` array and read its `->val`/
`->div` fields. Struct-pointer iteration over a mirrored struct: exactly the
struct-context class pure-scalar synth cannot express. Haiku transplanted both
against the `ClkDivTable` mirror ($0.0027) — a clean raw-pointer walk to the
`div==0` sentinel.

## Three gates — and why struct mirroring needs all of them

```
correct     : CLKDIV_PROBE n=82 bad=0  firstbad=-1  DIFF_PASS
mirror-size : error[E0080]: assertion failed: size_of::<ClkDivTable>() == 8  (COMPILE FAIL)
mirror-swap : CLKDIV_PROBE n=82 bad=13 firstbad=0   DIFF_FAIL   (compiled — guard blind to same-size swap)
```

- **correct** — the transplant reads the struct through the mirror bit-identically
  to the C, over a non-identity `val→div` table.
- **mirror-size (compile-time)** — a mirror that drifts in size is rejected *before
  the kernel is built*, by the layout guard. This is the config-dependent-layout
  bug class the research flagged, caught cheaply.
- **mirror-swap (runtime)** — a mirror with the two fields swapped is the *same
  size*, so the layout guard passes — but the transplant then reads the wrong
  field, and the differential catches it. This is the lesson: **the size/offset
  guard and the behavioral differential catch different bugs; struct mirroring
  needs both.**

## What this unlocks

Every Tier-B function that reads a struct now has a path: mirror the struct in
`ksdk` (guarded), reimplement its inline helpers once (shared), transplant, gate.
The mirror + helper library is amortized across every function that touches the
same struct — build `clk_div_table` once, and every clk-divider function is
reachable. This is the substrate that turns the research's ~73% from "reachable in
principle" into "transplantable in practice," one struct family at a time.

## Status

- `ksdk` shared crate: repr(C) mirrors + compile-time layout guards + reimplemented
  inlines + container_of. ✅
- A real struct-context Tier-B function (clk-divider table walk) transplanted and
  differentially verified — the class pure-scalar synth couldn't reach. ✅
- Layout guard catches size drift at compile time; differential catches field
  confusion at runtime — both mirror failure modes gated. ✅
- The depth substrate exists; the Tier-B middle is now addressable per struct
  family. ✅
