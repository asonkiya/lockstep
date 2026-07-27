# Ring 9 — a real subsystem swept: breadth × depth

Rings 5–7 proved the fleet loop and parallel workers on scalar leaves; Ring 8
built the depth substrate (`ksdk`) for struct-context. Ring 9 puts them together
on a **real subsystem cluster**: the entire divider-math family of
`drivers/clk/clk-divider.c`, unlocked by the one `clk_div_table` mirror.

## The cluster

Six functions, all reading `struct clk_div_table` arrays and branching on the
divider flags — a genuine subsystem, not hand-picked scalars:

`_get_table_div`, `_get_table_val`, `_get_table_maxdiv`, `_get_maxdiv`,
`_get_div`, `_get_val`.

They call each other (`_get_maxdiv` → `_get_table_maxdiv`, `_get_div` →
`_get_table_div`), so they were transplanted **as one Rust object** against `ksdk`
— internal helpers stay Rust-internal, the six exports are the `cgir_*` seams.
Haiku produced the whole cluster in one shot ($0.0087). This is the depth payoff:
the `clk_div_table` mirror + `clk_div_mask` helper were built once in Ring 8, and
here the *entire family* rides on them at no extra mirroring cost.

## One boot, whole family verified

`gate.sh` compiles the cluster + the C originals + a per-function probe, and boots
once. The probe drives a non-identity table, all the divider flags
(`ONE_BASED`/`POWER_OF_TWO`/`MAX_AT_ZERO`/`EVEN_INTEGERS`/none), and widths, and
compares each `cgir_*` against its C `_ref`:

```
CLKFAM: get_table_div    bad=0  DIFF_PASS
CLKFAM: get_table_val    bad=0  DIFF_PASS
CLKFAM: get_table_maxdiv bad=0  DIFF_PASS
CLKFAM: get_maxdiv       bad=0  DIFF_PASS
CLKFAM: get_div          bad=0  DIFF_PASS
CLKFAM: get_val          bad=0  DIFF_PASS
CLKFAM: total            bad=0  DIFF_PASS   ->  RING 9 GATE: PASS (6/6)
```

Every function in the family verified bit-identical to the clk-divider C it
replaced, across the flag/width/table matrix, in a single boot.

## What Ring 9 shows

This is "run it wide on a real subsystem," in miniature: a whole in-tree file's
computational cluster, transplanted through the depth substrate and batch-verified.
The cost was one struct mirror (amortized) + one cheap model call + one boot. Scale
this by (a) more subsystems' worklists, (b) their struct mirrors added to `ksdk`
once each, (c) parallel workers (Ring 7) fanning the boots — and it is the full-run
regime the research described, now assembled from proven parts.

## Status

- A real subsystem's math family (6 clk-divider fns) transplanted as one object
  against the shared mirror. ✅
- All six differentially verified against the C, one boot, across the flag/width
  matrix. ✅
- Depth substrate amortized: one mirror unlocked the whole family. ✅
- Breadth × depth demonstrated on real in-tree code — the shape a subsystem sweep
  repeats. ✅
