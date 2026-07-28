# The per-driver MMIO-record harness generator — closing the T3_TRACE bucket

The router routes driver register functions to T3_TRACE (the recorder). The
recorder's *mechanism* was proven (Ring 3/4, `dream/recorder/`); what was missing
was the **per-driver harness** — the seam-adapted C reference, the register
model, and the probe, all hand-written for one example. This generator produces
them **automatically from a real in-tree driver function**, so a T3_TRACE routee
goes from "owes a recording" to "verified against its own C by its register
trace" with no hand-scaffolding.

## What it does (per function)

1. **Extract the MMIO program** — the ordered `readl`/`writel(base + OFFSET)`
   accesses, offsets resolved from the file's `#define`s, value expressions
   parameterized by the pin/hwirq input. Non-MMIO effect calls
   (`gpiochip_enable_irq`/`disable_irq`) are **noted as out-of-trace**, not
   silently dropped — the honest edge of what the recorder covers.
2. **Seam-adapt to a self-contained C ref** — struct plumbing removed, input a
   plain `u32`, `readl`/`writel` → `reg_read`/`reg_write` (the Ring 3/4 seam),
   `BIT()` → shift.
3. **Emit a candidate** from the same skeleton (the register program in Rust) and
   a **negative control** with one write offset mutated.
4. **Record** the C ref against the RAM register model, **replay** each candidate:
   correct → MATCH, mutant → DIVERGE on the trace. Non-vacuous by construction.

Refuses honestly anything outside the clean pattern — same generate-or-refuse
discipline as the mirror generator.

## Result — real GPIO functions (`gate.sh`)

```
ftgpio_gpio_ack_irq     program=[W(GPIO_INT_CLR)]           correct MATCH · control DIVERGE@0
ftgpio_gpio_mask_irq    program=[R(GPIO_INT_EN) W(GPIO_INT_EN)]  MATCH · control DIVERGE@1   (out-of-trace: gpiochip_disable_irq)
ftgpio_gpio_unmask_irq  program=[R(GPIO_INT_EN) W(GPIO_INT_EN)]  MATCH · control DIVERGE@1   (out-of-trace: gpiochip_enable_irq)

MMIOGEN GATE: PASS (3/3 real GPIO fns recorder-verified + control-rejected)
```

Three real `drivers/gpio/gpio-ftgpio010.c` functions, verified end to end against
their own C register trace, extracted automatically from the in-tree source. The
wrong-register control diverges at the exact trace position where the program
differs (the write). This is the recorder's promise — *what the driver does to
the device is the correctness* — now **automated** for the clean pattern.

## Coverage — the router's 40 T3_TRACE routees (`close_drivers.py`)

```
T3_TRACE routees 40 | CLOSED 3 | refused 37 | harness-fail 0
```

3/40 is the honest first-cut coverage, and the 37 refusals are an **itemized
extractor backlog**, not a wall:

- **base-alias locals** (`void __iomem *base = g->reg_base + N; readl(base + OFF)`)
  — tegra186/tng functions alias the base into a local first; the extractor needs
  to track that alias (the largest bucket).
- **macro/computed offsets** (`readl(gchip->base + PDR(gpio))`) — the offset is a
  function of the pin, not a `#define` constant; extend to symbolic offsets.
- **multi-local bodies** (`u32 pos, regset; unsigned long flags`) — the extractor
  currently models only a single `val` local.
- **helper-wrapped accessors** (`readl(ctrl->base + reg)` with `reg` a parameter)
  — the low-level accessor; the real targets are its callers.

Each is a concrete regex/extractor enhancement that widens coverage; the RECORD/
REPLAY engine and the seam are unchanged. The refusals are also honest signal:
much of a driver's register access is wrapped or computed, so a naive "direct
constant-offset" extractor reaches a minority first pass — the same shape as the
mirror generator refusing entangled structs.

## Why this matters

This is, as far as the prior-art survey found, the piece nobody else has:
**automatic verification of a real kernel driver function against its own MMIO
register program.** Userspace differential tools (SACTOR/Syzygy/VERT) check
return values; a return-value check passes a driver that pokes the wrong
registers. Here a wrong-register control is *rejected*, on a register program
*extracted from real in-tree C*, with no device present. Closing the driver mass
is now a coverage-grind on the extractor, not a missing mechanism.

## Files

`record_engine.h` (generic RECORD/REPLAY, RAM register model), `mmio_harness.py`
(extract → seam-adapt → emit ref/candidate/control → gate), `gate.sh` (the 3-fn
reproducible proof), `close_drivers.py` (coverage over the router's T3_TRACE set),
`close_drivers_result.json`. Companion: `dream/recorder/` (the mechanism),
`dream/router/DRIVER-RUN.md` (the routing that produced the T3_TRACE set),
`dream/ratchet/RING4.md` (the in-kernel realization of the same seam).
