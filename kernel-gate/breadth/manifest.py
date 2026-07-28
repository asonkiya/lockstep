#!/usr/bin/env python3
"""M4 breadth — the extractor-driven worklist for ptp_mock's locked cluster.

Runs the M1 extractor on the REAL driver (ptp_mock_stock.c, vendored verbatim
from drivers/ptp/ptp_mock.c) and emits the sweep manifest: every critical
section on mock_phc.lock, the protects map, and — for each transplantable
region — the full C function body that seeds its synthesis prompt.

Classification is explicit: regions are transplanted as a CLUSTER (they share
tc/cc under one lock — per-region partial transplant was M4-depth's shape; the
cluster is breadth's), init-path and wrapper functions are named and skipped
with reasons, not silently dropped.
"""

from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "extraction"))
from extract import _functions, _mask_comments, extract  # noqa: E402

STOCK = os.path.join(HERE, "ptp_mock_stock.c")

# The cluster to transplant vs. what is glue. Explicit, with reasons.
TRANSPLANT = {
    "mock_phc_adjfine": "region: lock { timecounter_read; cc.mult = MULT + adj }",
    "mock_phc_adjtime": "region: lock { tc.nsec += delta } (timecounter_adjtime inline)",
    "mock_phc_settime64": "region: lock { timecounter_init(tc, cc, ns) }",
    "mock_phc_gettime64": "region: lock { ns = timecounter_read(tc) }",
}
SKIP = {
    "mock_phc_create": "init path (spin_lock_init + registration) — probe replicates it, no critical section to transplant",
    "mock_phc_destroy": "teardown glue, no lock",
    "mock_phc_refresh": "wrapper: calls gettime64, takes no lock of its own",
    "mock_phc_cc_read": "cyclecounter callback (ktime_get_raw_ns), lock-free by design",
    "mock_phc_index": "accessor glue, no lock",
}


def function_source(src: str, name: str) -> str:
    """Full text of one function definition (signature + body) from the file."""
    masked = _mask_comments(src)
    for m in re.finditer(rf"(?<![\w.>])({re.escape(name)})\s*\([^;{{}}]*\)\s*\{{", masked):
        # walk back to the start of the signature line(s)
        sig_start = masked.rfind("\n", 0, masked.rfind("\n", 0, m.start())) + 1
        depth, i = 0, m.end() - 1
        while i < len(src):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    return src[sig_start : i + 1]
            i += 1
    raise KeyError(name)


def build_manifest() -> dict:
    src = open(STOCK).read()
    ir = extract(src)
    regions_found = sorted({r["function"] for r in ir["regions"]})
    manifest = {
        "subject": "drivers/ptp/ptp_mock.c (vendored verbatim)",
        "ir": {
            "structs": {s: v["locks"] for s, v in ir["structs"].items()},
            "protects": ir["protects"],
            "regions_found": regions_found,
        },
        "transplant": {},
        "skipped": SKIP,
    }
    for fn, why in TRANSPLANT.items():
        manifest["transplant"][fn] = {
            "why": why,
            "c_source": function_source(src, fn),
        }
    return manifest


def main() -> int:
    m = build_manifest()
    out = os.path.join(HERE, "out", "manifest.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump(m, fh, indent=1)
    print(f"extractor found regions in: {m['ir']['regions_found']}")
    print(f"protects: {json.dumps(m['ir']['protects'])}")
    print(f"transplant cluster ({len(m['transplant'])}): {sorted(m['transplant'])}")
    print(f"skipped ({len(m['skipped'])}): {sorted(m['skipped'])}")
    print(f"-> {out}")
    # sanity: every transplant target was actually found as a region by the extractor
    missing = [f for f in TRANSPLANT if f not in m["ir"]["regions_found"]]
    if missing:
        print(f"!! extractor did NOT find regions in: {missing}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
