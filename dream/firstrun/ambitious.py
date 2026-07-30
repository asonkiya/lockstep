#!/usr/bin/env python3
"""Ambitious rewrite run — the first run did 8 boot-verified Rust functions in 8
minutes for 8 cents; $7 should do ~100x. Reuses the PROVEN firstrun machinery
(overnight.py) unchanged, with three levers turned up:

  * whole-tree scalar-leaf harvest (was a lib/kernel/mm/... subset)
  * budget $7, 6 workers, 5h runtime cap, N_LEAVES=600
  * bigger boot-weave (up to 120 leaves, chunked in 40s)

Writes to a SEPARATE output dir (dream/firstrun/ambitious/) so the first run's
results are untouched. Same guards (hard budget cap, runtime cap, resumable,
0-false-pass gates), same soundness.
"""
import os
import sys

# raise caps BEFORE importing overnight (it reads these at module load)
os.environ.setdefault("BUDGET_CAP", "7.0")
os.environ.setdefault("N_LEAVES", "600")
os.environ.setdefault("WORKERS", "6")
os.environ.setdefault("RUNTIME_CAP_H", "5")
os.environ.setdefault("PHASE2_MAX", "120")

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("widerun", "hostdiff", "family", "structdiff"):
    sys.path.insert(0, os.path.join(HERE, "..", p))

import widerun  # noqa: E402
# whole-tree scalar exported leaves (harvest still filters to pure scalar leaves;
# most driver fns are dropped by the SKIP/parse filters, so this just widens the
# net without loosening soundness).
widerun.DIRS = ["lib", "kernel", "mm", "crypto", "block", "fs", "net", "security",
                "ipc", "sound", "arch/arm64", "drivers"]

import overnight  # noqa: E402
# redirect all outputs to a separate dir so the first run is untouched
AMB = os.path.join(HERE, "ambitious")
os.makedirs(AMB, exist_ok=True)
overnight.VERIFIED = os.path.join(AMB, "verified")
overnight.LOG = os.path.join(AMB, "run.log")
overnight.PROGRESS = os.path.join(AMB, "progress.json")
overnight.REPORT = os.path.join(AMB, "REPORT.md")

if __name__ == "__main__":
    raise SystemExit(overnight.main())
