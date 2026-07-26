# M2 results — single-region transplant, hand-checked

M2 transplants one critical section — the ring buffer's `ring_push`/`ring_count`
from M1 — out of C and into a Rust `SpinLock<T>` guard, by hand, through the
mechanical steps, and puts it through the gate. `python3 m2/gate.py` runs the
whole proof; it is green.

## The transplant

Stock C (`ring_stock.c`) keeps the invariant as a convention:

```c
struct ring { spinlock_t lock; int head; int count; char buf[SIZE]; };
void ring_push(struct ring *r, char c) {
    spin_lock(&r->lock);
    r->buf[r->head % SIZE] = c; r->head++; r->count++;   /* must remember the lock */
    spin_unlock(&r->lock);
}
```

The transplant (`transplant/src/lib.rs`) moves the protected fields *inside* a
`SpinLock<RingFields>` — the R4L `kernel::sync::SpinLock<T>` shape (data owned by
the lock, reached only through a `Guard` that unlocks on drop). The invariant
stops being something a reviewer must check and becomes something the type system
enforces: `RingFields` is unreachable without a guard, and a guard only exists
after `lock()`.

```rust
pub fn push(&self, c: u8) {
    let mut g = self.inner.lock();          // guard scope == critical section
    g.with_mut(|f| { let h = f.head % SIZE; f.buf[h] = c; f.head += 1; f.count += 1; });
}
```

## The gate (design.md §4 M2 proof)

| # | leg | oracle | result |
|---|-----|--------|--------|
| 1 | stock C race-clean baseline | clang `-fsanitize=thread`, 4 writers | 0 races, count exact |
| 2 | transplant functional ("KUnit green") | real threads, 4×50k pushes | count = 200000, exact |
| 3 | transplant race-clean | **loom**, exhaustive interleavings | no race |
| 4 | **negative control: dropped lock REJECTED** | loom | race found, transplant rejected |
| 5 | type-level guarantee (R4L bonus) | rustc | dropped lock does not compile |

**loom** is the userspace stand-in for KCSAN, and stronger: it *exhaustively*
explores thread interleavings rather than sampling under load. Leg 3 proves the
transplant is data-race free across every schedule; leg 2 proves it actually
works under real contention.

## The negative control is not vacuous

Leg 4 is the crux. `push_racy` is `push` with the lock dropped — it touches the
fields with no guard held. loom rejects it, and the test asserts it rejects *for
the right reason*:

```
Causality violation: Concurrent write accesses to `UnsafeCell`.
```

The test checks the panic message contains `Concurrent` + `UnsafeCell`, so a
transplant can't pass by failing for some unrelated reason (the M1
negative-control discipline). A vacuous "everything is fine" gate cannot pass leg 4.

Leg 5 is the deeper Rust-for-Linux point: in C, "dropped lock" is a judgment call
on every field access; in the transplant it is `error[E0616]: field is private` —
there is no expression that reaches a protected field without the lock. `gate.py`
compiles `neg_compile.rs` against the built rlib and confirms rustc rejects it.

## Scope and what's deferred

This is the **hand-checked, userspace** realization — M2's stated scope ("by
hand"). Faithful to M1's methodology (the sanitizer analog now, the in-kernel
gate deferred). Deferred to the in-kernel step (folded into M3's real-region
work): building the transplant as an actual Rust-for-Linux module and running it
under the M0 QEMU + KCSAN/lockdep harness, and syzkaller as the adversarial load.
loom gives a *stronger* race guarantee (exhaustive) but on a hand-modeled
`SpinLock`; the in-kernel run swaps in `kernel::sync::SpinLock<T>` and the real
KCSAN.

## M2 status: done

- One critical section transplanted C → Rust `SpinLock<T>`, mechanically. ✅
- Gate green: stock-clean, transplant race-clean (exhaustive) + functional. ✅
- Negative control (dropped lock) REJECTED, for a genuine data race. ✅
- Bonus: the invariant is type-enforced — the dropped lock won't compile. ✅

Next: **M3** — the model selects the abstraction and produces the region rewrite
from the IR + R4L catalog, same gate; a wrong one is rejected.
