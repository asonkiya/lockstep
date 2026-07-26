#![no_std]
#![no_main]
#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }

// leaf: cgir_int_pow

#[no_mangle]
pub extern "C" fn cgir_int_pow(mut base: u64, mut exp: u32) -> u64 {
	let mut result = 1u64;

	while exp != 0 {
		if (exp & 1) != 0 {
			result = result.wrapping_mul(base);
		}
		exp >>= 1;
		base = base.wrapping_mul(base);
	}

	result
}
