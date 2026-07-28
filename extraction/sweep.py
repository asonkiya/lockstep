#!/usr/bin/env python3
"""Run the M1 extractor over real kernel .c files and print each subsystem's
concurrency IR. Usage:

    python3 sweep.py path/to/linux/drivers/gpio/gpio-zevio.c ...

Paths are whatever you point at (a kernel checkout is not vendored here). Prints
the lock-bearing structs, critical-section count, protects map, and unprotected
accesses per file — the static half of the M1 reading.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract import extract  # noqa: E402


def main(paths: list[str]) -> int:
    for p in paths:
        try:
            ir = extract(open(p).read())
        except OSError as e:
            print(f"!! {p}: {e}")
            continue
        print("=" * 70)
        print(p)
        print("  lock structs :", {s: v["locks"] for s, v in ir["structs"].items()})
        print(
            "  sections     :",
            len(ir["regions"]),
            "in",
            sorted({r["function"] for r in ir["regions"]}),
        )
        print("  protects     :", json.dumps(ir["protects"]))
        print("  unprotected  :", [u["field"] for u in ir["unprotected_accesses"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
