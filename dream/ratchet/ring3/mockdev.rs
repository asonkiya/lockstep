#![no_std]
#![no_main]
#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }
use core::ffi::c_void;

// the MMIO seam (implemented by the harness / a real driver's readl/writel)
extern "C" {
    fn reg_read(m: *mut c_void, off: u32) -> u32;
    fn reg_write(m: *mut c_void, off: u32, val: u32);
}
const REG_DATA: u32 = 0x00;
const REG_CMD: u32 = 0x04;
const REG_STATUS: u32 = 0x08;
const CMD_START: u32 = 0x1;
const STATUS_BUSY: u32 = 0x1;

// driver: cgir_mockdev_xfer
#[no_mangle]
pub extern "C" fn cgir_mockdev_xfer(m: *mut c_void, input: u32) -> u32 {
    unsafe {
        reg_write(m, REG_DATA, input);
        reg_write(m, REG_CMD, CMD_START);
        while reg_read(m, REG_STATUS) & STATUS_BUSY != 0 {}
        reg_read(m, REG_DATA)
    }
}
