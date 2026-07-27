//! Negative control: the SAME cluster, but with a subtle bug planted inside the
//! PRIVATE helper `binary_gcd` — the `<< __ffs(r)` power-of-two restoration on
//! the `a == b` return is dropped (a realistic "forgot the shift" slip). Control
//! flow is unchanged, so it always terminates, but the result is wrong for any
//! pair sharing a factor of two. `cgir_gcd` itself is untouched. If the
//! differential over the exported entry still catches this, the boundary oracle
//! provably reaches the private helper — the whole point of cluster verification.

#![crate_type = "staticlib"]

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
            // BUG: kernel returns `a << __ffs(r)` — the `<< r.trailing_zeros()`
            // power-of-two restoration is dropped.
            return a;
        }
        if a < b {
            core::mem::swap(&mut a, &mut b);
        }
        a -= b;
    }
}

#[no_mangle]
pub extern "C" fn cgir_gcd(a: u64, b: u64) -> u64 {
    let r = a | b;
    if a == 0 || b == 0 {
        return r;
    }
    binary_gcd(a, b)
}
