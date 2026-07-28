# The driver run — aiming the router where the deep oracles live

T2 and T3 both found the same thing on a scalar-leaf harvest: **0** real mirror
cases, **0** recordable-MMIO cases. The mirror and recorder — the two deep
oracles for the ~48% struct middle and the ~73% driver mass — had nothing to
work on, because a `EXPORT_SYMBOL` + scalar-signature harvester structurally
excludes struct-pointer and MMIO functions.

`driver_harvest.py` is the opposite harvester: it walks driver dirs, takes every
column-0 function (exported *or* static), and **keeps** the struct-param and
MMIO ones. Pointed at `drivers/{gpio,ptp,clk}` it confirms the populations are
real and large.

## The census (why the driver worklist is different)

```
6,653 driver functions scanned:
   3,516  B (struct readers)      ← the mirror population
   1,836  A
     403  B+MMIO                  ← recorder population (also struct)
     369  C (locked)
     343  D (entangled)
      99  A+MMIO
      74  C+MMIO
   → 589 MMIO functions total, 3,514 struct readers
```

Versus the scalar-leaf harvest: **0 MMIO, 0 real struct readers.** The deep
oracles' target populations live here, exactly as predicted.

## The router on a 102-fn stratified driver worklist

```
              scalar harvest (72)      driver harvest (102)
  T3_TRACE          0                       40    ← MMIO, the recorder's target
  T2_MIRROR         0 (all mis-flagged)     40    ← genuine struct-pointer readers
  TC_REGION         4                       10
  T3_EFFECT        42                        7
  C_FOREVER         2                        1
  T1_*              6                        4
```

The mirror and recorder buckets went from **empty to full**. This required one
routing fix: **MMIO now beats census-B/C** in `route_one` — a register program's
correctness is its access trace regardless of whether it also reads a struct
field, so driver ops land on the recorder (T3_TRACE), not the mirror.

## T2 on the 40 driver struct readers — real cases, but not auto-mirrorable

All 40 refine to **PARAM_STRUCT** (genuine `struct X *` readers — vs 0 in the
scalar harvest). The auto-mirror engine now resolves each struct's definition
anywhere in the tree (headers *and* the driver's own `.c`) and evaluates it —
and **0 of 40 auto-mirror**, for specific, itemized reasons:

- `gpio_chip`, `device` → **refused: config-dependent `#if` fields** (shared
  kernel structs whose layout isn't fixed without pinning a config);
- `xgpio_instance` (driver-local) → **refused: `DECLARE_BITMAP(map)`** — a kernel
  macro field the generator can't lay out;
- `platform_device` → **refused: nested `struct device` by value**.

So the mirror *population* is confirmed in drivers, but reaching it needs
generator enhancements: treat `DECLARE_BITMAP`/`spinlock_t`/`raw_spinlock_t` as
opaque fixed-size fields, and config-pin `#if` structs. That's a concrete
backlog, not a wall — and the clean-flat case still works (`clk_div_table` 8,
`cyclecounter` 24, `timecounter` 40 mirrorable, the capability proof holds).

## T3 on the 40 driver MMIO functions — the recorder's real target

```
T3_TRACE (recordable MMIO)  40   ath79_gpio_read, tegra186_gpio_get, mb86s70_gpio_set,
                                 tng_irq_ack, xlp_gpio_get_reg, mlxbf2_gpio_irq_handler, ...
T3_EFFECT (per-fn)           7   (opaque / irq)
recorder mechanism: PROVEN (correct MATCH + value-identical skip-poll DIVERGE on trace)
```

Forty real GPIO-driver register functions route to the recorder — the exact
population it was built for and that Ring 4 already trace-verified on a real
in-tree driver (gpio-zevio, bit-for-bit).

## The strategic finding: for drivers, the recorder beats the mirror

The two T2/T3 results together say something sharp: **for driver code the
recorder is the higher-value tool.** A driver register function reads an
entangled struct (`gpio_chip`) that won't auto-mirror — but the recorder
**sidesteps struct layout entirely**: it records the readl/writel *trace* and
replays against it, needing no mirror at all. That is precisely why Ring 4 could
trace-verify gpio-zevio despite `gpio_chip` being unmirrorable.

So the routing priority is right (MMIO → recorder before B → mirror), and the
per-tool roadmap is now concrete:

- **Recorder (T3):** the driver mass's real lever — reaches 40/102 here, needs a
  per-driver record harness (the automatable MMIO-seam work), no struct layout.
- **Mirror (T2):** unlocks the non-MMIO struct readers, gated on generator
  enhancements (opaque macro fields, config-pinning) — a smaller, later slice.

## Where this leaves the driver populations

Not closed-in-a-boot yet — closing 40 recorder functions needs the per-driver
MMIO-record harness generated (the next executor), and the mirror slice needs the
generator enhancements above. But the run did the thing it set out to: it
**proved the deep-oracle populations exist in drivers and routed the real
functions to the real tools**, turning "the driver mass" from a slogan into 40
recorder-targeted + 40 mirror-targeted named functions with their blockers
itemized. The tools are proven; they are now aimed correctly.

## Files

`driver_harvest.py` (the driver-scoped harvester + tier census), the router
(`--worklist`, MMIO-before-B routing fix, 0-arg probe fix), `t2_executor.py`
(struct auto-resolution across the tree), `t3_executor.py` (driver-aware).
`router_driver_result.json`, `t2_driver_result.json`, `t3_driver_result.json`,
`driver_worklist.json`. Companion: `T2-RESULTS.md`, `T3-RESULTS.md` (the
"wrong worklist" findings this run answers), `dream/ratchet/RING4.md`.
