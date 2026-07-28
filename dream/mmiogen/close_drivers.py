#!/usr/bin/env python3
"""Run the MMIO-record harness generator over the router's T3_TRACE routees and
measure real coverage: how many driver register functions auto-close (generate a
passing, non-vacuous record/replay harness) vs. refuse (helper-wrapped accessor,
computed offset, bank abstraction — the honest edge).

Reads the driver router result + worklist so the set is exactly what the router
routed to the recorder. Reports per-function verdict + a coverage number.

Usage: close_drivers.py [--router ../router/router_driver_result.json]
                        [--worklist ../router/driver_worklist.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mmio_harness as mh  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--router", default=os.path.join(HERE, "..", "router", "router_driver_result.json"))
    ap.add_argument("--worklist", default=os.path.join(HERE, "..", "router", "driver_worklist.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "close_drivers_result.json"))
    a = ap.parse_args()

    rr = json.load(open(a.router))
    wl = {w["sym"]: w for w in json.load(open(a.worklist))}
    trace = [r["func"] for r in rr["rows"] if r["route"] == "T3_TRACE"]
    print(f"[close] {len(trace)} T3_TRACE routees to attempt...\n")

    rows, closed, refused, failed = [], 0, 0, 0
    for sym in trace:
        w = wl.get(sym)
        if not w:
            continue
        out = f"/private/tmp/mmiogen_{sym}"
        try:
            ex = mh.generate(w["file"], sym, out)
        except mh.Unsupported as e:
            refused += 1
            rows.append({"func": sym, "verdict": "REFUSED", "reason": str(e)[:70]})
            continue
        v1, _ = mh.gate(sym, out, f"{sym}_cand.rs")
        v2, _ = mh.gate(sym, out, f"{sym}_bad.rs")
        if v1 == "MATCH" and v2 == "DIVERGE":
            closed += 1
            prog = " ".join(f"{k}({n})" for k, _, n, _ in ex["program"] if n)
            rows.append({"func": sym, "verdict": "CLOSED", "program": prog,
                         "out_of_trace": ex["out_of_trace"]})
        else:
            failed += 1
            rows.append({"func": sym, "verdict": f"harness_fail({v1}/{v2})"})

    print("per-function:")
    for r in rows:
        tag = r["verdict"]
        extra = r.get("program", r.get("reason", ""))
        print(f"  {r['func']:30s} {tag:22s} {extra}")
    print(f"\n=== MMIOGEN COVERAGE ===")
    print(f"  T3_TRACE routees {len(trace)} | CLOSED {closed} (recorder-verified + control-rejected) "
          f"| refused {refused} | harness-fail {failed}")
    json.dump({"rows": rows, "closed": closed, "refused": refused, "failed": failed,
               "total": len(trace)}, open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
