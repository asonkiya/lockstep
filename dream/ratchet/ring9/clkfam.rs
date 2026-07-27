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

// clk-divider flags (include/linux/clk-provider.h)
const CLK_DIVIDER_ONE_BASED: u64 = 1 << 0;
const CLK_DIVIDER_POWER_OF_TWO: u64 = 1 << 1;
const CLK_DIVIDER_MAX_AT_ZERO: u64 = 1 << 6;
const CLK_DIVIDER_EVEN_INTEGERS: u64 = 1 << 8;

// subsystem: clk-divider divider-math family

#[no_mangle]
pub extern "C" fn cgir_get_table_div(t: *const ClkDivTable, val: u32) -> u32 {
    if t.is_null() {
        return 0;
    }
    unsafe {
        let mut p = t;
        loop {
            if (*p).div == 0 {
                break;
            }
            if (*p).val == val {
                return (*p).div;
            }
            p = p.add(1);
        }
    }
    0
}

#[no_mangle]
pub extern "C" fn cgir_get_table_val(t: *const ClkDivTable, div: u32) -> u32 {
    if t.is_null() {
        return 0;
    }
    unsafe {
        let mut p = t;
        loop {
            if (*p).div == 0 {
                break;
            }
            if (*p).div == div {
                return (*p).val;
            }
            p = p.add(1);
        }
    }
    0
}

fn get_table_maxdiv(t: *const ClkDivTable, width: u8) -> u32 {
    let mut maxdiv = 0u32;
    let mask = clk_div_mask(width);
    if t.is_null() {
        return 0;
    }
    unsafe {
        let mut p = t;
        loop {
            if (*p).div == 0 {
                break;
            }
            if (*p).div > maxdiv && (*p).val <= mask {
                maxdiv = (*p).div;
            }
            p = p.add(1);
        }
    }
    maxdiv
}

#[no_mangle]
pub extern "C" fn cgir_get_table_maxdiv(t: *const ClkDivTable, width: u8) -> u32 {
    get_table_maxdiv(t, width)
}

#[no_mangle]
pub extern "C" fn cgir_get_maxdiv(t: *const ClkDivTable, width: u8, flags: u64) -> u32 {
    if (flags & CLK_DIVIDER_ONE_BASED) != 0 {
        return clk_div_mask(width);
    }
    if (flags & CLK_DIVIDER_POWER_OF_TWO) != 0 {
        return 1u32 << clk_div_mask(width);
    }
    if (flags & CLK_DIVIDER_EVEN_INTEGERS) != 0 {
        return 2u32.wrapping_mul(clk_div_mask(width).wrapping_add(1));
    }
    if !t.is_null() {
        return get_table_maxdiv(t, width);
    }
    clk_div_mask(width).wrapping_add(1)
}

#[no_mangle]
pub extern "C" fn cgir_get_div(t: *const ClkDivTable, val: u32, flags: u64, width: u8) -> u32 {
    if (flags & CLK_DIVIDER_ONE_BASED) != 0 {
        return val;
    }
    if (flags & CLK_DIVIDER_POWER_OF_TWO) != 0 {
        return 1u32 << val;
    }
    if (flags & CLK_DIVIDER_MAX_AT_ZERO) != 0 {
        return if val != 0 { val } else { clk_div_mask(width).wrapping_add(1) };
    }
    if (flags & CLK_DIVIDER_EVEN_INTEGERS) != 0 {
        return 2u32.wrapping_mul(val.wrapping_add(1));
    }
    if !t.is_null() {
        return cgir_get_table_div(t, val);
    }
    val.wrapping_add(1)
}

#[no_mangle]
pub extern "C" fn cgir_get_val(t: *const ClkDivTable, div: u32, flags: u64, width: u8) -> u32 {
    if (flags & CLK_DIVIDER_ONE_BASED) != 0 {
        return div;
    }
    if (flags & CLK_DIVIDER_POWER_OF_TWO) != 0 {
        return div.trailing_zeros();
    }
    if (flags & CLK_DIVIDER_MAX_AT_ZERO) != 0 {
        return if div == clk_div_mask(width).wrapping_add(1) { 0 } else { div };
    }
    if (flags & CLK_DIVIDER_EVEN_INTEGERS) != 0 {
        return (div >> 1).wrapping_sub(1);
    }
    if !t.is_null() {
        return cgir_get_table_val(t, div);
    }
    div.wrapping_sub(1)
}
