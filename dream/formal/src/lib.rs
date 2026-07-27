//! Formal tier — bounded model checking (Kani/CBMC) of the transplants over the
//! FULL domain, reaching where in-kernel/native exhaustion can't (2^64). The
//! hweight functions are loop-free SWAR bit manipulation, which CBMC's bit-vector
//! reasoning proves completely (not sampled): for every u32/u64 input, the
//! transplant equals the trusted `count_ones` popcount — hence equals the kernel
//! C (also popcount) over the whole domain.
//!
//! Run: `cargo kani` (proves all #[kani::proof] harnesses).

// the model's actual transplants (dream/widerun & ring2), verbatim
pub fn cgir_sw_hweight32(mut w: u32) -> u32 {
    w = w.wrapping_sub((w >> 1) & 0x55555555);
    w = (w & 0x33333333).wrapping_add((w >> 2) & 0x33333333);
    w = (w.wrapping_add(w >> 4)) & 0x0f0f0f0f;
    (w.wrapping_mul(0x01010101)) >> 24
}

pub fn cgir_sw_hweight64(w: u64) -> u64 {
    let mut res = w.wrapping_sub((w >> 1) & 0x5555555555555555u64);
    res = (res & 0x3333333333333333u64).wrapping_add((res >> 2) & 0x3333333333333333u64);
    res = (res.wrapping_add(res >> 4)) & 0x0F0F0F0F0F0F0F0Fu64;
    res = res.wrapping_add(res >> 8);
    res = res.wrapping_add(res >> 16);
    (res.wrapping_add(res >> 32)) & 0x00000000000000FFu64
}

#[cfg(kani)]
mod proofs {
    use super::*;

    // Complete: CBMC explores all 2^32 symbolically, proves equal to popcount.
    #[kani::proof]
    fn hweight32_equiv_popcount() {
        let x: u32 = kani::any();
        assert_eq!(cgir_sw_hweight32(x), x.count_ones());
    }

    // The one native exhaustion cannot do (2^64): CBMC proves it over the whole
    // 64-bit domain via bit-vector reasoning.
    #[kani::proof]
    fn hweight64_equiv_popcount() {
        let x: u64 = kani::any();
        assert_eq!(cgir_sw_hweight64(x), x.count_ones() as u64);
    }
}
