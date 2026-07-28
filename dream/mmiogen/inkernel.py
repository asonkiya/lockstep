#!/usr/bin/env python3
"""In-kernel realization of the MMIO-record harness — Ring 4, auto-generated.

mmio_harness.py verifies driver register functions HOST-side. This weaves the
same auto-extracted functions into a BOOTING kernel: the seam-adapted C refs and
their Rust transplants both link into vmlinux as freestanding objects, an
in-kernel probe provides reg_read/reg_write over a software register block
(QEMU has no GPIO hardware — the passive register model is faithful) and records
each access, and a late_initcall runs both, compares the full register-access
trace, and prints the verdict. This is exactly the seam Ring 4 booted, now
produced automatically from real driver source for a batch of functions in one
boot.

  correct -> DIFF_PASS   the Rust runs the identical register program as the C
  wrong   -> DIFF_FAIL   a mutated register offset diverges on the trace

Usage: inkernel.py <driver.c> <fn1> [fn2 ...] [--mode correct|wrong]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mmio_harness as mh  # noqa: E402

KSRC = os.environ.get("KSRC", mh.KSRC)
IMG, VOL, GATE = "cgir-kernel-gate", "cgir-kbuild", "crypto/lockstep_gate"
OUT = os.path.join(HERE, "inkernel_out")


def merged_ref_c(exs: list[dict]) -> str:
    regs: dict[str, int] = {}
    for ex in exs:
        regs.update(ex["regs"])
    L = ["// SPDX-License-Identifier: GPL-2.0", "#include <linux/types.h>",
         "extern u32 reg_read(u32 off);", "extern void reg_write(u32 off, u32 val);"]
    for n, v in sorted(regs.items(), key=lambda x: x[1]):
        L.append(f"#define {n} 0x{v:x}u")
    for ex in exs:
        L += ["", f"void {ex['fn']}_ref(u32 p)", "{", "\tu32 val = 0; (void)val;"]
        for kind, off, name, expr in ex["program"]:
            if kind == "R":
                L.append(f"\t{mh._expr_c(expr)} = reg_read({name});")
            elif kind == "W":
                L.append(f"\treg_write({name}, {mh._expr_c(expr)});")
            else:
                L.append(f"\t{mh._expr_c(expr)};")
        L.append("}")
    return "\n".join(L) + "\n"


def merged_cand_rs(exs: list[dict], mutate: bool) -> str:
    regs: dict[str, int] = {}
    for ex in exs:
        regs.update(ex["regs"])
    L = ["#![no_std]", "#![no_main]", "#[panic_handler]",
         "fn ph(_: &core::panic::PanicInfo) -> ! { loop {} }",
         'extern "C" { fn reg_read(off: u32) -> u32; fn reg_write(off: u32, val: u32); }']
    L += [f"const {n}: u32 = 0x{v:x};" for n, v in sorted(regs.items(), key=lambda x: x[1])]
    mutated = False
    for ex in exs:
        L += ["", f'#[no_mangle]', f'pub extern "C" fn cgir_{ex["fn"]}(p: u32) {{',
              "    let mut val: u32 = 0; let _ = val;"]
        for kind, off, name, expr in ex["program"]:
            if kind == "R":
                L.append(f"    unsafe {{ val = reg_read({name}); }}")
            elif kind == "W":
                tgt = name
                if mutate and not mutated:
                    tgt = f"({name} ^ 0x4)"   # BUG: wrong register offset
                    mutated = True
                L.append(f"    unsafe {{ reg_write({tgt}, {mh._expr_rs(expr)}); }}")
            else:
                L.append(f"    {mh._expr_rs(expr)};")
        L.append("}")
    return "\n".join(L) + "\n"


def probe_c(exs: list[dict]) -> str:
    fns = [ex["fn"] for ex in exs]
    refs = "\n".join(f"void {f}_ref(u32 p);" for f in fns)
    cands = "\n".join(f"void cgir_{f}(u32 p);" for f in fns)
    ref_calls = "\n".join(f"\t\t{f}_ref(p);" for f in fns)
    cand_calls = "\n".join(f"\t\tcgir_{f}(p);" for f in fns)
    return f"""// SPDX-License-Identifier: GPL-2.0
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/types.h>
#include <linux/string.h>

{refs}
{cands}

#define BLK 256
#define TMAX 16384
static u32 blk[BLK];
static u64 trace[TMAX]; static int tn;

static void tpush(char k, u32 off, u32 v)
{{ if (tn < TMAX) trace[tn++] = ((u64)k<<40) | ((u64)off<<8) | (v & 0xffffffffULL); }}

u32 reg_read(u32 off) {{ u32 v = blk[(off>>2)&(BLK-1)]; tpush('R', off, v); return v; }}
void reg_write(u32 off, u32 val) {{ tpush('W', off, val); blk[(off>>2)&(BLK-1)] = val; }}

static void seed(void) {{ int i; for (i=0;i<BLK;i++) blk[i] = 2654435761u*(u32)i ^ 0xA5A5A5A5u; }}

static u64 fnv(const u64 *v, int n)
{{ u64 h=0xcbf29ce484222325ULL; int i; for (i=0;i<n;i++){{h^=v[i]; h*=0x100000001b3ULL;}} return h; }}

static u64 rt[TMAX], ct[TMAX];

static int __init mmio_probe_init(void)
{{
	int rn, cn, i, fd = -1;
	u32 p;
	tn = 0; seed();
	for (p = 0; p < 32; p++) {{
{ref_calls}
	}}
	rn = tn; memcpy(rt, trace, tn*sizeof(u64));
	tn = 0; seed();
	for (p = 0; p < 32; p++) {{
{cand_calls}
	}}
	cn = tn; memcpy(ct, trace, tn*sizeof(u64));
	for (i = 0; i < (rn < cn ? rn : cn); i++)
		if (rt[i] != ct[i]) {{ fd = i; break; }}
	pr_emerg("MMIO_INK: ops=%d ref_len=%d cand_len=%d ref_hash=0x%llx cand_hash=0x%llx firstdiff=%d verdict=%s\\n",
	         32, rn, cn, fnv(rt, rn), fnv(ct, cn), fd,
	         (rn==cn && fd<0) ? "DIFF_PASS" : "DIFF_FAIL");
	return 0;
}}
late_initcall(mmio_probe_init);
"""


def build_and_boot(mode: str, fns: list[str]) -> tuple[str, str]:
    cand = "cand_bad.rs" if mode == "wrong" else "cand.rs"
    setup = (
        f"cd /build/linux; mkdir -p {GATE}\n"
        f"grep -q 'obj-y += lockstep_gate/' crypto/Makefile || echo 'obj-y += lockstep_gate/' >> crypto/Makefile\n"
        f"cd {GATE}; rm -f *.c *.o *.o_shipped\n"
        f"cp /o/ref.c /o/probe.c .\n"
        f"rustc --target aarch64-unknown-none-softfloat --emit=obj -C panic=abort "
        f"-C relocation-model=static -O /o/{cand} -o cand.o_shipped\n"
        f"test -s cand.o_shipped\n"
        f"printf 'obj-y := probe.o ref.o cand.o\\n' > Kbuild\n"
    )
    r = subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", "-v", f"{OUT}:/o:ro",
                        IMG, "bash", "-euc", setup], capture_output=True, text=True)
    if r.returncode:
        return "INSTALL_FAIL", (r.stdout + r.stderr)[-400:]
    b = subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", IMG, "bash", "-eo",
                        "pipefail", "-uc", "cd /build/linux && rm -f arch/arm64/boot/Image && "
                        "make -s -j$(nproc) Image 2>&1 | tail -3 && test -f arch/arm64/boot/Image"],
                       capture_output=True, text=True)
    if b.returncode:
        return "BUILD_FAIL", (b.stdout + b.stderr)[-500:]
    q = subprocess.run(["docker", "run", "--rm", "-v", f"{VOL}:/build", IMG, "bash", "-c",
                        "timeout 300 qemu-system-aarch64 -M virt -cpu max -smp 2 -m 1024 -nographic "
                        "-net none -kernel /build/linux/arch/arm64/boot/Image "
                        "-append 'console=ttyAMA0 panic=-1' -no-reboot 2>&1 || true"],
                       capture_output=True, text=True)
    con = q.stdout + q.stderr
    open(os.path.join(OUT, f"boot-{mode}.txt"), "w").write(con)
    line = next((l for l in con.splitlines() if "MMIO_INK:" in l), "(no MMIO_INK line)")
    verd = "DIFF_PASS" if "verdict=DIFF_PASS" in line else ("DIFF_FAIL" if "verdict=DIFF_FAIL" in line else "?")
    return verd, line.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("driver")
    ap.add_argument("fns", nargs="+")
    ap.add_argument("--skip-boot", action="store_true")
    a = ap.parse_args()
    src = open(os.path.join(KSRC, a.driver), errors="ignore").read()
    defs = mh.resolve_defines(src)
    exs = []
    for fn in a.fns:
        try:
            exs.append(mh.extract(src, fn, defs))
        except mh.Unsupported as e:
            print(f"  skip {fn}: {e}")
    if not exs:
        print("no extractable functions"); return 2
    os.makedirs(OUT, exist_ok=True)
    open(f"{OUT}/ref.c", "w").write(merged_ref_c(exs))
    open(f"{OUT}/cand.rs", "w").write(merged_cand_rs(exs, mutate=False))
    open(f"{OUT}/cand_bad.rs", "w").write(merged_cand_rs(exs, mutate=True))
    open(f"{OUT}/probe.c", "w").write(probe_c(exs))
    got = [ex["fn"] for ex in exs]
    print(f"[inkernel] generated in-kernel harness for {len(got)} fns: {got}")
    if a.skip_boot:
        print("  (--skip-boot: artifacts written, not built)"); return 0
    for mode, want in (("correct", "DIFF_PASS"), ("wrong", "DIFF_FAIL")):
        print(f"[inkernel:{mode}] build + boot + trace-verify (in vmlinux)...")
        verd, line = build_and_boot(mode, got)
        print(f"  {line}")
        if verd != want:
            print(f"INKERNEL GATE: FAIL ({mode} -> {verd}, wanted {want})"); return 1
    print(f"INKERNEL GATE: PASS ({len(got)} driver fns Rust-transplanted, trace-verified in a BOOTING kernel; "
          f"wrong-register control DIFF_FAIL)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
