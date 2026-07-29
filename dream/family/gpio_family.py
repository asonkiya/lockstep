#!/usr/bin/env python3
"""GPIO driver-family trace oracle — host-first (boot-free) generalization of the
Ring-4 recorded-MMIO differential across the GPIO family.

The scope (dream/family/SCOPE.md) established that GPIO register logic factors into
a few register-programming IDIOMS, and that the kernel already factored them into
the shared gpio-mmio.c (bgpio) library. This harness proves that empirically: a
GENERIC op-driver + trace comparator (write-once) drives each driver's get/set/
direction ops across pins, records the full ordered register trace via recording
mmio_r/mmio_w accessors, and compares the C reference's trace against a Rust
transplant's. Correct -> DIFF_PASS; a wrong-register transplant -> DIFF_FAIL.

Layers (mirroring the write-once / per-driver / per-function cost split):
  * PROBE_TMPL + IDIOMS  = write-once (whole family) + write-once-per-idiom.
  * DRIVERS[name]        = per-driver config: seam-adapted C ref + Rust transplant
                           + a one-line wrong-register mutation (negative control).
The op interface is standardized so the generic probe is driver-agnostic:
    C:    void ref_dir_out(void*,unsigned,int); void ref_dir_in(void*,unsigned);
          void ref_set(void*,unsigned,int);     int  ref_get(void*,unsigned);
    Rust: cand_dir_out/cand_dir_in/cand_set/cand_get (#[no_mangle] extern "C").

Boot-free: rustc staticlib + cc link + run (the recorder.c / structdiff substrate).
The in-kernel boot gate is unchanged from Ring 4 and is a documented follow-on.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------------------
# write-once: the generic op-driver + recording accessors + trace comparator
# ---------------------------------------------------------------------------

PROBE_TMPL = r"""
#include <stdio.h>
#include <stdint.h>
typedef uint32_t u32;

#define REGSZ 4096
static unsigned char dev[REGSZ];
%(DEFINES)s

/* ordered register-access trace (lossless: kind, offset, value) */
struct acc { char kind; u32 off; u32 val; };
static struct acc trace[65536]; static int tn;
static void tpush(char k, u32 off, u32 val) {
    if (tn < 65536) { trace[tn].kind = k; trace[tn].off = off; trace[tn].val = val; tn++; }
}

/* the recorded-MMIO seam (readl/writel stand-ins), backed by the idiom device
   model: writes may couple into a readable register (SET/CLR idioms). */
u32 mmio_r(void *base, u32 off) {
    u32 v = *(u32 *)((unsigned char *)base + off);
    tpush('R', off, v);
    return v;
}
void mmio_w(void *base, u32 off, u32 val) {
    tpush('W', off, val);
%(COUPLING)s
    *(u32 *)((unsigned char *)base + off) = val;
}

/* standardized op interface implemented by ref.c (C) and cand.rs (Rust) */
extern void ref_dir_out(void *, unsigned, int);  extern void ref_dir_in(void *, unsigned);
extern void ref_set(void *, unsigned, int);      extern int  ref_get(void *, unsigned);
extern void cand_dir_out(void *, unsigned, int); extern void cand_dir_in(void *, unsigned);
extern void cand_set(void *, unsigned, int);     extern int  cand_get(void *, unsigned);

static void seed(void) { for (int i = 0; i < REGSZ; i++) dev[i] = (unsigned char)(i * 37 + 11); }
static int cov_set0, cov_set1;

/* op script: dir_out(v1) -> set(v2) -> get -> dir_in -> get, across NPINS pins */
static void run(int R) {
    seed(); tn = 0;
    for (unsigned pin = 0; pin < %(NPINS)s; pin++) {
        int v1 = (pin * 7 + 1) & 1, v2 = (pin * 3) & 1, r;
        if (v2) cov_set1 = 1; else cov_set0 = 1;
        if (R) ref_dir_out(dev, pin, v1); else cand_dir_out(dev, pin, v1);
        if (R) ref_set(dev, pin, v2);     else cand_set(dev, pin, v2);
        r = R ? ref_get(dev, pin) : cand_get(dev, pin);  tpush('V', pin, (u32)r);
        if (R) ref_dir_in(dev, pin);      else cand_dir_in(dev, pin);
        r = R ? ref_get(dev, pin) : cand_get(dev, pin);  tpush('V', pin, (u32)r);
    }
}

static struct acc rbuf[65536]; static int rn;
static struct acc cbuf[65536]; static int cn;

int main(void) {
    run(1); rn = tn; for (int i = 0; i < tn; i++) rbuf[i] = trace[i];
    run(0); cn = tn; for (int i = 0; i < tn; i++) cbuf[i] = trace[i];
    int firstdiff = -1, m = rn < cn ? rn : cn;
    for (int i = 0; i < m; i++)
        if (rbuf[i].kind != cbuf[i].kind || rbuf[i].off != cbuf[i].off || rbuf[i].val != cbuf[i].val) {
            firstdiff = i; break;
        }
    if (firstdiff < 0 && rn != cn) firstdiff = m;
    int cov_ok = cov_set0 && cov_set1;
    const char *verdict = !cov_ok ? "REFUSE"
                        : (firstdiff < 0 && rn == cn) ? "DIFF_PASS" : "DIFF_FAIL";
    printf("GPIO_FAMILY %(NAME)s ops=%(NPINS)s ref_len=%%d cand_len=%%d firstdiff=%%d cov=%%d verdict=%%s\n",
           rn, cn, firstdiff, cov_ok, verdict);
    return (firstdiff < 0 && rn == cn && cov_ok) ? 0 : 1;
}
"""

# ---------------------------------------------------------------------------
# write-once per idiom: the device-model coupling in mmio_w
# ---------------------------------------------------------------------------

# The device-model coupling is written ONCE per idiom; the register OFFSETS it
# references are per-DRIVER (see each driver's "defines"). Only TWO coupling
# mechanisms are needed across the family: same-offset RMW, and write-offset->
# read-offset SET/CLR. SET/CLR "separate registers" (ftgpio/bgpio, set@0x10) and
# SET/CLR "aliases" (mxs, set@0x04) are the SAME coupling at different offsets —
# so a new driver in a known idiom costs only its offset table.
IDIOMS = {
    "rmw_data": {"coupling": ""},
    "set_clr": {
        "coupling": ("    if (off == REG_SET) { *(u32 *)(dev + REG_DAT) |= val; return; }\n"
                     "    if (off == REG_CLR) { *(u32 *)(dev + REG_DAT) &= ~val; return; }"),
    },
}

# ---------------------------------------------------------------------------
# per-driver: seam-adapted C reference + Rust transplant + wrong-register control
# ---------------------------------------------------------------------------

ZEVIO_REF_C = r"""
#include <stdint.h>
typedef uint32_t u32;
extern u32 mmio_r(void *, u32); extern void mmio_w(void *, u32, u32);
#define SECT 0x40
#define DIR  0x10
#define OUT  0x14
#define IN   0x18
#define B(pin) (1u << ((pin) & 7))
static u32 pg(void *r, unsigned pin, unsigned po) { return mmio_r(r, ((pin >> 3) & 3) * SECT + po); }
static void ps(void *r, unsigned pin, unsigned po, u32 v) { mmio_w(r, ((pin >> 3) & 3) * SECT + po, v); }
void ref_dir_out(void *r, unsigned pin, int val) {
    u32 v = pg(r, pin, OUT); if (val) v |= B(pin); else v &= ~B(pin); ps(r, pin, OUT, v);
    v = pg(r, pin, DIR); v &= ~B(pin); ps(r, pin, DIR, v);
}
void ref_dir_in(void *r, unsigned pin) { u32 v = pg(r, pin, DIR); v |= B(pin); ps(r, pin, DIR, v); }
void ref_set(void *r, unsigned pin, int val) { u32 v = pg(r, pin, OUT); if (val) v |= B(pin); else v &= ~B(pin); ps(r, pin, OUT, v); }
int ref_get(void *r, unsigned pin) {
    u32 dir = pg(r, pin, DIR);
    u32 v = (dir & B(pin)) ? pg(r, pin, IN) : pg(r, pin, OUT);
    return (v >> ((pin) & 7)) & 1;
}
"""

ZEVIO_CAND_RS = r"""
use core::ffi::c_void;
extern "C" { fn mmio_r(b: *mut c_void, o: u32) -> u32; fn mmio_w(b: *mut c_void, o: u32, v: u32); }
const SECT: u32 = 0x40;
const DIR: u32 = 0x10;
const OUT: u32 = 0x14;
const IN: u32 = 0x18;
fn pg(r: *mut c_void, pin: u32, po: u32) -> u32 { unsafe { mmio_r(r, ((pin >> 3) & 3) * SECT + po) } }
fn ps(r: *mut c_void, pin: u32, po: u32, v: u32) { unsafe { mmio_w(r, ((pin >> 3) & 3) * SECT + po, v) } }
#[no_mangle] pub extern "C" fn cand_dir_out(r: *mut c_void, pin: u32, val: i32) {
    let mut v = pg(r, pin, OUT); if val != 0 { v |= 1 << (pin & 7); } else { v &= !(1 << (pin & 7)); } ps(r, pin, OUT, v);
    let mut d = pg(r, pin, DIR); d &= !(1 << (pin & 7)); ps(r, pin, DIR, d);
}
#[no_mangle] pub extern "C" fn cand_dir_in(r: *mut c_void, pin: u32) { let mut v = pg(r, pin, DIR); v |= 1 << (pin & 7); ps(r, pin, DIR, v); }
#[no_mangle] pub extern "C" fn cand_set(r: *mut c_void, pin: u32, val: i32) { let mut v = pg(r, pin, OUT); if val != 0 { v |= 1 << (pin & 7); } else { v &= !(1 << (pin & 7)); } ps(r, pin, OUT, v); }
#[no_mangle] pub extern "C" fn cand_get(r: *mut c_void, pin: u32) -> i32 {
    let dir = pg(r, pin, DIR);
    let v = if dir & (1 << (pin & 7)) != 0 { pg(r, pin, IN) } else { pg(r, pin, OUT) };
    ((v >> (pin & 7)) & 1) as i32
}
"""

# The shared gpio-mmio.c (bgpio) core — the big lever: this ONE transplant is the
# get/set/direction logic ~41 gpio drivers delegate to. get = !!(read_reg(reg_dat)
# & mask); set = set_with_clear (write reg_set / reg_clr); dir_out/dir_in shadow
# sdir and write reg_dir_out. line2mask = BIT(pin) (bits=32, not be_bits).
GPIO_MMIO_REF_C = r"""
#include <stdint.h>
typedef uint32_t u32;
extern u32 mmio_r(void *, u32); extern void mmio_w(void *, u32, u32);
#define REG_DAT     0x00
#define REG_DIR_OUT 0x08
#define REG_SET     0x10
#define REG_CLR     0x14
#define MASK(pin) (1u << (pin))
static u32 sdir;  /* shadow direction register (bgpio chip->sdir) */
int ref_get(void *r, unsigned pin) { return !!(mmio_r(r, REG_DAT) & MASK(pin)); }
void ref_set(void *r, unsigned pin, int val) {           /* gpio_mmio_set_with_clear */
    if (val) mmio_w(r, REG_SET, MASK(pin)); else mmio_w(r, REG_CLR, MASK(pin));
}
void ref_dir_out(void *r, unsigned pin, int val) {        /* set value, then direction */
    ref_set(r, pin, val);
    sdir |= MASK(pin); mmio_w(r, REG_DIR_OUT, sdir);
}
void ref_dir_in(void *r, unsigned pin) { sdir &= ~MASK(pin); mmio_w(r, REG_DIR_OUT, sdir); }
"""

GPIO_MMIO_CAND_RS = r"""
use core::ffi::c_void;
extern "C" { fn mmio_r(b: *mut c_void, o: u32) -> u32; fn mmio_w(b: *mut c_void, o: u32, v: u32); }
const REG_DAT: u32 = 0x00;
const REG_DIR_OUT: u32 = 0x08;
const REG_SET: u32 = 0x10;
const REG_CLR: u32 = 0x14;
static mut SDIR: u32 = 0;
#[no_mangle] pub extern "C" fn cand_get(r: *mut c_void, pin: u32) -> i32 {
    (unsafe { mmio_r(r, REG_DAT) } & (1u32 << pin) != 0) as i32
}
#[no_mangle] pub extern "C" fn cand_set(r: *mut c_void, pin: u32, val: i32) {
    if val != 0 { unsafe { mmio_w(r, REG_SET, 1 << pin) } } else { unsafe { mmio_w(r, REG_CLR, 1 << pin) } }
}
#[no_mangle] pub extern "C" fn cand_dir_out(r: *mut c_void, pin: u32, val: i32) {
    cand_set(r, pin, val);
    unsafe { SDIR |= 1 << pin; mmio_w(r, REG_DIR_OUT, SDIR); }
}
#[no_mangle] pub extern "C" fn cand_dir_in(r: *mut c_void, pin: u32) {
    unsafe { SDIR &= !(1 << pin); mmio_w(r, REG_DIR_OUT, SDIR); }
}
"""

# A SECOND SET/CLR driver at the mxs-family ALIAS layout (SET@+0x4, CLR@+0x8 alias
# the DOUT register at +0x0). Same idiom coupling as bgpio, DIFFERENT offsets +
# get reads DOUT directly — demonstrating a new driver in a known idiom costs only
# its offset table + get-register choice (the per-driver cost claim, empirical).
ALIAS_REF_C = r"""
#include <stdint.h>
typedef uint32_t u32;
extern u32 mmio_r(void *, u32); extern void mmio_w(void *, u32, u32);
#define REG_DAT 0x00
#define REG_SET 0x04
#define REG_CLR 0x08
#define REG_DIR 0x0c
#define MASK(pin) (1u << (pin))
int ref_get(void *r, unsigned pin) { return !!(mmio_r(r, REG_DAT) & MASK(pin)); }
void ref_set(void *r, unsigned pin, int val) {
    if (val) mmio_w(r, REG_SET, MASK(pin)); else mmio_w(r, REG_CLR, MASK(pin));
}
void ref_dir_out(void *r, unsigned pin, int val) {
    ref_set(r, pin, val);
    u32 d = mmio_r(r, REG_DIR); d |= MASK(pin); mmio_w(r, REG_DIR, d);
}
void ref_dir_in(void *r, unsigned pin) { u32 d = mmio_r(r, REG_DIR); d &= ~MASK(pin); mmio_w(r, REG_DIR, d); }
"""

ALIAS_CAND_RS = r"""
use core::ffi::c_void;
extern "C" { fn mmio_r(b: *mut c_void, o: u32) -> u32; fn mmio_w(b: *mut c_void, o: u32, v: u32); }
const REG_DAT: u32 = 0x00;
const REG_SET: u32 = 0x04;
const REG_CLR: u32 = 0x08;
const REG_DIR: u32 = 0x0c;
#[no_mangle] pub extern "C" fn cand_get(r: *mut c_void, pin: u32) -> i32 {
    (unsafe { mmio_r(r, REG_DAT) } & (1u32 << pin) != 0) as i32
}
#[no_mangle] pub extern "C" fn cand_set(r: *mut c_void, pin: u32, val: i32) {
    if val != 0 { unsafe { mmio_w(r, REG_SET, 1 << pin) } } else { unsafe { mmio_w(r, REG_CLR, 1 << pin) } }
}
#[no_mangle] pub extern "C" fn cand_dir_out(r: *mut c_void, pin: u32, val: i32) {
    cand_set(r, pin, val);
    let d = unsafe { mmio_r(r, REG_DIR) } | (1 << pin); unsafe { mmio_w(r, REG_DIR, d); }
}
#[no_mangle] pub extern "C" fn cand_dir_in(r: *mut c_void, pin: u32) {
    let d = unsafe { mmio_r(r, REG_DIR) } & !(1 << pin); unsafe { mmio_w(r, REG_DIR, d); }
}
"""

DRIVERS = {
    "gpio-zevio": {
        "idiom": "rmw_data", "npins": 32,
        "ref_c": ZEVIO_REF_C, "cand_rs": ZEVIO_CAND_RS,
        # classic "wrong register": OUTPUT ops land on the INPUT offset
        "wrong": ("const OUT: u32 = 0x14;", "const OUT: u32 = 0x18;"),
    },
    "gpio-mmio(bgpio-core)": {
        "idiom": "set_clr", "npins": 32,
        "defines": "#define REG_DAT 0x00\n#define REG_SET 0x10\n#define REG_CLR 0x14",
        "ref_c": GPIO_MMIO_REF_C, "cand_rs": GPIO_MMIO_CAND_RS,
        # wrong register: `set` writes the CLEAR offset instead of SET
        "wrong": ("const REG_SET: u32 = 0x10;", "const REG_SET: u32 = 0x14;"),
    },
    "mxs-alias(set/clr@+4/+8)": {
        "idiom": "set_clr", "npins": 32,
        "defines": "#define REG_DAT 0x00\n#define REG_SET 0x04\n#define REG_CLR 0x08",
        "ref_c": ALIAS_REF_C, "cand_rs": ALIAS_CAND_RS,
        "wrong": ("const REG_SET: u32 = 0x04;", "const REG_SET: u32 = 0x08;"),
    },
}


def _emit_sources(cfg: dict, workdir: str, wrong: bool) -> None:
    idiom = IDIOMS[cfg["idiom"]]
    probe = PROBE_TMPL % {"DEFINES": cfg.get("defines", ""), "COUPLING": idiom["coupling"],
                          "NPINS": cfg["npins"], "NAME": cfg["_name"]}
    cand = cfg["cand_rs"]
    if wrong:
        old, new = cfg["wrong"]
        assert old in cand, f"wrong-register mutation anchor not found: {old!r}"
        cand = cand.replace(old, new, 1)
    open(os.path.join(workdir, "probe.c"), "w").write(probe)
    open(os.path.join(workdir, "ref.c"), "w").write(cfg["ref_c"])
    open(os.path.join(workdir, "cand.rs"), "w").write(cand)


def close(name: str, wrong: bool = False, workdir: str | None = None) -> tuple[str, str]:
    """Build + run the trace-oracle for one driver. Returns (verdict, output)."""
    cfg = dict(DRIVERS[name]); cfg["_name"] = name
    wd = workdir or tempfile.mkdtemp()
    os.makedirs(wd, exist_ok=True)
    _emit_sources(cfg, wd, wrong)
    r = subprocess.run(["rustc", "--edition", "2021", "-O", "--crate-type=staticlib",
                        os.path.join(wd, "cand.rs"), "-o", os.path.join(wd, "libcand.a")],
                       capture_output=True, text=True, cwd=wd)
    if r.returncode:
        return "BUILD_FAIL(rust)", r.stderr
    r = subprocess.run(["cc", "-O2", os.path.join(wd, "probe.c"), os.path.join(wd, "ref.c"),
                        os.path.join(wd, "libcand.a"), "-o", os.path.join(wd, "run")],
                       capture_output=True, text=True)
    if r.returncode:
        return "BUILD_FAIL(c)", r.stderr
    r = subprocess.run([os.path.join(wd, "run")], capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    v = ("DIFF_PASS" if "verdict=DIFF_PASS" in out else
         "DIFF_FAIL" if "verdict=DIFF_FAIL" in out else
         "REFUSE" if "verdict=REFUSE" in out else "UNKNOWN")
    return v, out


def main() -> int:
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        for name in DRIVERS:
            vc, oc = close(name, wrong=False, workdir=os.path.join(tmp, name + "_ok"))
            vw, ow = close(name, wrong=True, workdir=os.path.join(tmp, name + "_wrong"))
            good = vc == "DIFF_PASS" and vw == "DIFF_FAIL"
            ok &= good
            print(f"[{'ok' if good else 'FAIL'}] {name}")
            print(f"     correct        : {oc}")
            print(f"     wrong-register : {ow}")
    print("\nGPIO FAMILY:", "PASS — trace oracle generalizes; wrong-register caught"
          if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
