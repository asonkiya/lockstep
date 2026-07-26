# Ring 4 — a real in-tree driver, transplanted and trace-verified

Ring 3 built the recorded-I/O oracle on a representative device. Ring 4 points it
at **real in-tree driver code**: `drivers/gpio/gpio-zevio.c` — genuine register-
address math, read-modify-write bit programming, the get/set/direction ops that
every GPIO driver has. This is the first conversion of code that shipped in Linux.

## What is real, and what is adapted (kept honest)

**Real (verbatim from the driver):** the register-programming logic —
`section_offset = ((pin >> 3) & 3) * ZEVIO_GPIO_SECTION_SIZE`, the RMW on the
OUTPUT/DIRECTION registers, `BIT(pin & 7)`, the direction-aware read in `get`, the
exact register offsets. That logic — the part with the subtle bugs — is the
driver's own.

**Adapted (the seam only):** the ops take the register base directly instead of
recovering it via `gpiochip_get_data()`/`container_of()`, and MMIO goes through
`mmio_r`/`mmio_w` (a recording `readl`/`writel`) so the oracle can trace it. The
spinlock is dropped (Ring 4 is about the register program; locking was Rings
0/2/M4). These are the seam adaptations a real recorded-MMIO harness makes; they
don't touch the register logic under test.

The device is a software register block (QEMU has no zevio hardware) — faithful
for this passive controller: OUTPUT/DIRECTION hold the bits you write and read
back, INPUT is seeded with a fixed pattern. Both implementations hit the same
block.

## The transplant + gate

Haiku transplanted all four ops to freestanding Rust (`$0.0058`) — section math,
RMW, register choices, order all matched. The gate drives a 32-pin op script
(dir_out → set → get → dir_in → get) through the C original and the Rust
transplant and compares the full register-access trace:

```
correct: ZEVIO_PROBE: ops=32 ref_len=448 cand_len=448
         ref_ret=0x2ddd0d3d cand_ret=0x2ddd0d3d
         ref_hash=0xb525111373d1a589 cand_hash=0xb525111373d1a589 firstdiff=-1  DIFF_PASS
```

448 register accesses across 32 pins, bit-identical program, identical returns.
The Rust transplant of the real driver *programs the registers exactly as the C
does*.

## Negative control — the classic "wrong register" bug

The most common real driver bug: touching the wrong register. The control points
the OUTPUT ops at the INPUT offset (`0x14` → `0x18`):

```
wrong:   ZEVIO_PROBE: ops=32 ref_len=448 cand_len=448
         ref_ret=0x2ddd0d3d cand_ret=0x0  ref_hash=0xb525...a589 cand_hash=0x17a2...509d
         firstdiff=0  DIFF_FAIL
```

Diverges at the very first access — where the driver reads OUTPUT (0x14), the
buggy transplant reads INPUT (0x18) — and the returns collapse to 0. Non-crashing,
wouldn't trip KCSAN; a driver that quietly programs the wrong register just makes
the hardware misbehave. The trace oracle catches it immediately.

## Why Ring 4 matters

This is the shape of the 73%: a driver's correctness is which registers it
touches, in what order, with what values — and that is now mechanically
transplantable and gateable. The pipeline that did it — vendor the real logic,
model the device in software, transplant with a cheap model, verify the register
trace against the C — is exactly what a fleet run over `drivers/` would do, at
`$0.0058` and one boot per driver-cluster. The only thing a live driver adds is
recording the *real* `readl`/`writel` trace from hardware instead of a software
model; the oracle and transplant are unchanged.

## Status

- Real in-tree driver (gpio-zevio) register logic transplanted to Rust. ✅
- Register program verified bit-identical to the C over a 448-access trace. ✅
- The canonical "wrong register" bug REJECTED on its trace (boot+KCSAN blind). ✅
- First conversion of code that shipped in Linux — the 73% is addressable. ✅
