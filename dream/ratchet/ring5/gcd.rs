#![no_std]
#![no_main]
#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }

// fleet: cgir_gcd
#[no_mangle]
pub extern "C" fn cgir_gcd(mut a: u64, mut b: u64) -> u64 {
    while b != 0 {
        let tmp = b;
        b = a % b;
        a = tmp;
    }
    a
}
