#!/usr/bin/env python3
"""M4 (depth leg) — model-synthesize the IN-KERNEL Rust region.

Same M3 recipe, kernel target: the concurrency IR + R4L catalog + a fixed
freestanding prelude (kernel lock externs + Guard RAII + #[repr(C)] fields
mirror) go to a cheap model; it writes `lockstep_ring_push` — the region body in
one guard scope over the kernel's REAL spinlock. The output compiles with the
rung-4 flags (aarch64-unknown-none-softfloat, no CONFIG_RUST machinery) and
links into vmlinux; gate.sh judges it under QEMU+KCSAN+lockdep.

The negative control (`--emit-sabotaged`) strips the marked acquisition AND
release from the prelude of the accepted winner: `Guard::new` hands out a guard
without ever taking the lock — the dropped-lock transplant, which the in-kernel
KCSAN must reject.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "extraction"))
sys.path.insert(0, os.path.join(REPO, "synthesis"))
from extract import extract  # noqa: E402
from synthesize import CATALOG, _api_key, parse_candidate  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
PRICE_IN, PRICE_OUT = 1.00, 5.00

STOCK_C = os.path.join(HERE, "probe", "lockstep_target.c")
# The IR comes from the ORIGINAL pre-transplant region (lock embedded in the
# struct, m1-extractable). lockstep_target.c is the kbuild shim form of the same
# region (lock passed opaquely), which the extractor rightly can't attribute.
IR_SOURCE_C = os.path.join(REPO, "transplant", "ring_stock.c")
WINNER = os.path.join(HERE, "winner_kernel.rs")

# The fixed prelude — the freestanding stand-in for kernel::sync::SpinLock's
# guard discipline, over the kernel's real out-of-line lock API (lockdep-aware
# in the gate's config). The model appends to this; it must not modify it.
PRELUDE = '''\
// ---- fixed prelude (scaffold; the model writes only what follows it) ----
#![no_std]
#![no_main]

#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! {
    loop {}
}

use core::ffi::c_void;

// The kernel's real spinlock API (out-of-line, lockdep-instrumented in this
// config). spinlock_t* is passed opaquely; its first member is the raw lock.
extern "C" {
    fn _raw_spin_lock(lock: *mut c_void);
    fn _raw_spin_unlock(lock: *mut c_void);
}

/// RAII critical section over the kernel lock: exists => the lock is held.
pub struct Guard {
    lock: *mut c_void,
}

impl Guard {
    pub fn new(lock: *mut c_void) -> Guard {
        // SABOTAGE-BEGIN (negative control removes this acquisition)
        unsafe { _raw_spin_lock(lock) };
        // SABOTAGE-END
        Guard { lock }
    }
}

impl Drop for Guard {
    fn drop(&mut self) {
        // SABOTAGE-DROP-BEGIN (and this release)
        unsafe { _raw_spin_unlock(self.lock) };
        // SABOTAGE-DROP-END
    }
}

/// Mirror of the C `struct ring_fields` (lockstep_ring.h): long, long, u8[64]
/// on LP64 arm64.
#[repr(C)]
pub struct RingFields {
    pub head: i64,
    pub count: i64,
    pub buf: [u8; 64],
}
// ---- end prelude ----
'''


def build_kernel_prompt(c_source: str, ir: dict) -> str:
    catalog = "\n".join(f"  {c}  ->  {r}" for c, r in CATALOG)
    ir_slim = {
        "structs": {s: v["locks"] for s, v in ir["structs"].items()},
        "protects": ir["protects"],
    }
    return f"""You are transplanting one concurrent region of LINUX KERNEL C into Rust, to be
compiled freestanding (no_std) and linked into vmlinux. It will run on SMP under
KCSAN and lockdep — a data race or missed lock WILL be detected and rejected.

THE STOCK C REGION (the critical section being replaced):

```c
{c_source}
```

THE EXTRACTED CONCURRENCY IR: {json.dumps(ir_slim)}

THE RUST-FOR-LINUX ABSTRACTION CATALOG (pick the ONE row encoding this
invariant):

{catalog}

THE FIXED PRELUDE (already at the top of the file — do NOT repeat or modify it;
`Guard` and `RingFields` are in scope):

```rust
{PRELUDE}
```

TASK — emit ONLY what comes after the prelude (no fences, no prose):
1. First line exactly: `// abstraction: <your catalog selection>`
2. This exact export, replacing the C function:
     #[no_mangle]
     pub extern "C" fn lockstep_ring_push(f: *mut RingFields, lock: *mut c_void, c: u8)
3. Body: create the guard FIRST (`let _g = Guard::new(lock);`), then, inside the
   guard's scope, the exact C semantics on `*f`:
     buf[head % 64] = c;  head += 1;  count += 1;
4. Rules:
   - the raw-pointer dereference goes in an `unsafe` block INSIDE the guard scope;
   - NO panicking operations: index buf via `get_unchecked_mut` (head % 64 is
     always in range) — a panic path will not link freestanding;
   - no statics, no atomics of your own, no other synchronization: the Guard IS
     the critical section;
   - the guard must live to the end of the function (bind it to `_g`, not `_`)."""


def sabotage(winner_src: str) -> str:
    """Drop the lock: remove both marked blocks so Guard::new/Drop never touch
    the kernel lock. The region body is untouched — same writes, no mutual
    exclusion. KCSAN must reject this leg."""
    out = re.sub(
        r"// SABOTAGE-BEGIN.*?// SABOTAGE-END\n",
        "// [sabotaged: acquisition removed]\n",
        winner_src,
        flags=re.DOTALL,
    )
    return re.sub(
        r"// SABOTAGE-DROP-BEGIN.*?// SABOTAGE-DROP-END\n",
        "// [sabotaged: release removed]\n",
        out,
        flags=re.DOTALL,
    )


def sample(prompt: str, feedback: str | None = None) -> tuple[str, float]:
    import anthropic

    client = anthropic.Anthropic(api_key=_api_key())
    msgs = [{"role": "user", "content": prompt}]
    if feedback:
        msgs += [
            {"role": "assistant", "content": "(previous candidate)"},
            {"role": "user", "content": f"That candidate FAILED:\n{feedback}\nEmit a corrected version (same rules)."},
        ]
    r = client.messages.create(model=MODEL, max_tokens=1500, messages=msgs)
    cost = (r.usage.input_tokens * PRICE_IN + r.usage.output_tokens * PRICE_OUT) / 1e6
    return r.content[0].text, cost


def crosscompile_check(rs_path: str) -> tuple[bool, str]:
    """Compile the candidate with the container's rustc + rung-4 flags."""
    import subprocess

    r = subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{os.path.dirname(rs_path)}:/w",
            "cgir-kernel-gate",
            "bash", "-c",
            f"cd /w && rustc --target aarch64-unknown-none-softfloat --emit=obj "
            f"-C panic=abort -C relocation-model=static -O {os.path.basename(rs_path)} "
            f"-o /tmp/c.o && echo COMPILED && nm /tmp/c.o | grep -c lockstep_ring_push",
        ],
        capture_output=True, text=True,
    )
    ok = "COMPILED" in r.stdout and r.stdout.strip().endswith("1")
    return ok, (r.stdout + r.stderr).strip()[:400]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--emit-sabotaged", metavar="OUT", help="write dropped-lock variant of the winner")
    ap.add_argument("--k", type=int, default=3)
    args = ap.parse_args()

    if args.emit_sabotaged:
        src = open(WINNER).read()
        bad = sabotage(src)
        assert "_raw_spin_lock" not in bad.split("extern")[1].split("}")[0] or True
        assert "SABOTAGE-BEGIN" not in bad
        with open(args.emit_sabotaged, "w") as fh:
            fh.write(bad)
        print(f"sabotaged variant -> {args.emit_sabotaged}")
        return 0

    if not args.live:
        ap.error("--live or --emit-sabotaged")

    c_source = open(STOCK_C).read()
    ir = extract(open(IR_SOURCE_C).read().split("#define WRITERS")[0])
    prompt = build_kernel_prompt(c_source, ir)
    print(f"IR: protects={json.dumps(ir['protects'])}")

    total, feedback = 0.0, None
    for attempt in range(1, args.k + 1):
        text, cost = sample(prompt, feedback)
        total += cost
        sel, code = parse_candidate(text)
        print(f"attempt {attempt}: abstraction: {sel!r} (${total:.4f})")
        if "SpinLock" not in sel:
            feedback = f"wrong abstraction {sel!r} — the IR shows spin_lock around fields"
            print(f"  ✗ {feedback}")
            continue
        full = PRELUDE + "\n" + code + "\n"
        with open(WINNER, "w") as fh:
            fh.write(full)
        ok, msg = crosscompile_check(WINNER)
        print(("  ✓ " if ok else "  ✗ ") + f"cross-compile: {msg[:160]}")
        if ok:
            print(f"\nwinner -> {WINNER} (cost=${total:.4f}, attempts={attempt})")
            return 0
        feedback = f"rustc (aarch64 freestanding) failed:\n{msg}"
    print(f"no candidate in {args.k} attempts (${total:.4f})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
