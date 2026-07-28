# M4 breadth results — a real driver's whole locked cluster, model-transplanted, in-kernel

Depth proved one region. Breadth transplants the **entire locked surface of a
real existing kernel driver** — `drivers/ptp/ptp_mock.c`, the mock PTP clock:
four regions (`adjfine`, `adjtime`, `settime64`, `gettime64`) sharing one
`spinlock_t` protecting `tc`/`cc` — synthesized region-by-region by Haiku,
assembled into one object, judged as a cluster inside a booting SMP kernel.
`./gate.sh all` is green with the **strict criterion: the negative control must
be rejected by KCSAN itself** (no functional fallback — the depth leg's pacing
lesson made that demandable).

## The pipeline (extractor → per-region synthesis → assembled cluster → one gate)

1. **Worklist** (`manifest.py`): the M1 extractor on the vendored driver finds
   all 5 lock regions; 4 are the transplant cluster, 5 glue functions are
   skipped *with named reasons* (init path, wrapper, callback, accessors).
   `protects = {mock_phc.lock: [cc, clock, info, tc]}`.
2. **Synthesis** (`synthesize_phc.py --live`): each region prompted with its
   REAL C body from the driver + the cluster IR + the catalog + a fixed prelude
   (`#[repr(C)]` mirrors of `timecounter`/`cyclecounter` — layout
   `BUILD_BUG_ON`-guarded C-side — externs for the kernel's real
   `_raw_spin_lock`, `timecounter_read`, `timecounter_init`, and the Guard).
   **4/4 regions on attempt 1, $0.0084 total.** All faithful, including the
   subtle ones: adjfine keeps `timecounter_read` *inside* the lock (flush at old
   mult) with the correct signed→u32 mult cast; adjtime is
   `nsec.wrapping_add_signed(delta)`; gettime's tail expression evaluates before
   the guard drops.
3. **Assembly**: prelude + 4 winners → one freestanding aarch64 object → kbuild
   `.o_shipped` → vmlinux. The dependency story that made ptp_mock "hard" is
   just externs: Rust calls the kernel's exported `timecounter_*` under its own
   guard, and those call back into the C cyclecounter callback — a
   Rust→C→C-callback chain inside a Rust-held kernel spinlock.
4. **The probe** (`lockstep_phc_probe.c`): five hammers drive all four regions
   at once (2× gettime, adjtime +1000ns, adjfine sweeping ±1000 ppm, settime
   jumping forward 10s) + the locked instrumented C reader (KCSAN bait). The
   functional oracle is a real clock invariant: **gettime must be monotone**
   under full cluster concurrency; every hammer must land its exact op count.

## Verdicts

| leg | functional | KCSAN on probe |
|-----|-----------|----------------|
| stock (driver's C, verbatim) | PASS — 300,000/300,000 monotone, all ops exact | 0 |
| **rewrite (Haiku's 4-region cluster)** | **PASS — identical profile to stock** | **0** |
| sabotaged (all four regions unlocked) | FAIL — **clock went backwards 98×** | **35 reports, 24 naming probe/transplant frames** |

The rewrite leg is the breadth claim: ~300s of 4-CPU contention across four
different critical sections on one lock, ~610k locked instrumented reads
interleaved, 0 races, lockdep silent, and the mock PTP clock behaves
identically to stock — monotone through 2,000 forward settime jumps, 150k mult
adjustments, 150k adjtimes.

## The negative control names the defendant

```
BUG: KCSAN: data-race in timecounter_read+0xe0/0x118
race at unknown origin, with read to 0xffffaf23b4139048 of 8 bytes by task 103 on cpu 0:
 timecounter_read+0xe0/0x118
 lockstep_phc_adjfine+0x30/0x48
value changed: 0x0000012c5b978ebe -> 0x0000012c5b97968e
```

KCSAN convicts **the model's own transplanted function by name** —
`lockstep_phc_adjfine` in the racing stack, tearing timecounter state through
the kernel's instrumented `timecounter_read` (72 report frames). This is
stronger than the depth leg's reader-side-only detection: the racing accesses
here go through *instrumented kernel C called from Rust*, so KCSAN sees both
the effect and the caller. The functional corroboration is vivid: a PTP clock
whose regions drop their lock **runs backwards** (98 monotonicity violations
from torn `cycle_last`/`nsec`).

Background findings (baseline class, pre-probe or generic): `_find_first_bit`/
`_find_next_and_bit` cpumask/IRQ noise — named, scoped out by the probe filter.

## Honest accounting

- The cluster runs under the gate probe (replicating `mock_phc_create`'s init
  minus PTP-class registration — no userspace in this boot to drive
  `/dev/ptp*`). The regions, the lock, the timecounter calls, and the
  concurrency are the driver's real ones; the registration plumbing is not
  under test.
- "Dependency-ordered sweep": this cluster is one strongly-connected component
  (four regions, one lock, shared state) — the right unit is the cluster,
  transplanted and judged together. A multi-cluster subsystem (and syzkaller as
  load for syscall-facing regions) is the remaining M4 scale-out.
- The GPIO candidates from the M1 sweep can't run this gate (SoC/southbridge
  hardware); ptp_mock was chosen *because* its "hardware" is `ktime_get_raw()`.

## Status

- Real driver, whole locked cluster (4 regions), model-transplanted at
  **$0.0084**, 4/4 first-attempt. ✅
- Accepted in-kernel: KCSAN + lockdep silent, clock invariants hold, profile
  identical to stock. ✅
- Dropped-lock cluster **rejected by KCSAN by name**, corroborated by a clock
  that runs backwards. ✅
