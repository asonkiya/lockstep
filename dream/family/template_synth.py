#!/usr/bin/env python3
"""Template synthesis — the $0, no-model rung of the synth ladder for
idiom-recognizable driver families.

The GPIO family result (dream/family/RESULTS.md) showed the register logic
factors into a few IDIOMS, and that a new driver in a known idiom costs only its
offset table. That means, for idiom-recognizable drivers, the Rust transplant is
a DETERMINISTIC instantiation of the idiom template + offsets — no LLM call, no
tokens. This module is that codegen.

Soundness: template synth emits only the Rust CANDIDATE. The trace-oracle gate
(gpio_family.close) still checks it against the REAL driver's C reference, so a
mis-recognized idiom or wrong template DIFF_FAILs and the ladder falls back to
c2rust / a model. Template synth is just the cheapest, gate-arbitrated rung —
sound by construction, like every other synthesizer in the pipeline.

Covers the SET/CLR idiom (bgpio `set_with_clear` + the mxs alias layout — the
shared-library mass, ~41 gpio drivers). RMW-DATA custom drivers (zevio's
section/direction math) are not a flat template and stay on c2rust/model.
"""
from __future__ import annotations

_HEADER = (
    "use core::ffi::c_void;\n"
    "extern \"C\" { fn mmio_r(b: *mut c_void, o: u32) -> u32; "
    "fn mmio_w(b: *mut c_void, o: u32, v: u32); }\n"
)

_GET_SET = """\
#[no_mangle] pub extern "C" fn cand_get(r: *mut c_void, pin: u32) -> i32 {
    (unsafe { mmio_r(r, REG_DAT) } & (1u32 << pin) != 0) as i32
}
#[no_mangle] pub extern "C" fn cand_set(r: *mut c_void, pin: u32, val: i32) {
    if val != 0 { unsafe { mmio_w(r, REG_SET, 1 << pin) } } else { unsafe { mmio_w(r, REG_CLR, 1 << pin) } }
}"""

# direction sub-modes: bgpio shadows sdir and writes it back; mxs RMWs a dir reg.
_DIR_SHADOW = """\
static mut SDIR: u32 = 0;
#[no_mangle] pub extern "C" fn cand_dir_out(r: *mut c_void, pin: u32, val: i32) {
    cand_set(r, pin, val);
    unsafe { SDIR |= 1 << pin; mmio_w(r, REG_DIR, SDIR); }
}
#[no_mangle] pub extern "C" fn cand_dir_in(r: *mut c_void, pin: u32) {
    unsafe { SDIR &= !(1 << pin); mmio_w(r, REG_DIR, SDIR); }
}"""

_DIR_RMW = """\
#[no_mangle] pub extern "C" fn cand_dir_out(r: *mut c_void, pin: u32, val: i32) {
    cand_set(r, pin, val);
    let d = unsafe { mmio_r(r, REG_DIR) } | (1 << pin); unsafe { mmio_w(r, REG_DIR, d); }
}
#[no_mangle] pub extern "C" fn cand_dir_in(r: *mut c_void, pin: u32) {
    let d = unsafe { mmio_r(r, REG_DIR) } & !(1 << pin); unsafe { mmio_w(r, REG_DIR, d); }
}"""

_DIR = {"shadow": _DIR_SHADOW, "rmw": _DIR_RMW}


def synth(idiom: str, offsets: dict, dir_mode: str = "shadow") -> str:
    """Deterministically render the Rust transplant for a driver in `idiom` with
    the given register `offsets` (keys dat/set/clr/dir). $0 — no model call."""
    if idiom != "set_clr":
        raise ValueError(f"no template for idiom {idiom!r} (custom logic -> c2rust/model)")
    if dir_mode not in _DIR:
        raise ValueError(f"unknown dir_mode {dir_mode!r}")
    need = {"dat", "set", "clr", "dir"}
    if set(offsets) < need:
        raise ValueError(f"offsets missing {need - set(offsets)}")
    consts = (f"const REG_DAT: u32 = {offsets['dat']:#x};\n"
              f"const REG_SET: u32 = {offsets['set']:#x};\n"
              f"const REG_CLR: u32 = {offsets['clr']:#x};\n"
              f"const REG_DIR: u32 = {offsets['dir']:#x};\n")
    return "\n".join([_HEADER, consts, _GET_SET, _DIR[dir_mode]]) + "\n"


# The offset tables ARE the per-driver cost — a handful of numbers each. Adding a
# bgpio-based driver = one entry here, then gate it (no model, no tokens).
DRIVER_SPECS = {
    "gpio-mmio(bgpio-core)": {"idiom": "set_clr", "dir_mode": "shadow",
                              "offsets": {"dat": 0x00, "set": 0x10, "clr": 0x14, "dir": 0x08}},
    "mxs-alias(set/clr@+4/+8)": {"idiom": "set_clr", "dir_mode": "rmw",
                                 "offsets": {"dat": 0x00, "set": 0x04, "clr": 0x08, "dir": 0x0c}},
}


def synth_for(driver: str) -> str:
    s = DRIVER_SPECS[driver]
    return synth(s["idiom"], s["offsets"], s["dir_mode"])


if __name__ == "__main__":
    for d in DRIVER_SPECS:
        print(f"==== {d}  ($0, no model) ====")
        print(synth_for(d))
