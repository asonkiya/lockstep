#!/usr/bin/env python3
"""Generate + emit verified mirrors for a target list of real kernel structs.
Writes mirrors.rs (Rust mirrors + const-asserts, verified by rustc) and guards.c
(kernel BUILD_BUG_ONs, verified by a kernel build against real headers)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mirror  # noqa: E402

KSRC = os.environ.get("KSRC", "/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux")
# (struct, defining header) — real kernel structs, mixed scalar/ptr/fnptr/align.
# The last two exercise the host-sound extensions:
#   * ieee80211_mu_edca_param_set embeds ieee80211_he_mu_edca_param_ac_rec
#     BY VALUE (recursive nested-struct-of-scalars, both mirrored + guarded);
#   * fb_blit_caps has two DECLARE_BITMAP(...) members with #define'd NBITS
#     (fixed [u64; K] arrays) plus scalar tail.
TARGETS = [
    ("clk_div_table", "include/linux/clk-provider.h"),
    ("clk_duty", "include/linux/clk-provider.h"),
    ("cyclecounter", "include/linux/timecounter.h"),
    ("timecounter", "include/linux/timecounter.h"),
    ("ieee80211_mu_edca_param_set", "include/linux/ieee80211-he.h"),
    ("fb_blit_caps", "include/linux/fb.h"),
    # opaque-primitive sizing (probe_primitives.py): raw_spinlock_t + atomic_t
    # by value, so every trailing offset depends on the in-kernel-probed lock
    # size — a wrong probe fails the kernel BUILD_BUG_ON below.
    ("ratelimit_state", "include/linux/ratelimit_types.h"),
]
# headers the kernel BUILD_BUG_ON guard must #include to see the real layouts.
# ieee80211-he.h is a `#include "..."` split of linux/ieee80211.h, so include
# the umbrella header rather than the fragment.
KHEADERS = ["linux/clk-provider.h", "linux/timecounter.h",
            "linux/ieee80211.h", "linux/fb.h", "linux/ratelimit.h"]


def main():
    rust = ["#![no_std]", "#![allow(dead_code)]", ""]
    guards, ok, refused = [], [], []
    for name, hdr in TARGETS:
        path = os.path.join(KSRC, hdr)
        src = open(path).read()
        try:
            m = mirror.mirror(src, name, near_file=path)
        except mirror.Unsupported as e:
            refused.append((name, str(e)))
            print(f"REFUSED {name}: {e}")
            continue
        rust.append(m["rust"])
        rust.append("")
        guards.append(m["c_guard"])
        ok.append((name, m["rust_type"], m["size"]))
        print(f"generated {name} -> {m['rust_type']} (size {m['size']})")

    open(os.path.join(HERE, "out_mirrors.rs"), "w").write("\n".join(rust))
    inc = "\n".join(f"#include <{h}>" for h in KHEADERS)
    # Guards are file-scope static_asserts (see emit_c_guard): unconditional at
    # compile time, unlike BUILD_BUG_ON in an unreferenced (DCE'd) function.
    cfile = ("// SPDX-License-Identifier: GPL-2.0\n"
             "#include <linux/build_bug.h>\n#include <linux/kernel.h>\n#include <linux/stddef.h>\n"
             + inc + "\n\n"
             + "\n".join(guards) + "\n")
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    open(os.path.join(HERE, "out", "guards.c"), "w").write(cfile)
    print(f"\n{len(ok)} mirrors generated, {len(refused)} refused")
    print("-> out_mirrors.rs (rustc-verified), out/guards.c (kernel-BUILD_BUG_ON-verified)")


if __name__ == "__main__":
    main()
