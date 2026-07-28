# M3 results — model-synthesized transplant, same gate

M3 hands the transplant to the model: given the extracted concurrency IR + the
R4L abstraction catalog + the scaffold API, a **cheap model (Haiku)** selects the
abstraction and writes the region rewrite; the M2 gate decides. No hand-written
candidate code in the loop.

## The run

```
IR: protects={"ring": {"lock": ["buf", "count", "head"]}}
attempt 1: selected abstraction: 'spin_lock/unlock around fields -> SpinLock<Fields> + guard scope'
  ✓ attempt 1: build+functional+loom all green
  ✓ dropped lock REJECTED (concurrent-access race)
M3: PASS — cost=$0.0028, attempts=1
```

**First attempt, $0.0028.** The model:
1. **selected correctly** from the catalog (the IR shows spin_lock around fields
   → `SpinLock<Fields>` + guard scope) — the selection is machine-checked, a
   wrong pick is rejected before compilation;
2. **produced a correct region rewrite** (`winner_region.rs`): protected fields
   inside the `SpinLock`, the whole critical section in one guard scope, exact C
   semantics (`buf[head % SIZE] = c; head += 1; count += 1`).

Pipeline: `synthesize.py --live` — extract IR from the stock C (`extraction/extract.py`
on `transplant/ring_stock.c`), build the prompt (IR + catalog + scaffold API), sample,
install the candidate as `harness/src/region.rs`, gate. Up to k=3 attempts with
failure feedback; 1 was needed.

## The gate (same battery as M2, per candidate)

| leg | oracle | winner |
|-----|--------|--------|
| abstraction selection matches the IR | string check vs catalog | ✓ |
| compiles against the fixed scaffold | rustc | ✓ |
| functional under real contention | 4 threads × 50k, exact count | ✓ |
| race-free, exhaustively | loom (all interleavings) | ✓ |
| **negative control: dropped lock** | sabotaged scaffold + loom | **REJECTED** ✓ |

The scaffold (`harness/src/lib.rs`) is the R4L stand-in: the model does not
reinvent `SpinLock` — R4L ships it — the model *uses* it. The negative control
mechanically deletes the scaffold's marked lock acquisition (so `lock()` hands
out a guard without locking) and re-runs loom on the **model's own accepted
candidate**: `Causality violation: Concurrent write accesses to UnsafeCell` →
rejected. Works against any candidate shape, since every candidate routes
through the guard.

## "A wrong one is rejected" — all three failure modes

- **Concurrency bug (dropped lock):** loom kills it — above.
- **Semantics bug:** a wrong candidate (`count += 2`, race-free but incorrect)
  was pushed through the same gate → killed at the **functional** leg
  (400000 ≠ 200000). The battery rejects at the leg that can see the bug.
- **Structural bug (lockless field access):** cannot even compile — fields are
  unreachable without the guard (proven in M2, `E0616`).

And the harness itself was proven non-vacuous **before** any model output was
trusted: `synthesize.py --self-test` gates the committed reference impl (must
PASS) and the sabotage (must REJECT) — the M1/M2 discipline.

## Scope

Userspace realization, faithful to M1/M2's methodology: loom is the exhaustive
KCSAN analog, the scaffold is the `kernel::sync::SpinLock<T>` analog. Deferred
(the in-kernel step): real R4L module under M0's QEMU+KCSAN/lockdep harness,
syzkaller as adversarial load, and a *kernel* region (the M1 sweep's `ptp_mock`
is the candidate — its regions call `timecounter_*` helpers, so the transplant
needs the dependency story M4's dependency-ordered sweep introduces).

## M3 status: done

- Model selects the abstraction from the catalog, from the IR. ✅ (checked)
- Model produces the region rewrite; full battery green, attempt 1, $0.0028. ✅
- Wrong transplants rejected: concurrency (loom), semantics (functional),
  structural (rustc). ✅
- Harness proven able to accept AND reject before the model ran. ✅

Next: **M4** — scale to a whole subsystem, regions dependency-ordered, the way
CGIR swept SQLite.
