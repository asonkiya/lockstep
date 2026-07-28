# M4 (depth leg) results — the model's transplant, verified in-kernel by KCSAN

The claim this leg exists for: **a model-written Rust region, linked into
vmlinux, taking the kernel's real spinlock, judged by the kernel's own
sanitizers inside a booting SMP Linux — accepted when correct, REJECTED BY KCSAN
when the lock is dropped.** `./gate.sh all` runs the whole thing; it is green.

## The pipeline (all machinery, no hands on the code)

1. **IR** — `extraction/extract.py` on the original region: `protects = {ring.lock:
   [buf, count, head]}`.
2. **Synthesis** — `synthesize_kernel.py --live`: IR + R4L catalog + a fixed
   freestanding prelude (externs for the kernel's out-of-line
   `_raw_spin_lock`/`_raw_spin_unlock` + a `Guard` RAII + `#[repr(C)]` fields
   mirror) → **Haiku**. First attempt, **$0.0020**: correct catalog selection,
   and a textbook region (`winner_kernel.rs`): guard first, `unsafe` deref inside
   the guard scope, `get_unchecked_mut` (no panic path), exact C semantics.
3. **Link** — rung-4 path: `rustc --target aarch64-unknown-none-softfloat
   --emit=obj` → `.o_shipped` → kbuild → vmlinux. No CONFIG_RUST machinery.
4. **Judge** — QEMU arm64, `-smp 4`, the M0 KCSAN+lockdep config. The probe
   (`probe/lockstep_probe.c`) binds 3 pusher kthreads + 1 reader to separate
   CPUs; pushers call the transplant seam 1M times each; the reader takes the
   SAME real spinlock and reads the protected fields with plain (=instrumented)
   loads. Functional verdict on the console; KCSAN/lockdep judge concurrently.

## The verdicts (run 2, paced probe)

| leg | target | functional | KCSAN on probe | lockdep |
|-----|--------|-----------|----------------|---------|
| stock | C `spin_lock` | PASS — 3,000,000 exact | 0 | silent |
| **rewrite** | **Haiku's Rust** | **PASS — 3,000,000 exact** | **0** | **silent** |
| sabotaged | same Rust, `Guard::new` never locks | FAIL — 2,998,722 (1,278 lost) | **37 reports** | silent |

The rewrite leg: ~200s of sustained 4-CPU contention, ~1.3M locked reader
passes interleaving with 3M Rust-side critical sections on the same lock class
— zero data races, zero lock-ordering complaints, exact count. lockdep tracked
every Rust acquisition (via `_raw_spin_lock`'s internal `lock_acquire`).

## The negative control — KCSAN catches the dropped lock in the act

```
BUG: KCSAN: data-race in lockstep_reader+0x40/0x100
race at unknown origin, with read to 0xffff...9028 of 8 bytes by task 100 on cpu 0:
  lockstep_reader+0x40/0x100
value changed: 0x00000000000027fb -> 0x00000000000027fe
```

The reader is **inside `spin_lock`** when its watchpoint sees `count` jump by 3
— three unlocked Rust pushers wrote through the "critical section" mid-read.
"Race at unknown origin" is precisely the designed detection path: the Rust
object is uninstrumented, so KCSAN never sees the writer — but its watchpoints
on the instrumented C reader catch the value changing under a held lock. Same
report class as the M0 baseline's `data_push_tail` findings. 37 reports across
both `head` and `count`, plus 1,278 lost updates as the independent functional
signal. REJECTED, twice over.

Background noise, named and scoped out (the M0 "no NEW findings" discipline):
`_find_first_bit`/`_find_next_and_bit` (cpumask/IRQ, in the M0 baseline set,
fired before the probe started) and `arch_dup_task_struct` (task_struct
snapshot during fork — triggered by the probe *spawning* kthreads, generic
kernel code, no lockstep frame). The gate counts only reports naming
`lockstep_*` symbols.

## The harness lesson (run 1 → run 2)

Run 1's sabotaged leg was rejected by the functional signal alone (9,338 lost
updates) — **KCSAN saw nothing**. Reason: with no lock to wait on, the buggy
run finished in 0.85s, and KCSAN is a *sampling* detector (~1 watchpoint per
4000 accesses) — the reader landed ~6k instrumented accesses in the window, ≈1
watchpoint. A fast bug can outrun a sampler.

Fix in the probe, not the oracle: `udelay(10)` per push paces every leg
identically, holding the stress window open (~60s sabotaged) → ~hundreds of
reader watchpoints, each an ~80µs trap that unlocked writers walk into. Run 2:
37 KCSAN reports. Lesson recorded because it generalizes: **the in-kernel gate
must budget dwell time for KCSAN's sampling, or a racy transplant that is also
*fast* can slip past the race oracle** (the functional oracle still catches
lost updates, but only when the workload makes them).

## Honest accounting

- The region is the M1–M3 ring buffer (kernel-style, but ours), transplanted
  into a *partial-migration* shape that is the realistic unit: the struct stays
  C-owned, Rust takes one critical section, C keeps another, both on the same
  real lock. A region from existing kernel code (`ptp_mock`) + the breadth sweep
  is the remaining M4 work.
- The `Guard` is an R4L-*shaped* wrapper over the kernel's real lock API, not
  `kernel::sync::SpinLock<T>` itself (that needs CONFIG_RUST + its rustc/bindgen
  pin). The lock, the lockdep instrumentation, KCSAN, the SMP kernel — all real.
- syzkaller as adversarial load: still deferred (our probe IS the adversarial
  load for this region; syzkaller matters when regions have syscall surface).

## Status

- Model-written Rust region linked into vmlinux, real spinlock, boot-verified. ✅
- Accepted by KCSAN + lockdep + functional under sustained SMP contention. ✅
- Dropped-lock negative control REJECTED **by KCSAN itself** (37 reports),
  independently confirmed by the functional signal. ✅
- Total model cost of the in-kernel transplant: **$0.0020**.
