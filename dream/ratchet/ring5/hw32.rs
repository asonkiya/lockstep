#![no_std]
#![no_main]
#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }

// fleet: cgir_sw_hweight32
#[no_mangle]
pub extern "C" fn cgir_sw_hweight32(w: u32) -> u32 {
    w.count_ones()
}
