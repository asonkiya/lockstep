# The T3 executor — recordable vs per-function effects

The router sends effectful functions to T3. But "effectful" is two populations
with completely different economics, and the executor's job is to split them:

- **T3_TRACE** — the effect is an MMIO **register program** (`readl`/`writel`/
  `io*`/`in`/`out`). A driver's correctness *is* this trace, and it is
  **uniformly recordable**: record the C's accesses once (kind/offset/value/
  order), replay any candidate against the frozen trace with no device present.
  This is the recorder — the lever for the ~73% driver mass.
- **T3_EFFECT** — the effect is arbitrary global / RCU / scheduler / per-cpu
  state, or the function calls an opaque helper. There is no uniform trace to
  record; each needs a per-function effect oracle. This is the honest floor of
  the effectful mass.

## Result — classifying the effectful mass (52 functions)

```
T3_TRACE  (recordable MMIO)      0    ← none in this worklist
T3_EFFECT (per-fn effect trace)  52   by state category:
    31  opaque      capable, cs5535_gpio_set_irq, disable_hardirq, fuse_req_hash, ...
     9  irq         generic_handle_irq, ipi_send_single, irq_inject_interrupt, ...
     7  sched/cpu   add_cpu, blk_mq_num_online_queues, cpumask_local_spread, ...
     2  rcu         poll_state_synchronize_rcu, sock_load_diag_module
     3  mixed       irq_percpu_is_enabled, synchronize_hardirq, torture_shuffle_init
```

**Zero of the 52 are directly MMIO.** Exactly the T2 pattern, one tier down: the
recorder's target population — driver register programs — lives in `drivers/`,
and a scalar-leaf harvest across `lib/kernel/mm/fs/net/sound` does not sample it.
The effectful mass here is arbitrary kernel state (irq descriptors, cpu masks,
RCU, per-cpu counters) plus opaque-helper calls — the class that genuinely needs
a per-function effect oracle, itemized here by which state each touches.

## The recorder mechanism (capability proof)

The recorder is ready; it just needs a driver worklist and an MMIO seam. Proven
end to end on a driver hot path:

```
recorder[correct]: PASS  — the transplant replays the recording exactly
recorder[subtle] : PASS  — a value-IDENTICAL skip-the-poll bug is REJECTED on the
                            trace (cand consumes 1024 of 2048 accesses, diverges at
                            trace[2] where the correct program polls STATUS)
```

The subtle leg is the whole point of T3: the buggy candidate returns the *same
value*, so a return-value differential (T0/T1) passes it — and the recorder
catches it because **what the driver does to the device is the correctness, not
what it returns.** (Fixed a latent `set -e` bug in the recorder gate here: the
intentionally-failing subtle run aborted before printing its verdict; now
captured.)

## The finding — and the pointer to the real prize

Two executors (T2, T3) now agree on the same structural fact: **a general
scalar-leaf harvest does not contain the populations the deep oracles target.**
T2 found 0 real struct-mirror cases; T3 finds 0 recordable-MMIO cases. Both
target populations are real and large — struct-reading Tier-B, and the driver
register-program mass — but they live in `drivers/` and in struct-heavy
subsystems, not in a random export-scalar sweep.

The consequence is a concrete next move, not a wall: **the recorder (and the
mirror) want a driver-scoped worklist.** Point the harvester at `drivers/gpio`,
`drivers/ptp`, `drivers/clk` — where Ring 4 already trace-verified a real
in-tree driver (gpio-zevio) bit-for-bit — and the T3_TRACE bucket fills with the
functions the recorder was built to close. The tool is proven; it's aimed at the
wrong worklist.

## Files

`t3_executor.py` — `subclass()` splits effectful into T3_TRACE vs
T3_EFFECT(+state category); `prove_recorder()` drives the recorder gate as the
close mechanism. `t3_result.json` — per-function classification. Companion:
`dream/recorder/` (the mechanism), `dream/ratchet/RING4.md` (the real-driver
trace verification), `T2-RESULTS.md` (the same "wrong worklist" finding one tier
up).
