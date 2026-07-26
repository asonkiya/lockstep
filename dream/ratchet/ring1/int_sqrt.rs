#![no_std]
#![no_main]
#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }

// leaf: cgir_int_sqrt

#[no_mangle]
pub extern "C" fn cgir_int_sqrt(mut x: u64) -> u64 {
	if x <= 1 {
		return x;
	}

	let mut y: u64 = 0;
	let mut m: u64 = 1u64 << ((63u32.wrapping_sub(x.leading_zeros())) & !1u64 as u32);

	while m != 0 {
		let b = y.wrapping_add(m);
		y >>= 1;

		if x >= b {
			x = x.wrapping_sub(b);
			y = y.wrapping_add(m);
		}
		m >>= 2;
	}

	y
}
