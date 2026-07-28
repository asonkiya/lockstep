// SPDX-License-Identifier: GPL-2.0-or-later
//! RFC — the idiomatic Rust-for-Linux destination for ptp_mock (NOT the
//! verified artifact; see patches 1-2 for what was verified, and the cover
//! letter for the gap analysis).
//!
//! This sketch shows where the verified region cluster lands once the missing
//! bindings exist: the four critical sections become methods on a
//! `SpinLock<MockPhcInner>` where the protected state is OWNED by the lock —
//! the invariant the C version keeps by convention ("take phc->lock before
//! touching tc/cc") becomes unrepresentable to violate.
//!
//! Missing binding surface (called out, not papered over):
//!  * `struct timecounter` / `struct cyclecounter` have no Rust abstraction
//!    (this sketch declares the mirror types it needs);
//!  * `ptp_clock_register()` / `struct ptp_clock_info` have no Rust
//!    abstraction — the driver registration half stays C until one exists;
//!  * `ktime_get_raw_ns()` is available via `kernel::time`.

use kernel::{new_spinlock, prelude::*, sync::SpinLock, time::Ktime};

/// Mirror of `struct cyclecounter` pending a proper binding.
struct Cyclecounter {
    mask: u64,
    mult: u32,
    shift: u32,
}

/// Mirror of `struct timecounter` pending a proper binding.
struct Timecounter {
    cycle_last: u64,
    nsec: u64,
    mask: u64,
    frac: u64,
}

const MOCK_PHC_CC_SHIFT: u32 = 31;
const MOCK_PHC_CC_MULT: u32 = 1 << MOCK_PHC_CC_SHIFT;
const MOCK_PHC_FADJ_SHIFT: u32 = 9;
const MOCK_PHC_FADJ_DENOMINATOR: i64 = 15625;

/// The state the C version protects by convention; here the `SpinLock` owns
/// it, so unlocked access does not compile.
struct MockPhcInner {
    tc: Timecounter,
    cc: Cyclecounter,
}

#[pin_data]
pub struct MockPhc {
    #[pin]
    inner: SpinLock<MockPhcInner>,
}

impl MockPhc {
    pub fn new() -> impl PinInit<Self> {
        pin_init!(Self {
            inner <- new_spinlock!(MockPhcInner {
                tc: Timecounter { cycle_last: 0, nsec: 0, mask: u64::MAX, frac: 0 },
                cc: Cyclecounter { mask: u64::MAX, mult: MOCK_PHC_CC_MULT, shift: MOCK_PHC_CC_SHIFT },
            }),
        })
    }

    /// mock_phc_adjfine: flush accumulated time at the old rate, then adjust.
    pub fn adjfine(&self, scaled_ppm: i64) {
        let adj = (scaled_ppm << MOCK_PHC_FADJ_SHIFT) / MOCK_PHC_FADJ_DENOMINATOR;
        let mut inner = self.inner.lock();
        inner.timecounter_read();
        inner.cc.mult = (MOCK_PHC_CC_MULT as i64 + adj) as u32;
    }

    /// mock_phc_adjtime
    pub fn adjtime(&self, delta: i64) {
        let mut inner = self.inner.lock();
        inner.tc.nsec = inner.tc.nsec.wrapping_add_signed(delta);
    }

    /// mock_phc_settime64 (timespec glue stays with the caller)
    pub fn settime(&self, ns: u64) {
        let mut inner = self.inner.lock();
        inner.timecounter_init(ns);
    }

    /// mock_phc_gettime64
    pub fn gettime(&self) -> u64 {
        self.inner.lock().timecounter_read()
    }
}

impl MockPhcInner {
    /// `timecounter_read()` pending a binding: cyc2ns accumulation on top of
    /// CLOCK_MONOTONIC_RAW, exactly the C helper's math.
    fn timecounter_read(&mut self) -> u64 {
        let cycle_now = Ktime::ktime_get_raw().to_ns() as u64;
        let delta = (cycle_now.wrapping_sub(self.tc.cycle_last)) & self.tc.mask;
        self.tc.cycle_last = cycle_now;
        let ns = ((delta as u128 * self.cc.mult as u128) >> self.cc.shift) as u64;
        self.tc.nsec = self.tc.nsec.wrapping_add(ns);
        self.tc.nsec
    }

    fn timecounter_init(&mut self, start_ns: u64) {
        self.tc.cycle_last = Ktime::ktime_get_raw().to_ns() as u64;
        self.tc.nsec = start_ns;
        self.tc.frac = 0;
    }
}
