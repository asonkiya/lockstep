#!/usr/bin/env python3
"""Transplant gpio-zevio's four register-programming ops to freestanding Rust."""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "synthesis"))
from synthesize import _api_key, parse_candidate  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
PRELUDE = """\
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
"""

C_SRC = """\
static u32 zevio_port_get(void *regs, unsigned pin, unsigned port_offset) {
    unsigned section = ((pin >> 3) & 3) * ZEVIO_GPIO_SECTION_SIZE;
    return mmio_r(regs, section + port_offset);
}
static void zevio_port_set(void *regs, unsigned pin, unsigned port_offset, u32 val) {
    unsigned section = ((pin >> 3) & 3) * ZEVIO_GPIO_SECTION_SIZE;
    mmio_w(regs, section + port_offset, val);
}
int zevio_gpio_get(void *regs, unsigned pin) {
    u32 val, dir = zevio_port_get(regs, pin, ZEVIO_GPIO_DIRECTION);
    if (dir & BIT(pin & 7)) val = zevio_port_get(regs, pin, ZEVIO_GPIO_INPUT);
    else                    val = zevio_port_get(regs, pin, ZEVIO_GPIO_OUTPUT);
    return (val >> (pin & 7)) & 1;
}
int zevio_gpio_set(void *regs, unsigned pin, int value) {
    u32 val = zevio_port_get(regs, pin, ZEVIO_GPIO_OUTPUT);
    if (value) val |= BIT(pin & 7); else val &= ~BIT(pin & 7);
    zevio_port_set(regs, pin, ZEVIO_GPIO_OUTPUT, val);
    return 0;
}
int zevio_gpio_dir_in(void *regs, unsigned pin) {
    u32 val = zevio_port_get(regs, pin, ZEVIO_GPIO_DIRECTION);
    val |= BIT(pin & 7);
    zevio_port_set(regs, pin, ZEVIO_GPIO_DIRECTION, val);
    return 0;
}
int zevio_gpio_dir_out(void *regs, unsigned pin, int value) {
    u32 val = zevio_port_get(regs, pin, ZEVIO_GPIO_OUTPUT);
    if (value) val |= BIT(pin & 7); else val &= ~BIT(pin & 7);
    zevio_port_set(regs, pin, ZEVIO_GPIO_OUTPUT, val);
    val = zevio_port_get(regs, pin, ZEVIO_GPIO_DIRECTION);
    val &= ~BIT(pin & 7);
    zevio_port_set(regs, pin, ZEVIO_GPIO_DIRECTION, val);
    return 0;
}
"""

PROMPT = f"""Transplant these four register-programming ops of the Linux gpio-zevio driver
into freestanding Rust linked into vmlinux. Correctness is the REGISTER PROGRAM —
the section-offset math, which register, the read-modify-write bit ops, the order
— verified against the C original by a recorded register-access trace.

THE C (BIT(n) == 1u << n):
```c
{C_SRC}
```

The prelude is already present (do NOT repeat it); mmio_r/mmio_w and the
ZEVIO_GPIO_* consts are in scope:
```rust
{PRELUDE}
```

Emit ONLY the Rust (no fences, no prose):
1. First line: `// driver: gpio-zevio`
2. Reproduce the two helpers (as plain fns) and export exactly these four:
     #[no_mangle] pub extern "C" fn cgir_zevio_gpio_get(regs: *mut c_void, pin: u32) -> i32
     #[no_mangle] pub extern "C" fn cgir_zevio_gpio_set(regs: *mut c_void, pin: u32, value: i32) -> i32
     #[no_mangle] pub extern "C" fn cgir_zevio_gpio_dir_in(regs: *mut c_void, pin: u32) -> i32
     #[no_mangle] pub extern "C" fn cgir_zevio_gpio_dir_out(regs: *mut c_void, pin: u32, value: i32) -> i32
3. Match the C EXACTLY: section = ((pin >> 3) & 3) * SECTION_SIZE; bit = pin & 7;
   BIT(n) = 1u32 << n. Same registers, same RMW, same order. mmio_r/mmio_w are
   unsafe extern calls. No panics, no other state."""


def main() -> int:
    import anthropic

    client = anthropic.Anthropic(api_key=_api_key())
    feedback, total = None, 0.0
    seams = ["cgir_zevio_gpio_get", "cgir_zevio_gpio_set", "cgir_zevio_gpio_dir_in", "cgir_zevio_gpio_dir_out"]
    for attempt in range(1, 4):
        msgs = [{"role": "user", "content": PROMPT}]
        if feedback:
            msgs += [{"role": "assistant", "content": "(prev)"},
                     {"role": "user", "content": f"FAILED:\n{feedback}\nCorrect it."}]
        r = client.messages.create(model=MODEL, max_tokens=1200, messages=msgs)
        total += (r.usage.input_tokens + r.usage.output_tokens * 5) / 1e6
        _, code = parse_candidate(r.content[0].text)
        missing = [s for s in seams if s not in code]
        if missing:
            feedback = f"missing exports: {missing}"
            print(f"attempt {attempt}: ✗ {feedback}")
            continue
        out = os.path.join(HERE, "zevio.rs")
        open(out, "w").write(PRELUDE + "\n" + code + "\n")
        rc = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{HERE}:/w", "cgir-kernel-gate", "bash", "-c",
             "cd /w && rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort "
             "-C relocation-model=static -O zevio.rs -o /tmp/z.o && nm /tmp/z.o | grep -c cgir_zevio"],
            capture_output=True, text=True)
        ok = rc.stdout.strip().split("\n")[-1] == "4"
        print(f"attempt {attempt}: {'✓' if ok else '✗'} {(rc.stdout + rc.stderr).strip()[:160]}")
        if ok:
            print(f"driver -> {out} (${total:.4f})")
            return 0
        feedback = f"rustc: {(rc.stdout + rc.stderr)[:300]}"
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
