# T3 in a booting kernel — the auto-generated harness, realized in vmlinux

`mmio_harness.py` verifies driver register functions host-side (fast, no boot).
`inkernel.py` closes the loop the dream actually asks for: it weaves the same
**auto-extracted** functions into a **booting Linux kernel** and trace-verifies
them there — the Ring 4 realization, but produced automatically from real driver
source for a batch of functions, no hand-scaffolding.

## What gets woven

From the same extraction the host generator uses, `inkernel.py` emits three
freestanding artifacts and links them into vmlinux:

- **`ref.c`** — the seam-adapted C references (`<fn>_ref`), `readl`/`writel` →
  `reg_read`/`reg_write` (the seam Ring 3/4 booted).
- **`cand.rs`** — the Rust transplants (`cgir_<fn>`), compiled
  `--target aarch64-unknown-none-softfloat --emit=obj` into a `.o_shipped`
  object, exactly as every ring's Rust does.
- **`probe.c`** — provides `reg_read`/`reg_write` over a software register block
  (QEMU has no GPIO hardware; the passive register model is faithful), records
  every access, and in a `late_initcall` runs the C refs and the Rust candidates
  over all 32 pins, hashes the two register-access traces, and prints the
  verdict.

Everything links into one `vmlinux`; the verdict comes out of a real boot.

## Result

```
correct: MMIO_INK: ops=32 ref_len=160 cand_len=160
         ref_hash=0x91db65dcbebb39de cand_hash=0x91db65dcbebb39de firstdiff=-1 verdict=DIFF_PASS
wrong  : MMIO_INK: ops=32 ref_len=160 cand_len=160
         ref_hash=0x91db65dcbebb39de cand_hash=0xb970deac0a1f9dde firstdiff=0  verdict=DIFF_FAIL
INKERNEL GATE: PASS (3 driver fns Rust-transplanted, trace-verified in a BOOTING kernel;
                     wrong-register control DIFF_FAIL)
```

The correct Rust produces a register trace with the **byte-identical hash**
(`0x91db65dc…`) as the C, over 160 recorded accesses across 32 pins, confirmed
by the boot console `[155.97]`. The wrong-register control's hash differs
(`0xb970deac…`) and diverges at `trace[0]` — a non-crashing bug that boots fine
and returns nothing wrong, caught because *what the driver does to the device* is
what's checked.

The correct candidates issue the **identical register program** as the C, verified
inside the booting kernel by trace hash; the wrong-register control (one write
offset mutated) **DIFF_FAILs** on the trace — a non-crashing bug that boots fine
and returns nothing wrong, caught because *what the driver does to the device* is
checked.

## What this is

The full chain, automatic and end to end:

```
real in-tree driver C  --extract-->  register program
                       --seam-adapt-->  C ref + Rust transplant
                       --weave-->  freestanding objects in vmlinux
                       --boot-->  in-kernel trace differential  --> DIFF_PASS
```

Three real `gpio-ftgpio010` IRQ functions (`ack`/`mask`/`unmask`) transplanted to
Rust and **verified running inside a booting kernel against their own C register
program** — starting from nothing but the driver source and one command. Host
verification (`mmio_harness.py`) is the fast pre-check; this is the in-kernel
proof. The non-MMIO `gpiochip_enable/disable_irq` bookkeeping remains out of the
recorded trace (the honest edge), same as host.

## Scope

- Software register block, not real hardware (QEMU) — identical to Ring 4; a real
  driver adds recording the actual `readl`/`writel` values, the oracle logic
  unchanged.
- Coverage is the host generator's coverage (3/40 of the router's T3_TRACE set
  first-cut; the rest is the itemized extractor backlog in `RESULTS.md`). This
  proves the *in-kernel realization* of whatever the host generator closes.

## Files

`inkernel.py` (merge extracted fns → ref.c / cand.rs / probe.c, build + boot +
verify, with a wrong-register control), `inkernel_out/` (the generated artifacts
+ boot logs). Companion: `mmio_harness.py` (host generator + extraction),
`RESULTS.md` (host coverage), `dream/ratchet/RING4.md` (the hand-built precedent
this automates).
