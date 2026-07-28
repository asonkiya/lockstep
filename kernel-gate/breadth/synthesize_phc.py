#!/usr/bin/env python3
"""M4 breadth — model-synthesize ptp_mock's whole locked cluster, region by region.

The CGIR-sweep shape at region granularity: the manifest (extractor-driven)
yields 4 regions sharing one lock; each region gets its own prompt (its REAL C
body from the driver + the cluster IR + catalog + the fixed prelude), a cheap
model writes each Rust region independently, and the winners assemble into ONE
freestanding object exporting the full seam. The in-kernel gate then judges the
assembled cluster as a unit — regions that share a lock are verified together,
because their bugs are only visible together.

Negative control: the same sabotage as the depth leg (the Guard's marked
acquisition/release stripped) — all four regions lose the lock at once.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "synthesis"))
sys.path.insert(0, os.path.join(REPO, "kernel-gate"))
from synthesize import CATALOG, _api_key, parse_candidate  # noqa: E402
from synthesize_kernel import sabotage  # noqa: E402  (same markers)

MODEL = "claude-haiku-4-5-20251001"
PRICE_IN, PRICE_OUT = 1.00, 5.00

MANIFEST = os.path.join(HERE, "out", "manifest.json")
WINNER = os.path.join(HERE, "winner_phc.rs")

PRELUDE = '''\
// ---- fixed prelude (scaffold; model-written regions follow) ----
#![no_std]
#![no_main]

#[panic_handler]
fn ph(_: &core::panic::PanicInfo) -> ! {
    loop {}
}

use core::ffi::c_void;

// The kernel's real out-of-line spinlock API (lockdep-instrumented) and the
// exported timecounter functions the regions call (real kernel C, in vmlinux).
extern "C" {
    fn _raw_spin_lock(lock: *mut c_void);
    fn _raw_spin_unlock(lock: *mut c_void);
    fn timecounter_read(tc: *mut Timecounter) -> u64;
    fn timecounter_init(tc: *mut Timecounter, cc: *const Cyclecounter, start_tstamp: u64);
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

/// Mirrors of include/linux/timecounter.h (layout BUILD_BUG_ON-guarded on the
/// C side: cyclecounter 24 bytes, timecounter 40 bytes on LP64 arm64).
#[repr(C)]
pub struct Cyclecounter {
    pub read: Option<extern "C" fn(*mut Cyclecounter) -> u64>,
    pub mask: u64,
    pub mult: u32,
    pub shift: u32,
}

#[repr(C)]
pub struct Timecounter {
    pub cc: *mut Cyclecounter,
    pub cycle_last: u64,
    pub nsec: u64,
    pub mask: u64,
    pub frac: u64,
}

// ptp_mock.c's constants
pub const MOCK_PHC_CC_MULT: u32 = 0x8000_0000; // 1 << 31
pub const MOCK_PHC_FADJ_SHIFT: u32 = 9;
pub const MOCK_PHC_FADJ_DENOMINATOR: i64 = 15625;
// ---- end prelude ----
'''

# Region -> the exact seam signature its Rust must export.
SIGNATURES = {
    "mock_phc_adjfine": "#[no_mangle]\npub extern \"C\" fn lockstep_phc_adjfine(tc: *mut Timecounter, cc: *mut Cyclecounter, lock: *mut c_void, scaled_ppm: i64) -> i32",
    "mock_phc_adjtime": "#[no_mangle]\npub extern \"C\" fn lockstep_phc_adjtime(tc: *mut Timecounter, lock: *mut c_void, delta: i64) -> i32",
    "mock_phc_settime64": "#[no_mangle]\npub extern \"C\" fn lockstep_phc_settime64(tc: *mut Timecounter, cc: *mut Cyclecounter, lock: *mut c_void, ns: u64) -> i32",
    "mock_phc_gettime64": "#[no_mangle]\npub extern \"C\" fn lockstep_phc_gettime64(tc: *mut Timecounter, lock: *mut c_void) -> u64",
}

REGION_NOTES = {
    "mock_phc_adjfine": "div_s64(a, b) is plain truncating i64 division — use `/`. "
    "cc.mult is u32; adj may be negative: compute (MOCK_PHC_CC_MULT as i64 + adj) as u32. "
    "timecounter_read is called INSIDE the lock (flushes accumulated time at the old mult) — keep that.",
    "mock_phc_adjtime": "timecounter_adjtime(tc, delta) is an inline whose body is exactly: tc.nsec += delta. "
    "nsec is u64, delta is i64: use wrapping_add_signed (deltas here are small).",
    "mock_phc_settime64": "the timespec64_to_ns glue is the CALLER's job — you receive ns directly. "
    "The region is just the locked timecounter_init call.",
    "mock_phc_gettime64": "the ns_to_timespec64 glue is the CALLER's job — return the ns from the locked timecounter_read.",
}


def build_region_prompt(fn: str, c_source: str, ir: dict) -> str:
    catalog = "\n".join(f"  {c}  ->  {r}" for c, r in CATALOG)
    return f"""You are transplanting ONE region of a real Linux driver (drivers/ptp/ptp_mock.c,
a mock PTP clock) into freestanding Rust linked into vmlinux. It runs on SMP
under KCSAN + lockdep; races or missed locks are detected and rejected.

THE STOCK C FUNCTION (verbatim from the driver):

```c
{c_source}
```

CLUSTER IR (four regions share this one lock): {json.dumps(ir)}

THE CATALOG (pick the ONE row encoding the invariant):

{catalog}

THE FIXED PRELUDE already in the file (do NOT repeat it; Guard, Timecounter,
Cyclecounter, the constants, and the timecounter externs are in scope):

```rust
{PRELUDE}
```

TASK — emit ONLY this region's Rust (no fences, no prose):
1. First line: `// abstraction: <catalog selection>`
2. Then exactly this export:
{SIGNATURES[fn]}
3. Semantics: mirror the C — pre-lock computation stays before Guard::new, the
   locked body stays inside the guard scope (`let _g = Guard::new(lock);` then
   an unsafe block for pointer work), post-lock stays after.
4. Region notes: {REGION_NOTES[fn]}
5. Rules: no panicking ops (no unwrap/indexing that can panic), no statics, no
   extra synchronization; bind the guard as `_g` so it lives to scope end;
   return 0 for the i32-returning ops (the C always returns 0)."""


def sample(prompt: str, feedback: str | None = None) -> tuple[str, float]:
    import anthropic

    client = anthropic.Anthropic(api_key=_api_key())
    msgs = [{"role": "user", "content": prompt}]
    if feedback:
        msgs += [
            {"role": "assistant", "content": "(previous candidate)"},
            {"role": "user", "content": f"That candidate FAILED:\n{feedback}\nEmit a corrected version (same rules)."},
        ]
    r = client.messages.create(model=MODEL, max_tokens=1200, messages=msgs)
    cost = (r.usage.input_tokens * PRICE_IN + r.usage.output_tokens * PRICE_OUT) / 1e6
    return r.content[0].text, cost


def crosscompile(rs_path: str, want_symbols: list[str]) -> tuple[bool, str]:
    r = subprocess.run(
        [
            "docker", "run", "--rm", "-v", f"{os.path.dirname(rs_path)}:/w",
            "cgir-kernel-gate", "bash", "-c",
            f"cd /w && rustc --target aarch64-unknown-none-softfloat --emit=obj "
            f"-C panic=abort -C relocation-model=static -O {os.path.basename(rs_path)} "
            f"-o /tmp/c.o && nm /tmp/c.o",
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return False, (r.stdout + r.stderr).strip()[:500]
    missing = [s for s in want_symbols if s not in r.stdout]
    return (not missing), (f"missing exports: {missing}" if missing else "all exports present")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--emit-sabotaged", metavar="OUT")
    ap.add_argument("--k", type=int, default=3)
    args = ap.parse_args()

    if args.emit_sabotaged:
        bad = sabotage(open(WINNER).read())
        assert "SABOTAGE-BEGIN" not in bad
        with open(args.emit_sabotaged, "w") as fh:
            fh.write(bad)
        print(f"sabotaged cluster -> {args.emit_sabotaged}")
        return 0

    if not args.live:
        ap.error("--live or --emit-sabotaged")

    manifest = json.load(open(MANIFEST))
    ir = manifest["ir"]["protects"]
    total = 0.0
    parts: dict[str, str] = {}

    for fn, entry in manifest["transplant"].items():
        feedback = None
        for attempt in range(1, args.k + 1):
            text, cost = sample(build_region_prompt(fn, entry["c_source"], ir), feedback)
            total += cost
            sel, code = parse_candidate(text)
            seam = SIGNATURES[fn].split("fn ")[1].split("(")[0]
            if "SpinLock" not in sel:
                feedback = f"wrong abstraction {sel!r}"
                print(f"  {fn} attempt {attempt}: ✗ {feedback}")
                continue
            if seam not in code:
                feedback = f"must export exactly `{seam}`"
                print(f"  {fn} attempt {attempt}: ✗ {feedback}")
                continue
            parts[fn] = code
            print(f"  {fn} attempt {attempt}: ✓ ({sel}) ${total:.4f}")
            break
        else:
            print(f"{fn}: no candidate in {args.k} attempts")
            return 1

    assembled = PRELUDE + "\n" + "\n\n".join(parts[f] for f in manifest["transplant"]) + "\n"
    with open(WINNER, "w") as fh:
        fh.write(assembled)
    ok, msg = crosscompile(WINNER, [s.split("fn ")[1].split("(")[0] for s in SIGNATURES.values()])
    print(("✓ " if ok else "✗ ") + f"assembled cluster cross-compile: {msg}")
    if not ok:
        return 1
    print(f"\nwinner cluster -> {WINNER} (4 regions, cost=${total:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
