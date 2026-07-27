//! ksdk — the shared kernel-idioms + struct-mirror crate (Ring 8, "depth").
//!
//! The research found the biggest wall to reaching the Tier-B middle (~73% of the
//! kernel) is STRUCT CONTEXT: functions that read fields of kernel structs and
//! call inline/macro helpers that have no symbol to link. Pure-scalar synth
//! (Rings 2/5) cannot express them. This crate is the reusable substrate that
//! makes them transplantable, built ONCE and linked by every Tier-B transplant:
//!
//!   * `#[repr(C)]` MIRRORS of kernel structs, each with a compile-time layout
//!     guard (Rust's const-assert = the BUILD_BUG_ON the research called
//!     load-bearing for config-dependent layout). A size-wrong mirror fails to
//!     COMPILE — a gate before the kernel is ever built.
//!   * reimplemented inline/macro helpers (clk_div_mask, DIV_ROUND_UP, …) — the
//!     ~69% of callees that are inlines/macros with no linkable symbol.
//!   * `container_of!` — the pointer-arithmetic primitive that is 17% of Tier-D.
//!
//! One panic handler lives here (the shared-runtime fix from Ring 1): Tier-B
//! transplants `use` this crate's items and carry no runtime of their own.
#![no_std]
#![no_main]
#![allow(dead_code)]

#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! {
    loop {}
}

// ---- struct mirrors (each guarded) ----

/// Mirror of `struct clk_div_table { unsigned int val; unsigned int div; };`
#[repr(C)]
pub struct ClkDivTable {
    pub val: u32,
    pub div: u32,
}

// Compile-time layout guard == BUILD_BUG_ON(sizeof/offsetof). A mirror that
// drifts from the C ABI (wrong size, added/removed field) fails to compile.
const _: () = assert!(core::mem::size_of::<ClkDivTable>() == 8);
const _: () = assert!(core::mem::align_of::<ClkDivTable>() == 4);

// ---- reimplemented inline/macro helpers (no linkable symbol in C) ----

/// `clk_div_mask(width)` == `(1 << width) - 1`
#[inline]
pub fn clk_div_mask(width: u8) -> u32 {
    (1u32 << width).wrapping_sub(1)
}

/// `DIV_ROUND_UP_ULL(n, d)` == `(n + d - 1) / d`
#[inline]
pub fn div_round_up_u64(n: u64, d: u64) -> u64 {
    (n.wrapping_add(d - 1)) / d
}

// ---- container_of (17% of Tier-D): recover the enclosing struct ----

/// `container_of(ptr, Struct, field)` — the offset-subtraction primitive.
#[macro_export]
macro_rules! container_of {
    ($ptr:expr, $ty:ty, $field:ident) => {{
        let off = core::mem::offset_of!($ty, $field);
        ($ptr as *const u8).sub(off) as *const $ty
    }};
}
