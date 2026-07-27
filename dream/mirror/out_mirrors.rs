#![no_std]
#![allow(dead_code)]

#[repr(C)]
pub struct ClkDivTable {
    pub val: u32,
    pub div: u32,
}
const _: () = assert!(core::mem::size_of::<ClkDivTable>() == 8);
const _: () = assert!(core::mem::offset_of!(ClkDivTable, val) == 0);
const _: () = assert!(core::mem::offset_of!(ClkDivTable, div) == 4);

#[repr(C)]
pub struct ClkDuty {
    pub num: u32,
    pub den: u32,
}
const _: () = assert!(core::mem::size_of::<ClkDuty>() == 8);
const _: () = assert!(core::mem::offset_of!(ClkDuty, num) == 0);
const _: () = assert!(core::mem::offset_of!(ClkDuty, den) == 4);

#[repr(C)]
pub struct Cyclecounter {
    pub read: *mut core::ffi::c_void,
    pub mask: u64,
    pub mult: u32,
    pub shift: u32,
}
const _: () = assert!(core::mem::size_of::<Cyclecounter>() == 24);
const _: () = assert!(core::mem::offset_of!(Cyclecounter, read) == 0);
const _: () = assert!(core::mem::offset_of!(Cyclecounter, mask) == 8);
const _: () = assert!(core::mem::offset_of!(Cyclecounter, mult) == 16);
const _: () = assert!(core::mem::offset_of!(Cyclecounter, shift) == 20);

#[repr(C)]
pub struct Timecounter {
    pub cc: *mut core::ffi::c_void,
    pub cycle_last: u64,
    pub nsec: u64,
    pub mask: u64,
    pub frac: u64,
}
const _: () = assert!(core::mem::size_of::<Timecounter>() == 40);
const _: () = assert!(core::mem::offset_of!(Timecounter, cc) == 0);
const _: () = assert!(core::mem::offset_of!(Timecounter, cycle_last) == 8);
const _: () = assert!(core::mem::offset_of!(Timecounter, nsec) == 16);
const _: () = assert!(core::mem::offset_of!(Timecounter, mask) == 24);
const _: () = assert!(core::mem::offset_of!(Timecounter, frac) == 32);
