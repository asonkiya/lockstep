#!/usr/bin/env python3
"""Ring 6 — integrate the Ring 5 fleet's fresh, verified passers (lcm,
lcm_not_zero) into the ratchet manifest so the weaver folds them into the
booting kernel. Closes the loop: fleet-verified -> woven -> booting."""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(os.path.dirname(HERE), "manifest.json")


def main() -> int:
    m = json.load(open(MANIFEST))
    m["sources"]["lib/math/lcm.c"] = {
        "extern_block": "\nunsigned long cgir_lcm(unsigned long a, unsigned long b);\n"
                        "unsigned long cgir_lcm_not_zero(unsigned long a, unsigned long b);\n",
        "functions": {
            "lcm": {
                "status": "rust", "tier": "unsafe", "gate": "differential", "verdict": "PASS",
                "seam": "cgir_lcm",
                "shell": "unsigned long lcm(unsigned long a, unsigned long b)\n{\n\treturn cgir_lcm(a, b);\n}",
            },
            "lcm_not_zero": {
                "status": "rust", "tier": "unsafe", "gate": "differential", "verdict": "PASS",
                "seam": "cgir_lcm_not_zero",
                "shell": "unsigned long lcm_not_zero(unsigned long a, unsigned long b)\n{\n\treturn cgir_lcm_not_zero(a, b);\n}",
            },
        },
        "total_functions": 2,
    }
    m["rust_objects"]["lcm"] = {"src": "ring5/lcm.rs", "kbuild_dir": "lib/math", "obj": "lcm_rust"}
    m["rust_objects"]["lcm_nz"] = {"src": "ring5/lcm_nz.rs", "kbuild_dir": "lib/math", "obj": "lcm_nz_rust"}
    json.dump(m, open(MANIFEST, "w"), indent=1)
    n = sum(1 for s in m["sources"].values() for f in s["functions"].values() if f["status"] == "rust")
    print(f"manifest now: {len(m['sources'])} sources, {n} functions status:rust")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
