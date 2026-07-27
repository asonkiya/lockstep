# The driver-MMIO effect recorder — record once, replay to verify

The gap analysis named this the highest-ROI missing piece: the way to make the
driver mass (~73%) soundly verifiable. A driver's correctness is its register
program, and a return-value differential over-credits it (the wide run passed
`__refrigerator`/`probe_irq_mask` on return value alone). Ring 3/4 fixed the
oracle but needed a **live device model present while running both** the C and the
Rust. The recorder removes that dependency — the one thing that lets you verify
against a real driver whose device you can't reproduce.

## How it works

- **RECORD:** run the C driver once; intercept every `readl`/`writel` (here the
  `reg_read`/`reg_write` seam) and log the ordered trace — each access's kind,
  offset, and the value the device returned — plus the return.
- **REPLAY:** run the Rust transplant against the **frozen recording, no device
  present.** Each `reg_read` must match the next recorded read (same offset) and
  is fed the recorded value; each `reg_write` must match the next recorded write
  (same offset *and* value). Accept iff the candidate consumes the whole trace in
  order and returns the same result.

The decoupling is the point: record a real driver's MMIO **once** (on hardware, a
model, or a captured trace), then verify every transplant candidate against the
recording forever, without the device.

## Result (`gate.sh`)

```
correct : RECORDER cases=512 bad=0   verdict=MATCH
          (the transplant replays the recorded register program exactly)
subtle  : RECORDER cases=512 bad=512 first_divergence_at_trace=2 verdict=DIVERGE
          ("skip the status poll" — same RETURN value, wrong register program;
           rejected on the trace at exactly the missing STATUS read)
```

The subtle bug is the crux: the skip-poll transplant returns the **identical
value** (the model sets the result synchronously), so a value-only differential —
the whole field's oracle — passes it. The recorder rejects it on every one of the
512 cases, at the precise access where the register program diverges. *What the
driver does to the device* is checked, not just what it returns.

## Scope + how it lands in-kernel

- Userspace here (fast, self-contained): `reg_read`/`reg_write` stand in for
  `readl`/`writel`, and a software device model is the RECORD-phase device. The
  **mechanism is identical in-kernel** — the seam becomes an interceptor on the
  real accessors, which Ring 3/4 already booted; the RECORD phase then hits real
  hardware (or a captured trace) instead of the model, and REPLAY is unchanged.
- What it does *not* cover: reads whose value depends on timing/interrupts the
  recording didn't capture (a genuinely reactive device needs a richer recorded
  model), and non-MMIO effects (DMA, shared-memory) — those are the honest edges.
- This is the automatable subclass of the effect-trace work the purity retry
  flagged: MMIO-shaped effects record uniformly; arbitrary global/RCU state does
  not.

## Status

- Record-once / replay-to-verify: built, both legs pass. ✅
- Verifies a transplant with **no device present**, against a recording — the
  decoupling that unlocks real drivers. ✅
- Catches the realistic register-program bug (skip poll) that a return-value
  differential passes. ✅
- The highest-ROI item from the gap analysis, delivered as a reusable mechanism. ✅
