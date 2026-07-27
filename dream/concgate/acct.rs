// The Rust transplant of the acct critical section — holds the kernel's REAL
// spinlock (out-of-line _raw_spin_lock, lockdep-instrumented) and updates BOTH
// coupled fields inside one guard scope, preserving the mirror==count invariant.
// The concurrency gate's negative control (gate.sh --subtle) narrows this scope
// so `mirror` escapes the lock — the realistic transplant bug KCSAN must catch.
#![no_std]
#![no_main]

#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! {
    loop {}
}

use core::ffi::c_void;

extern "C" {
    fn _raw_spin_lock(lock: *mut c_void);
    fn _raw_spin_unlock(lock: *mut c_void);
}

pub struct Guard {
    lock: *mut c_void,
}
impl Guard {
    pub fn new(lock: *mut c_void) -> Guard {
        unsafe { _raw_spin_lock(lock) };
        Guard { lock }
    }
}
impl Drop for Guard {
    fn drop(&mut self) {
        unsafe { _raw_spin_unlock(self.lock) };
    }
}

#[repr(C)]
pub struct AcctFields {
    pub count: i64,
    pub mirror: i64,
}

#[no_mangle]
pub extern "C" fn cgir_acct_add(f: *mut AcctFields, lock: *mut c_void, delta: i64) {
    // CRITICAL-SECTION-BEGIN
    let _g = Guard::new(lock);
    unsafe {
        (*f).count += delta;
        (*f).mirror += delta;
    }
    // CRITICAL-SECTION-END (both fields updated under the lock)
}
