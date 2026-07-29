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

#[repr(C)]
pub struct Ieee80211HeMuEdcaParamAcRec {
    pub aifsn: u8,
    pub ecw_min_max: u8,
    pub mu_edca_timer: u8,
}
const _: () = assert!(core::mem::size_of::<Ieee80211HeMuEdcaParamAcRec>() == 3);
const _: () = assert!(core::mem::offset_of!(Ieee80211HeMuEdcaParamAcRec, aifsn) == 0);
const _: () = assert!(core::mem::offset_of!(Ieee80211HeMuEdcaParamAcRec, ecw_min_max) == 1);
const _: () = assert!(core::mem::offset_of!(Ieee80211HeMuEdcaParamAcRec, mu_edca_timer) == 2);

#[repr(C)]
pub struct Ieee80211MuEdcaParamSet {
    pub mu_qos_info: u8,
    pub ac_be: Ieee80211HeMuEdcaParamAcRec,
    pub ac_bk: Ieee80211HeMuEdcaParamAcRec,
    pub ac_vi: Ieee80211HeMuEdcaParamAcRec,
    pub ac_vo: Ieee80211HeMuEdcaParamAcRec,
}
const _: () = assert!(core::mem::size_of::<Ieee80211MuEdcaParamSet>() == 13);
const _: () = assert!(core::mem::offset_of!(Ieee80211MuEdcaParamSet, mu_qos_info) == 0);
const _: () = assert!(core::mem::offset_of!(Ieee80211MuEdcaParamSet, ac_be) == 1);
const _: () = assert!(core::mem::offset_of!(Ieee80211MuEdcaParamSet, ac_bk) == 4);
const _: () = assert!(core::mem::offset_of!(Ieee80211MuEdcaParamSet, ac_vi) == 7);
const _: () = assert!(core::mem::offset_of!(Ieee80211MuEdcaParamSet, ac_vo) == 10);

#[repr(C)]
pub struct FbBlitCaps {
    pub x: [u64; 1],
    pub y: [u64; 2],
    pub len: u32,
    pub flags: u32,
}
const _: () = assert!(core::mem::size_of::<FbBlitCaps>() == 32);
const _: () = assert!(core::mem::offset_of!(FbBlitCaps, x) == 0);
const _: () = assert!(core::mem::offset_of!(FbBlitCaps, y) == 8);
const _: () = assert!(core::mem::offset_of!(FbBlitCaps, len) == 24);
const _: () = assert!(core::mem::offset_of!(FbBlitCaps, flags) == 28);

#[repr(C)]
pub struct RatelimitState {
    pub lock: [u64; 8],
    pub interval: i32,
    pub burst: i32,
    pub rs_n_left: u32,
    pub missed: u32,
    pub flags: u32,
    pub begin: u64,
}
const _: () = assert!(core::mem::size_of::<RatelimitState>() == 96);
const _: () = assert!(core::mem::offset_of!(RatelimitState, lock) == 0);
const _: () = assert!(core::mem::offset_of!(RatelimitState, interval) == 64);
const _: () = assert!(core::mem::offset_of!(RatelimitState, burst) == 68);
const _: () = assert!(core::mem::offset_of!(RatelimitState, rs_n_left) == 72);
const _: () = assert!(core::mem::offset_of!(RatelimitState, missed) == 76);
const _: () = assert!(core::mem::offset_of!(RatelimitState, flags) == 80);
const _: () = assert!(core::mem::offset_of!(RatelimitState, begin) == 88);
