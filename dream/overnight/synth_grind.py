"""Synth grind — make real progress overnight at $0.

Harvest pure kernel leaves tree-wide, and for each host-reachable one run the
FREE rungs of the ladder — c2rust (deterministic) then the local qwen model
(ollama) — verifying every candidate against the real C via hostdiff. No API
calls ($0), no kernel boots. Local inference is slow (~20-60s/fn), so this
genuinely fills the night while producing a real artifact: a list of kernel
functions verified bit-identical to their C by a free pipeline.

Incremental JSONL + heartbeat; a per-fn failure never stops the grind.

Usage: synth_grind.py [--max N] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for p in ("dream/ladder", "dream/localmodel", "dream/hostdiff", "dream/widerun"):
    sys.path.insert(0, os.path.join(REPO, p))
import hostdiff  # noqa: E402
import ladder  # noqa: E402
import localbench  # noqa: E402
import purity  # noqa: E402
import widerun  # noqa: E402
from widerun72 import host_tu_ok  # noqa: E402

KSRC = os.environ.get("KSRC", hostdiff.KSRC_DEFAULT)
WIDE = ["lib", "lib/math", "kernel", "kernel/time", "kernel/sched", "mm", "crypto",
        "block", "fs", "fs/ext4", "net/core", "net/ipv4", "sound/core", "security",
        "ipc", "drivers/gpio", "drivers/clk", "drivers/rtc", "drivers/pci"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(HERE, "reports", "synth_grind"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    jl = open(os.path.join(a.out, "grind.jsonl"), "w")
    t0 = time.time()

    def hb(m):
        print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

    widerun.DIRS = WIDE
    work = widerun.harvest()
    pn = set()
    for _ in range(3):
        pn = {w["sym"] for w in work if purity.classify(w["body"], pn, w["sym"])[0] == "pure"}
    pure = [w for w in work if w["sym"] in pn]
    reach = [w for w in pure if host_tu_ok(w["file"], w["sym"])]
    if a.max:
        reach = reach[:a.max]
    hb(f"GRIND start | {len(work)} harvested | {len(pure)} pure | {len(reach)} host-reachable (T0)")

    by_rung = {"c2rust": 0, "local-14b": 0}
    verified, unsolved = [], []
    for i, w in enumerate(reach):
        path, func = w["file"], w["sym"]
        rung = None
        try:
            src = open(os.path.join(KSRC, path)).read()
            ret, params = hostdiff.parse_sig(src, func)
            _, sig_line = localbench.rust_sig(func, ret, params)
            csrc = localbench.context_of(src, func)
            # rung 0: c2rust ($0, deterministic)
            rs, _note = ladder.c2rust_rung(path, func, [])
            if rs is not None and ladder.verify(path, func, [], rs, "sg")["verdict"] == "MATCH":
                rung = "c2rust"
            else:  # rung 1: local model ($0)
                ok, _log = ladder.local_rung(path, func, [], csrc, sig_line, attempts=2)
                if ok:
                    rung = "local-14b"
        except Exception as e:
            rec = {"fn": func, "file": path, "verdict": "ERROR", "detail": str(e)[:80]}
            jl.write(json.dumps(rec) + "\n"); jl.flush()
            continue
        rec = {"fn": func, "file": path, "verdict": rung or "unsolved"}
        jl.write(json.dumps(rec) + "\n"); jl.flush()
        if rung:
            by_rung[rung] += 1
            verified.append({"fn": func, "file": path, "rung": rung})
        else:
            unsolved.append(func)
        hb(f"  {i+1}/{len(reach)} {func:24s} -> {rung or 'unsolved'}  "
           f"(verified {len(verified)}, {round(time.time()-t0)}s)")

    summary = {"harvested": len(work), "pure": len(pure), "reachable": len(reach),
               "verified": len(verified), "by_rung": by_rung,
               "verified_fns": verified, "unsolved": unsolved,
               "external_cost_usd": 0.0, "wall_s": round(time.time() - t0)}
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    hb("=" * 60)
    hb(f"GRIND DONE: verified {len(verified)}/{len(reach)} at $0 ({by_rung}) | {summary['wall_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
