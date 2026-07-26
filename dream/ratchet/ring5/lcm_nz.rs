#![no_std]
#![no_main]
#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }

// fleet: cgir_lcm_not_zero
#[no_mangle]
pub extern "C" fn cgir_lcm_not_zero(a: u64, b: u64) -> u64 {
    fn gcd(mut x: u64, mut y: u64) -> u64 {
        while y != 0 {
            let t = y;
            y = x % y;
            x = t;
        }
        x
    }

    fn lcm(x: u64, y: u64) -> u64 {
        if x == 0 || y == 0 {
            return 0;
        }
        let g = gcd(x, y);
        (x / g).wrapping_mul(y)
    }

    let l = lcm(a, b);
    if l != 0 {
        l
    } else if a != 0 {
        a
    } else {
        b
    }
}
