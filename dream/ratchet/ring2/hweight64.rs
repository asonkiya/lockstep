#![no_std]
#![no_main]
#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }

// leaf: cgir_sw_hweight64
// (model output; the dead 32-bit `cfg` path + its #[no_mangle] cgir_sw_hweight32
// helper were removed — no_mangle symbols aren't DCE'd, so the cfg-inactive
// helper still collided with hweight32.rs at link. A real weaver finding: model
// transplants that mirror C's structural split can emit duplicate exported
// symbols; the batch must dedupe or one-crate them.)

#[no_mangle]
pub extern "C" fn cgir_sw_hweight64(w: u64) -> u64 {
    let mut res = w.wrapping_sub((w >> 1) & 0x5555555555555555u64);
    res = (res & 0x3333333333333333u64).wrapping_add((res >> 2) & 0x3333333333333333u64);
    res = (res.wrapping_add(res >> 4)) & 0x0F0F0F0F0F0F0F0Fu64;
    res = res.wrapping_add(res >> 8);
    res = res.wrapping_add(res >> 16);
    (res.wrapping_add(res >> 32)) & 0x00000000000000FFu64
}
