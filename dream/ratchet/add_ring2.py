#!/usr/bin/env python3
"""Ring 2 growth: add the batch-verified lib leaves to the manifest.

Weaves the 3 clean leaves (int_pow, __sw_hweight32, __sw_hweight64). gcd is
batch-verified too but NOT woven: excising it would leave its static helper
`binary_gcd` unused (a -Werror risk) — a real weaver limitation worth recording,
and a reason the manifest tracks 'verified' distinctly from 'woven'. These are
widely-called kernel functions, so weaving them means real callers run the Rust
at boot (unlike ptp_mock, which had no consumer)."""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "manifest.json")


def shell_ret(sig_ret, name, params, args):
    return f"{sig_ret} {name}({params})\n{{\n\treturn cgir_{name.lstrip('_')}({args});\n}}"


def main() -> int:
    m = json.load(open(MANIFEST))

    # int_pow (lib/math/int_pow.c) — 1 function in file
    m["sources"]["lib/math/int_pow.c"] = {
        "extern_block": "\nu64 cgir_int_pow(u64 base, unsigned int exp);\n",
        "functions": {
            "int_pow": {
                "status": "rust", "tier": "unsafe", "gate": "differential", "verdict": "PASS",
                "seam": "cgir_int_pow",
                "shell": "u64 int_pow(u64 base, unsigned int exp)\n{\n\treturn cgir_int_pow(base, exp);\n}",
            }
        },
        "total_functions": 1,
    }

    # hweight (lib/hweight.c) — 4 functions in file (8/16/32/64); we weave 32 + 64
    m["sources"]["lib/hweight.c"] = {
        "extern_block": "\nunsigned int cgir_sw_hweight32(unsigned int w);\n"
                        "unsigned long cgir_sw_hweight64(__u64 w);\n",
        "functions": {
            "__sw_hweight32": {
                "status": "rust", "tier": "unsafe", "gate": "differential", "verdict": "PASS",
                "seam": "cgir_sw_hweight32",
                "shell": "unsigned int __sw_hweight32(unsigned int w)\n{\n\treturn cgir_sw_hweight32(w);\n}",
            },
            "__sw_hweight64": {
                "status": "rust", "tier": "unsafe", "gate": "differential", "verdict": "PASS",
                "seam": "cgir_sw_hweight64",
                "shell": "unsigned long __sw_hweight64(__u64 w)\n{\n\treturn cgir_sw_hweight64(w);\n}",
            },
        },
        "total_functions": 4,
    }

    m["rust_objects"]["int_pow"] = {"src": "ring2/int_pow.rs", "kbuild_dir": "lib/math", "obj": "int_pow_rust"}
    m["rust_objects"]["hweight32"] = {"src": "ring2/hweight32.rs", "kbuild_dir": "lib", "obj": "hweight32_rust"}
    m["rust_objects"]["hweight64"] = {"src": "ring2/hweight64.rs", "kbuild_dir": "lib", "obj": "hweight64_rust"}

    # record gcd as verified-but-not-woven (honest bookkeeping)
    m.setdefault("verified_not_woven", {})["gcd"] = {
        "gate": "differential", "verdict": "PASS",
        "reason": "weaving would leave static helper binary_gcd unused (-Werror risk)",
    }

    json.dump(m, open(MANIFEST, "w"), indent=1)
    n = sum(1 for s in m["sources"].values() for f in s["functions"].values() if f["status"] == "rust")
    print(f"manifest now: {len(m['sources'])} sources, {n} functions status:rust; gcd verified-not-woven")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
