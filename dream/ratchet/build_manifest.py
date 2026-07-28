#!/usr/bin/env python3
"""Author the Ring-0 manifest from the artifacts we already verified: ptp_mock's
four regions (M4 breadth winner) + the C shells and extern block (M5 rewire).

This is the seed of the ratchet's single source of truth. It records, per
function, the ratchet state (status/tier/gate/verdict) and the artifacts the
weaver needs (the C shell that replaces the body, the extern decl, the Rust
object). Later functions are just more rows.
"""

from __future__ import annotations

import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "rfc-export"))
from emit import EXTERN_BLOCK, NEW_BODIES  # noqa: E402  (the verified shells)

WINNER = os.path.join(REPO, "kernel-gate", "breadth", "winner_phc.rs")
RING0 = os.path.join(HERE, "ring0")

SEAMS = {
    "mock_phc_adjfine": "lockstep_phc_adjfine",
    "mock_phc_adjtime": "lockstep_phc_adjtime",
    "mock_phc_settime64": "lockstep_phc_settime64",
    "mock_phc_gettime64": "lockstep_phc_gettime64",
}


def main() -> int:
    os.makedirs(RING0, exist_ok=True)
    # the Rust object source: the verified region cluster
    shutil.copy(WINNER, os.path.join(RING0, "ptp_mock_regions.rs"))

    functions = {}
    for fn, seam in SEAMS.items():
        functions[fn] = {
            "status": "rust",
            "tier": "unsafe",              # repr(C)/raw-ptr faithful Rust
            "gate": "differential",        # oracle-manufactured (dream/diffgate)
            "verdict": "PASS",
            "seam": seam,
            "shell": NEW_BODIES[fn],
        }

    manifest = {
        "config": "arm64-defconfig +PTP_1588_CLOCK_MOCK",
        "generated_by": "build_manifest.py",
        "config_enable": ["PTP_1588_CLOCK_MOCK"],
        "sources": {
            "drivers/ptp/ptp_mock.c": {
                "extern_block": EXTERN_BLOCK,
                "functions": functions,
                # total function bodies in this file, for the %-Rust metric
                "total_functions": 9,   # 4 regions + create/destroy/refresh/cc_read/index
            }
        },
        "rust_objects": {
            "ptp_mock_regions": {
                "src": "ring0/ptp_mock_regions.rs",
                "kbuild_dir": "drivers/ptp",
                "obj": "ptp_mock_regions",
            }
        },
    }
    out = os.path.join(HERE, "manifest.json")
    with open(out, "w") as fh:
        json.dump(manifest, fh, indent=1)
    n_rust = sum(
        1 for s in manifest["sources"].values() for f in s["functions"].values() if f["status"] == "rust"
    )
    print(f"manifest -> {out}: {n_rust} functions status:rust")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
