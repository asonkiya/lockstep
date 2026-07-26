#![no_std]
#![no_main]
#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }
use core::ffi::c_void;

// recorded MMIO seam (readl/writel on the ioremap'd base)
extern "C" {
    fn mmio_r(base: *mut c_void, off: u32) -> u32;
    fn mmio_w(base: *mut c_void, off: u32, val: u32);
}
const ZEVIO_GPIO_SECTION_SIZE: u32 = 0x40;
const ZEVIO_GPIO_DIRECTION: u32 = 0x10;
const ZEVIO_GPIO_OUTPUT: u32 = 0x14;
const ZEVIO_GPIO_INPUT: u32 = 0x18;

// driver: gpio-zevio

fn zevio_port_get(regs: *mut c_void, pin: u32, port_offset: u32) -> u32 {
    let section = ((pin >> 3) & 3) * ZEVIO_GPIO_SECTION_SIZE;
    unsafe { mmio_r(regs, section + port_offset) }
}

fn zevio_port_set(regs: *mut c_void, pin: u32, port_offset: u32, val: u32) {
    let section = ((pin >> 3) & 3) * ZEVIO_GPIO_SECTION_SIZE;
    unsafe { mmio_w(regs, section + port_offset, val) }
}

#[no_mangle]
pub extern "C" fn cgir_zevio_gpio_get(regs: *mut c_void, pin: u32) -> i32 {
    let dir = zevio_port_get(regs, pin, ZEVIO_GPIO_DIRECTION);
    let val = if dir & (1u32 << (pin & 7)) != 0 {
        zevio_port_get(regs, pin, ZEVIO_GPIO_INPUT)
    } else {
        zevio_port_get(regs, pin, ZEVIO_GPIO_OUTPUT)
    };
    ((val >> (pin & 7)) & 1) as i32
}

#[no_mangle]
pub extern "C" fn cgir_zevio_gpio_set(regs: *mut c_void, pin: u32, value: i32) -> i32 {
    let mut val = zevio_port_get(regs, pin, ZEVIO_GPIO_OUTPUT);
    if value != 0 {
        val |= 1u32 << (pin & 7);
    } else {
        val &= !(1u32 << (pin & 7));
    }
    zevio_port_set(regs, pin, ZEVIO_GPIO_OUTPUT, val);
    0
}

#[no_mangle]
pub extern "C" fn cgir_zevio_gpio_dir_in(regs: *mut c_void, pin: u32) -> i32 {
    let mut val = zevio_port_get(regs, pin, ZEVIO_GPIO_DIRECTION);
    val |= 1u32 << (pin & 7);
    zevio_port_set(regs, pin, ZEVIO_GPIO_DIRECTION, val);
    0
}

#[no_mangle]
pub extern "C" fn cgir_zevio_gpio_dir_out(regs: *mut c_void, pin: u32, value: i32) -> i32 {
    let mut val = zevio_port_get(regs, pin, ZEVIO_GPIO_OUTPUT);
    if value != 0 {
        val |= 1u32 << (pin & 7);
    } else {
        val &= !(1u32 << (pin & 7));
    }
    zevio_port_set(regs, pin, ZEVIO_GPIO_OUTPUT, val);
    val = zevio_port_get(regs, pin, ZEVIO_GPIO_DIRECTION);
    val &= !(1u32 << (pin & 7));
    zevio_port_set(regs, pin, ZEVIO_GPIO_DIRECTION, val);
    0
}
