//! M2 — a single critical section transplanted from C into a Rust `SpinLock<T>`.
//!
//! Stock C (see `../ring_stock.c`):
//! ```c
//! struct ring { spinlock_t lock; int head; int count; char buf[SIZE]; };
//! void ring_push(struct ring *r, char c) {
//!     spin_lock(&r->lock);
//!     r->buf[r->head % SIZE] = c; r->head++; r->count++;
//!     spin_unlock(&r->lock);
//! }
//! ```
//!
//! The transplant does not mimic the C — it moves the protected fields *inside*
//! a `SpinLock<RingFields>`, so the invariant "these fields are only touched with
//! the lock held" stops being a convention the reviewer must check and becomes a
//! fact the type system enforces: `RingFields` is unreachable without a `Guard`,
//! and a `Guard` only exists after `lock()`. This mirrors R4L's
//! `kernel::sync::SpinLock<T>` (data owned by the lock, reached through a guard).
//!
//! Verified two ways (see `tests/loom_checks.rs`): under `--cfg loom` the model
//! checker exhaustively explores interleavings and confirms the transplant is
//! data-race free, while the negative control (`push_racy`, a deliberately
//! dropped lock) is *rejected* — loom reports the race, exactly as KCSAN would.

// ---- loom / std primitive shim: one code path, checked under loom, real under std ----
#[cfg(loom)]
pub(crate) use loom::cell::UnsafeCell;
#[cfg(loom)]
pub(crate) use loom::sync::atomic::{AtomicBool, Ordering};

#[cfg(not(loom))]
pub(crate) use std::sync::atomic::{AtomicBool, Ordering};

/// std stand-in for `loom::cell::UnsafeCell`, same `with_mut` closure API so the
/// `SpinLock` body is identical under both builds.
#[cfg(not(loom))]
#[derive(Debug)]
pub(crate) struct UnsafeCell<T>(std::cell::UnsafeCell<T>);

#[cfg(not(loom))]
impl<T> UnsafeCell<T> {
    pub(crate) fn new(v: T) -> Self {
        Self(std::cell::UnsafeCell::new(v))
    }
    pub(crate) fn with_mut<R>(&self, f: impl FnOnce(*mut T) -> R) -> R {
        f(self.0.get())
    }
}

/// A spinlock that *owns* its protected data — the R4L `SpinLock<T>` shape.
/// `T` is only reachable through a `Guard`, which only exists while the lock is
/// held; there is no safe path to the fields without locking.
pub struct SpinLock<T> {
    locked: AtomicBool,
    data: UnsafeCell<T>,
}

// Safe to share across threads: all access is serialized by `locked`.
unsafe impl<T: Send> Sync for SpinLock<T> {}
unsafe impl<T: Send> Send for SpinLock<T> {}

impl<T> SpinLock<T> {
    pub fn new(value: T) -> Self {
        Self {
            locked: AtomicBool::new(false),
            data: UnsafeCell::new(value),
        }
    }

    /// Acquire the lock, returning a guard that releases on drop.
    pub fn lock(&self) -> Guard<'_, T> {
        while self
            .locked
            .compare_exchange(false, true, Ordering::Acquire, Ordering::Relaxed)
            .is_err()
        {
            #[cfg(loom)]
            loom::thread::yield_now();
            #[cfg(not(loom))]
            std::hint::spin_loop();
        }
        Guard { lock: self }
    }

    /// NEGATIVE CONTROL ONLY — the "dropped lock." Reaches the data WITHOUT
    /// acquiring the lock, i.e. the transplant a careless rewrite would produce.
    /// Present only under loom, so the model checker can prove it races; there is
    /// no way to reach this on the normal build.
    #[cfg(loom)]
    #[doc(hidden)]
    pub fn racy_with_mut<R>(&self, f: impl FnOnce(*mut T) -> R) -> R {
        self.data.with_mut(f)
    }
}

/// RAII guard: deref-free field access via a closure (matches the loom cell API),
/// unlocks on drop.
pub struct Guard<'a, T> {
    lock: &'a SpinLock<T>,
}

impl<'a, T> Guard<'a, T> {
    pub fn with<R>(&self, f: impl FnOnce(&T) -> R) -> R {
        self.lock.data.with_mut(|p| f(unsafe { &*p }))
    }
    pub fn with_mut<R>(&mut self, f: impl FnOnce(&mut T) -> R) -> R {
        self.lock.data.with_mut(|p| f(unsafe { &mut *p }))
    }
}

impl<'a, T> Drop for Guard<'a, T> {
    fn drop(&mut self) {
        self.lock.locked.store(false, Ordering::Release);
    }
}

// ---- the transplanted ring buffer ----

pub const SIZE: usize = 64;

/// The fields the C `struct ring` kept behind `spinlock_t lock` — now owned by
/// the `SpinLock` and unreachable without it.
pub struct RingFields {
    head: usize,
    count: usize,
    buf: [u8; SIZE],
}

pub struct Ring {
    inner: SpinLock<RingFields>,
}

impl Default for Ring {
    fn default() -> Self {
        Self::new()
    }
}

impl Ring {
    pub fn new() -> Self {
        Ring {
            inner: SpinLock::new(RingFields {
                head: 0,
                count: 0,
                buf: [0; SIZE],
            }),
        }
    }

    /// Transplant of `ring_push`: the whole body runs inside the guard scope.
    pub fn push(&self, c: u8) {
        let mut g = self.inner.lock();
        g.with_mut(|f| {
            let h = f.head % SIZE;
            f.buf[h] = c;
            f.head += 1;
            f.count += 1;
        });
    }

    /// Transplant of `ring_count`.
    pub fn count(&self) -> usize {
        let g = self.inner.lock();
        g.with(|f| f.count)
    }

    /// NEGATIVE CONTROL — `push` with the lock dropped. Touches the fields with
    /// no guard held; loom must report the data race (see the rejected test).
    #[cfg(loom)]
    pub fn push_racy(&self, c: u8) {
        self.inner.racy_with_mut(|p| {
            let f = unsafe { &mut *p };
            let h = f.head % SIZE;
            f.buf[h] = c;
            f.head += 1;
            f.count += 1;
        });
    }
}
