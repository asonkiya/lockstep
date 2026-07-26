#![no_std]
#![no_main]
#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }

// fleet: cgir_int_sqrt
#[no_mangle]
pub extern "C" fn cgir_int_sqrt(mut x: u64) -> u64 {
    if x <= 1 {
        return x;
    }
    
    let mut y: u64 = 0;
    let mut m: u64 = 1u64 << ((63 - x.leading_zeros() as u64) & !1);
    
    while m != 0 {
        let b = y + m;
        y >>= 1;
        if x >= b {
            x -= b;
            y += m;
        }
        m >>= 2;
    }
    
    y
}
