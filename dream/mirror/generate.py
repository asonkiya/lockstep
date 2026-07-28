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
# (struct, defining header) — real kernel structs, mixed scalar/ptr/fnptr/align
TARGETS = [
    ("clk_div_table", "include/linux/clk-provider.h"),
    ("clk_duty", "include/linux/clk-provider.h"),
    ("cyclecounter", "include/linux/timecounter.h"),
    ("timecounter", "include/linux/timecounter.h"),
]
KHEADERS = ["linux/clk-provider.h", "linux/timecounter.h"]


def main():
    rust = ["#![no_std]", "#![allow(dead_code)]", ""]
    guards, ok, refused = [], [], []
    for name, hdr in TARGETS:
        src = open(os.path.join(KSRC, hdr)).read()
        try:
            m = mirror.mirror(src, name)
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
    cfile = ("// SPDX-License-Identifier: GPL-2.0\n"
             "#include <linux/build_bug.h>\n#include <linux/kernel.h>\n#include <linux/stddef.h>\n"
             + inc + "\n\nstatic void __maybe_unused mirror_layout_guards(void)\n{\n"
             + "\n".join(guards) + "\n}\n")
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    open(os.path.join(HERE, "out", "guards.c"), "w").write(cfile)
    print(f"\n{len(ok)} mirrors generated, {len(refused)} refused")
    print("-> out_mirrors.rs (rustc-verified), out/guards.c (kernel-BUILD_BUG_ON-verified)")


if __name__ == "__main__":
    main()
