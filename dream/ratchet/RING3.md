# Ring 3 — the recorded-I/O oracle, the key to the 73%

The research pass named the binding constraint on the whole dream: ~73% of the
kernel (the driver mass) has no functional oracle, and a driver's meaning is not
its return value — it is its **register programming** (the order of writes, the
poll on a status bit, which register the result comes from). Rings 0–2 gated
pure/deterministic code. Ring 3 builds the oracle for the driver class: a
**recorded register-access (MMIO) trace**, replayed C-vs-Rust.

## Method

A driver's MMIO accessors (`readl`/`writel` on an ioremap'd base) are the seam.
`reg_read`/`reg_write` stand in for them, backed by a **software device model**
(hardware QEMU doesn't have), which records every access. The subject is the
canonical driver hot path — stage the operand, issue the command, poll status
until ready, read the result:

```c
u32 mockdev_xfer(struct regmodel *m, u32 input) {
    reg_write(m, REG_DATA, input);
    reg_write(m, REG_CMD, CMD_START);
    while (reg_read(m, REG_STATUS) & STATUS_BUSY) ;   /* poll */
    return reg_read(m, REG_DATA);
}
```

Haiku transplanted it to Rust ($0.0013). The gate drives 256 transfers through
the C reference and the Rust candidate against the deterministic device model,
records the **full register-access trace** of each (2,048 accesses), and asserts
bit-identical.

```
correct: MOCKDEV_PROBE: cases=256 ref_len=2048 cand_len=2048
         ref_hash=0xf7324856c134693f cand_hash=0xf7324856c134693f firstdiff=-1  DIFF_PASS
```

## Why the trace, not the return value — the sharp result

The negative control is the "skip the status poll" variant:

```rust
reg_write(m, REG_DATA, input);
reg_write(m, REG_CMD, CMD_START);
// poll removed
reg_read(m, REG_DATA)
```

In this model the result is set synchronously at command time, so **the skip-poll
driver returns the exact same value** for every input. A return-value differential
would PASS it. But it is a real bug — on real hardware, reading before the device
signals ready gets garbage — and the **trace oracle catches it** because the
register-access sequence is missing its STATUS reads:

```
wrong:   MOCKDEV_PROBE: cases=256 ref_len=2048 cand_len=1024
         ref_hash=0xf732...693f cand_hash=0x5008...a017 firstdiff=2 verdict=DIFF_FAIL
         at trace[2] ref=R REG_STATUS->BUSY   cand=R REG_DATA->result
```

The wrong driver made **half the register accesses** (1024 vs 2048 — no STATUS
polling), and the trace diverged at index 2: where the correct driver polls
STATUS, the buggy one reads DATA. The **return values are identical by
construction** (the model sets the result synchronously at command time), so a
value-only differential passes it; only the register trace catches it.

This is the whole point: for drivers, **what you do to the device is the
correctness criterion, not what you return.** The recorded-I/O trace oracle judges
exactly that, and it is strictly stronger than comparing outputs — while a wrong
register program neither crashes nor races, so boot-survival + KCSAN pass it.

## What this unlocks

This is the capability the research flagged as the highest-ROI missing piece. It
turns the ~73% weakly-gated driver mass from "boots" into "programs its hardware
identically to the C it replaced." For a real in-tree driver the only change is
the seam: record the actual `readl`/`writel` trace (register offsets + values +
order) from the stock driver under a workload, replay to the transplant. The
oracle machinery — deterministic responses, trace-and-hash, both-impls-linked,
first-divergence localization — is exactly this prototype.

## Honest limits

- The device is a software model, not real hardware — because QEMU has no device
  behind these registers. This proves the *oracle mechanism*; a real driver adds
  recording the actual MMIO trace (and mocking the device's read responses from
  that recording), not new oracle logic.
- Coverage is the input script's coverage — a strong differential test, not a
  proof. But it is a per-driver correctness gate where the kernel had none.

## Status

- Recorded register-access differential oracle: built and proven. ✅
- Correct driver transplant accepted (2,048-access trace bit-identical to C). ✅
- A wrong register program that returns IDENTICAL values REJECTED on its trace —
  the case a value-only check would miss and the weak gate would pass. ✅
- The 73%-unlocking capability now exists as a working prototype. ✅
