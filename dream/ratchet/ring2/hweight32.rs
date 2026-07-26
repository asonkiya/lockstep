#![no_std]
#![no_main]
#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }

// leaf: cgir_sw_hweight32

#[no_mangle]
pub extern "C" fn cgir_sw_hweight32(mut w: u32) -> u32 {
    #[cfg(CONFIG_ARCH_HAS_FAST_MULTIPLIER)]
    {
        w = w.wrapping_sub((w >> 1) & 0x55555555);
        w = (w & 0x33333333).wrapping_add((w >> 2) & 0x33333333);
        w = (w.wrapping_add(w >> 4)) & 0x0f0f0f0f;
        return (w.wrapping_mul(0x01010101)) >> 24;
    }

    #[cfg(not(CONFIG_ARCH_HAS_FAST_MULTIPLIER))]
    {
        let mut res = w.wrapping_sub((w >> 1) & 0x55555555);
        res = (res & 0x33333333).wrapping_add((res >> 2) & 0x33333333);
        res = (res.wrapping_add(res >> 4)) & 0x0f0f0f0f;
        res = res.wrapping_add(res >> 8);
        return (res.wrapping_add(res >> 16)) & 0x000000ff;
    }
}
