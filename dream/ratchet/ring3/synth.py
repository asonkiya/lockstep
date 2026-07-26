#!/usr/bin/env python3
"""Synthesize the Rust transplant of the mockdev driver's register-programming
xfer. Unlike a pure leaf, this drives an MMIO seam (reg_read/reg_write externs),
so its correctness is its register program (order + poll + which register)."""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "m3"))
from synthesize import _api_key, parse_candidate  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
PRELUDE = """\
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
"""

PROMPT = f"""Transplant this C driver hot path into freestanding Rust linked into vmlinux.
Its correctness is its REGISTER PROGRAM — the order of writes, the poll on the
status bit, and which register the result is read from — verified against the C
original by a recorded register-access trace.

THE C:
```c
u32 mockdev_xfer(struct regmodel *m, u32 input)
{{
    reg_write(m, REG_DATA, input);
    reg_write(m, REG_CMD, CMD_START);
    while (reg_read(m, REG_STATUS) & STATUS_BUSY)
        ;
    return reg_read(m, REG_DATA);
}}
```

The prelude (below) is already present — do NOT repeat it; the externs
reg_read/reg_write and the REG_*/CMD_*/STATUS_* consts are in scope:

```rust
{PRELUDE}
```

Emit ONLY the Rust (no fences, no prose):
1. First line: `// driver: cgir_mockdev_xfer`
2. Exactly: `#[no_mangle]\\npub extern "C" fn cgir_mockdev_xfer(m: *mut c_void, input: u32) -> u32`
3. Reproduce the register program EXACTLY: stage the operand to REG_DATA, issue
   CMD_START to REG_CMD, poll REG_STATUS until STATUS_BUSY clears, then read and
   return REG_DATA. Same order, same registers.
4. Rules: `unsafe` for the extern calls, no panics, no other state, no busy-loop
   hints beyond the poll."""


def main() -> int:
    import anthropic

    client = anthropic.Anthropic(api_key=_api_key())
    feedback = None
    total = 0.0
    for attempt in range(1, 4):
        msgs = [{"role": "user", "content": PROMPT}]
        if feedback:
            msgs += [{"role": "assistant", "content": "(prev)"},
                     {"role": "user", "content": f"FAILED:\n{feedback}\nCorrect it."}]
        r = client.messages.create(model=MODEL, max_tokens=800, messages=msgs)
        total += (r.usage.input_tokens + r.usage.output_tokens * 5) / 1e6
        _, code = parse_candidate(r.content[0].text)
        if "cgir_mockdev_xfer" not in code:
            feedback = "must export cgir_mockdev_xfer"
            continue
        out = os.path.join(HERE, "mockdev.rs")
        open(out, "w").write(PRELUDE + "\n" + code + "\n")
        rc = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{HERE}:/w", "cgir-kernel-gate", "bash", "-c",
             "cd /w && rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort "
             "-C relocation-model=static -O mockdev.rs -o /tmp/m.o && nm /tmp/m.o | grep -c cgir_mockdev_xfer"],
            capture_output=True, text=True)
        ok = rc.stdout.strip().endswith("1")
        print(f"attempt {attempt}: {'✓' if ok else '✗'} {(rc.stdout + rc.stderr).strip()[:160]}")
        if ok:
            print(f"driver -> {out} (${total:.4f})")
            return 0
        feedback = f"rustc: {(rc.stdout + rc.stderr)[:300]}"
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
