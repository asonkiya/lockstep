# GPIO driver-family trace oracle — results (the per-family cost, measured)

Host-first (boot-free) generalization of the Ring-4 recorded-MMIO differential
across the GPIO family. `python3 dream/family/gpio_family.py`:

```
[ok] gpio-zevio                  correct DIFF_PASS (448 accesses) / wrong-register DIFF_FAIL @0
[ok] gpio-mmio(bgpio-core)       correct DIFF_PASS (256 accesses) / wrong-register DIFF_FAIL @0
[ok] mxs-alias(set/clr@+4/+8)    correct DIFF_PASS (320 accesses) / wrong-register DIFF_FAIL @0
GPIO FAMILY: PASS — trace oracle generalizes; wrong-register caught
```

Coverage gate is **non-vacuous**: forcing `npins=1` (only the set-0 arm reachable)
yields `cov=0 verdict=REFUSE`, not a vacuous pass.

## What generalized

One **generic op-driver + ordered-trace comparator** (write-once, whole family)
drives every driver's get/set/direction ops across 32 pins, records the full
register trace via recording `mmio_r`/`mmio_w`, and compares the C reference's
trace to a Rust transplant's. Correct → `DIFF_PASS`; a one-line wrong-register
mutation → `DIFF_FAIL` at the diverging access. Same soundness as Ring 4, now
driver-agnostic.

## The idioms collapsed further than the scope predicted

The scope estimated ~3–4 idioms. Empirically there are **two coupling mechanisms**:

| idiom | coupling | drivers |
|---|---|---|
| RMW-DATA | same-offset read/write (a passive register array) | gpio-zevio, `gpio_mmio_set` |
| SET/CLR | write-offset ORs/ANDs into a read-offset | bgpio `set_with_clear`, mxs |

"SET/CLR separate registers" (bgpio, set@0x10) and "SET/CLR aliases" (mxs,
set@0x04) are the **same idiom at different offsets** — the `mxs-alias` driver
closes by reusing the bgpio idiom with only its offset table changed. So the
device model is essentially binary; everything else is per-driver offsets.

## The cost split, measured (the deliverable)

| layer | scope | size |
|---|---|---|
| op-driver + comparator + 2 idiom couplings | **write-once, whole family** | ~128 lines total |
| per-driver: seam-adapted C ref + Rust transplant + offsets | **per driver** | ~40 lines, of which the transplant (~30) is `$0.006` model-synth in the real loop and the hand config (offsets + get-register) is **~10 lines** |
| trace comparison | automatic | free |

**The big lever, confirmed:** the `gpio-mmio(bgpio-core)` entry is ONE transplant
of the shared library that ~41 gpio drivers delegate their get/set/direction to.
Proving it trace-verifies means one transplant covers the register core of all of
them — the per-shared-library economics the cost analysis turned on, now empirical.

## What this says about the full-rewrite cost

For the GPIO ~43-op register core: **1 write-once harness + 2 idiom couplings +
1 shared-library transplant + ~a dozen small per-driver offset tables**. The cost
is O(idioms) + O(shared-libraries), NOT O(functions) — confirming the driver mass
factors, and that the cash cost stays low (synth is `$0.006`/fn; the work is the
bounded write-once harness + mechanical per-driver offsets). Extrapolating the
shape across subsystems (regmap, spi-bitbang, …) is the remaining engineering
grind — bounded, not open-ended.

## Scope / honest edges

- Host-first differential (both impls hit the same software register model, the
  Ring-4 posture). The in-kernel boot gate is unchanged from Ring 4 and is the
  follow-on that verifies in vmlinux; the RECORD phase then hits the real
  accessor instead of the model.
- Candidates are hand-written to isolate the ORACLE-generalization result;
  model-driven synthesis (the Ring-5 fleet loop) is proven separately and is what
  makes this autonomous.
- The `mxs-alias` driver models the mxs SET/CLR-alias coupling + offsets faithfully
  but is not a verbatim transplant of every mxs op; it demonstrates idiom-reuse-
  at-new-offsets, the per-driver cost claim.
- The ~49 non-core gpio ops (irq/config/init) are heterogeneous and deferred.
