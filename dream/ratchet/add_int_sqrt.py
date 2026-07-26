#!/usr/bin/env python3
"""Ratchet growth: add the (differentially-verified) int_sqrt leaf to the
manifest as a second source. Demonstrates accumulation — the manifest gains a
row, %-Rust climbs, and the prior ptp entry is untouched (non-regression)."""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "manifest.json")

# the shell keeps the exported kernel symbol `int_sqrt`, forwarding to the Rust
# seam — the same partial-migration shape, uniform with ptp.
SHELL = (
    "unsigned long int_sqrt(unsigned long x)\n"
    "{\n"
    "\treturn cgir_int_sqrt(x);\n"
    "}"
)
EXTERN = "\nunsigned long cgir_int_sqrt(unsigned long x);\n"


def main() -> int:
    m = json.load(open(MANIFEST))
    m["sources"]["lib/math/int_sqrt.c"] = {
        "extern_block": EXTERN,
        "functions": {
            "int_sqrt": {
                "status": "rust",
                "tier": "unsafe",
                "gate": "differential",   # also has a KUnit oracle (int_sqrt_kunit)
                "verdict": "PASS",
                "seam": "cgir_int_sqrt",
                "shell": SHELL,
            }
        },
        "total_functions": 2,   # int_sqrt + int_sqrt64 (64-bit path)
    }
    m["rust_objects"]["int_sqrt"] = {
        "src": "ring1/int_sqrt.rs",
        "kbuild_dir": "lib/math",
        "obj": "int_sqrt_rust",
    }
    json.dump(m, open(MANIFEST, "w"), indent=1)
    n = sum(1 for s in m["sources"].values() for f in s["functions"].values() if f["status"] == "rust")
    print(f"manifest now: {len(m['sources'])} sources, {n} functions status:rust")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
