//! The Rust transplant under test — the canonical driver hot path, programmed
//! through the reg_read/reg_write seam (readl/writel stand-in). Verified by the
//! recorder: it must reproduce the C's recorded register trace exactly. The
//! negative control (gate.sh subtle) drops the status poll — a wrong register
//! program that returns the same value but the recording rejects on its trace.

extern "C" {
    fn reg_read(off: u32) -> u32;
    fn reg_write(off: u32, val: u32);
}

const REG_DATA: u32 = 0x00;
const REG_CMD: u32 = 0x04;
const REG_STATUS: u32 = 0x08;
const CMD_START: u32 = 0x1;
const STATUS_BUSY: u32 = 0x1;

#[no_mangle]
pub extern "C" fn cgir_xfer(input: u32) -> u32 {
    unsafe {
        reg_write(REG_DATA, input);
        reg_write(REG_CMD, CMD_START);
        // POLL-BEGIN
        while reg_read(REG_STATUS) & STATUS_BUSY != 0 {}
        // POLL-END
        reg_read(REG_DATA)
    }
}
