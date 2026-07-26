#![no_std]
#![no_main]
#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }

// leaf: cgir_gcd
#[no_mangle]
pub extern "C" fn cgir_gcd(mut a: u64, mut b: u64) -> u64 {
	let mut r = a | b;

	if a == 0 || b == 0 {
		return r;
	}

	r &= r.wrapping_neg();

	while b & r == 0 {
		b >>= 1;
	}
	if b == r {
		return r;
	}

	loop {
		while a & r == 0 {
			a >>= 1;
		}
		if a == r {
			return r;
		}
		if a == b {
			return a;
		}

		if a < b {
			core::mem::swap(&mut a, &mut b);
		}
		a -= b;
		a >>= 1;
		if a & r != 0 {
			a += b;
		}
		a >>= 1;
	}
}
