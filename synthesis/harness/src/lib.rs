//! M3 harness scaffold — the FIXED abstraction library the model's candidate
//! compiles against. Mirrors R4L's `kernel::sync::SpinLock<T>` (data owned by
//! the lock, reached only through a guard); the model does not reinvent this —
//! R4L ships it — the model *selects* it from the catalog and writes the region
//! rewrite (`src/region.rs`, overwritten by synthesize.py per candidate).
//!
//! The gate's negative control mechanically sabotages the marked acquisition
//! below (the "dropped lock") and re-runs loom: the model's own accepted
//! candidate must then be REJECTED. See ../synthesize.py.

// ---- loom / std primitive shim (same one proven in m2) ----
#[cfg(loom)]
pub(crate) use loom::cell::UnsafeCell;
#[cfg(loom)]
pub(crate) use loom::sync::atomic::{AtomicBool, Ordering};

#[cfg(not(loom))]
pub(crate) use std::sync::atomic::{AtomicBool, Ordering};

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

/// The R4L-shaped spinlock: owns its data, `T` reachable only through a `Guard`.
pub struct SpinLock<T> {
    locked: AtomicBool,
    data: UnsafeCell<T>,
}

unsafe impl<T: Send> Sync for SpinLock<T> {}
unsafe impl<T: Send> Send for SpinLock<T> {}

impl<T> SpinLock<T> {
    pub fn new(value: T) -> Self {
        Self {
            locked: AtomicBool::new(false),
            data: UnsafeCell::new(value),
        }
    }

    pub fn lock(&self) -> Guard<'_, T> {
        // SABOTAGE-BEGIN (negative control deletes this acquisition; the guard
        // is then handed out without the lock ever being taken)
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
        // SABOTAGE-END
        Guard { lock: self }
    }
}

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

// ---- the model's region rewrite lands here ----
mod region;
pub use region::*;
