// ---- fixed prelude (scaffold; model-written regions follow) ----
#![no_std]
#![no_main]

#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! {
    loop {}
}

use core::ffi::c_void;

// The kernel's real out-of-line spinlock API (lockdep-instrumented) and the
// exported timecounter functions the regions call (real kernel C, in vmlinux).
extern "C" {
    fn _raw_spin_lock(lock: *mut c_void);
    fn _raw_spin_unlock(lock: *mut c_void);
    fn timecounter_read(tc: *mut Timecounter) -> u64;
    fn timecounter_init(tc: *mut Timecounter, cc: *const Cyclecounter, start_tstamp: u64);
}

/// RAII critical section over the kernel lock: exists => the lock is held.
pub struct Guard {
    lock: *mut c_void,
}

impl Guard {
    pub fn new(lock: *mut c_void) -> Guard {
        // [sabotaged: acquisition removed]
        Guard { lock }
    }
}

impl Drop for Guard {
    fn drop(&mut self) {
        // [sabotaged: release removed]
    }
}

/// Mirrors of include/linux/timecounter.h (layout BUILD_BUG_ON-guarded on the
/// C side: cyclecounter 24 bytes, timecounter 40 bytes on LP64 arm64).
#[repr(C)]
pub struct Cyclecounter {
    pub read: Option<extern "C" fn(*mut Cyclecounter) -> u64>,
    pub mask: u64,
    pub mult: u32,
    pub shift: u32,
}

#[repr(C)]
pub struct Timecounter {
    pub cc: *mut Cyclecounter,
    pub cycle_last: u64,
    pub nsec: u64,
    pub mask: u64,
    pub frac: u64,
}

// ptp_mock.c's constants
pub const MOCK_PHC_CC_MULT: u32 = 0x8000_0000; // 1 << 31
pub const MOCK_PHC_FADJ_SHIFT: u32 = 9;
pub const MOCK_PHC_FADJ_DENOMINATOR: i64 = 15625;
// ---- end prelude ----

// abstraction: spin_lock/unlock around fields -> SpinLock<Fields> + guard scope

#[no_mangle]
pub extern "C" fn lockstep_phc_adjfine(tc: *mut Timecounter, cc: *mut Cyclecounter, lock: *mut c_void, scaled_ppm: i64) -> i32 {
    let adj: i64 = (scaled_ppm << MOCK_PHC_FADJ_SHIFT) / MOCK_PHC_FADJ_DENOMINATOR;

    let _g = Guard::new(lock);
    unsafe {
        timecounter_read(tc);
        (*cc).mult = ((MOCK_PHC_CC_MULT as i64) + adj) as u32;
    }

    0
}

// abstraction: spin_lock/unlock around fields -> SpinLock<Fields> + guard scope

#[no_mangle]
pub extern "C" fn lockstep_phc_adjtime(tc: *mut Timecounter, lock: *mut c_void, delta: i64) -> i32 {
    let _g = Guard::new(lock);
    unsafe {
        (*tc).nsec = (*tc).nsec.wrapping_add_signed(delta);
    }
    0
}

// abstraction: spin_lock/unlock around fields -> SpinLock<Fields> + guard scope

#[no_mangle]
pub extern "C" fn lockstep_phc_settime64(tc: *mut Timecounter, cc: *mut Cyclecounter, lock: *mut c_void, ns: u64) -> i32 {
    let _g = Guard::new(lock);
    unsafe {
        timecounter_init(tc, cc, ns);
    }
    0
}

// abstraction: spin_lock/unlock around fields -> SpinLock<Fields> + guard scope

#[no_mangle]
pub extern "C" fn lockstep_phc_gettime64(tc: *mut Timecounter, lock: *mut c_void) -> u64 {
    let _g = Guard::new(lock);
    unsafe { timecounter_read(tc) }
}
