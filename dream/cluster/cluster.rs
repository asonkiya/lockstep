//! The transplanted {gcd, binary_gcd} cluster as ONE Rust object.
//!
//! `cgir_gcd` is the exported boundary (`#[no_mangle]`, the symbol the woven C
//! forwards to). `binary_gcd` is a PRIVATE fn — no exported symbol (no link
//! collision with anything), yet used by `cgir_gcd` (no dead-code warning). The
//! static helper that orphaned the C build now lives inside the Rust object,
//! reached only through the entry — exactly the cluster shape.

#![crate_type = "staticlib"]

/// The kernel's `static unsigned long binary_gcd(...)`, transplanted verbatim.
/// `__ffs(x)` == `x.trailing_zeros()`; `r & -r` isolates the lsbit.
fn binary_gcd(mut a: u64, mut b: u64) -> u64 {
    let r = a | b;

    b >>= b.trailing_zeros();
    if b == 1 {
        return r & r.wrapping_neg();
    }
    loop {
        a >>= a.trailing_zeros();
        if a == 1 {
            return r & r.wrapping_neg();
        }
        if a == b {
            return a << r.trailing_zeros();
        }
        if a < b {
            core::mem::swap(&mut a, &mut b);
        }
        a -= b;
    }
}

/// The kernel's exported `gcd(...)`. `efficient_ffs_key` is a never-disabled
/// static-key-true on arm64, so the binary_gcd branch is always taken.
#[no_mangle]
pub extern "C" fn cgir_gcd(a: u64, b: u64) -> u64 {
    let r = a | b;
    if a == 0 || b == 0 {
        return r;
    }
    binary_gcd(a, b)
}
