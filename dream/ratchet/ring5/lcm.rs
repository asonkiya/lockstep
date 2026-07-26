#![no_std]
#![no_main]
#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }

// fleet: cgir_lcm

#[no_mangle]
pub extern "C" fn cgir_lcm(a: u64, b: u64) -> u64 {
    if a == 0 || b == 0 {
        return 0;
    }

    let gcd = {
        let mut x = a;
        let mut y = b;
        while y != 0 {
            let temp = y;
            y = x % y;
            x = temp;
        }
        x
    };

    (a / gcd).wrapping_mul(b)
}
