#!/usr/bin/env python3
"""The scale run — point the tested, hardened machine at a wide tree-wide harvest
and verify at scale through the boot-free T0 tier.

Harvest scalar-exported leaves across the whole tree, route each through the
router's soundness-ordered classifier, and execute T0_HOST (ladder synth +
hostdiff differential) at scale — boot-free, parallel, cheap. Reports the
largest sound-verified count to date with the false-pass count (0, now
test-guaranteed). A live CANARY (deliberately-wrong candidates woven into the
run) must never verify — if one does, the whole run's passes are void.

T1 (in-kernel, batched boot) is a separate, slower phase; this is the boot-free
production sweep.

Usage: scale_run.py [--workers N] [--local-attempts K] [--out scale_result.json]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for p in ("dream/router", "dream/widerun", "dream/hostdiff", "dream/sweep"):
    sys.path.insert(0, os.path.join(REPO, p))
import hostdiff  # noqa: E402
import router  # noqa: E402
import widerun  # noqa: E402

KSRC = os.environ.get("KSRC", hostdiff.KSRC_DEFAULT)

# wide tree coverage (vs widerun's default handful) — more subsystems, more leaves
WIDE_DIRS = [
    "lib", "lib/math", "kernel", "kernel/time", "kernel/sched", "kernel/locking",
    "mm", "crypto", "block", "fs", "fs/ext4", "fs/btrfs", "net/core", "net/ipv4",
    "net/ipv6", "sound/core", "security", "ipc", "arch/arm64/lib", "drivers/gpio",
    "drivers/clk", "drivers/pci", "drivers/base", "drivers/char", "drivers/misc",
    "drivers/rtc", "drivers/watchdog", "drivers/hwmon", "drivers/i2c",
]

# the canary: wrong candidates that MUST NOT verify (belt-and-suspenders at scale)
CANARIES = [
    ("lib/math/gcd.c", "gcd", "#[no_mangle] pub extern \"C\" fn cgir_gcd(a:u64,_b:u64)->u64{a}"),
    ("lib/math/int_pow.c", "int_pow", "#[no_mangle] pub extern \"C\" fn cgir_int_pow(_b:u64,_e:u32)->u64{1}"),
]


def run_canaries() -> list[str]:
    leaked = []
    for path, func, code in CANARIES:
        d = f"/private/tmp/scale_canary_{func}"
        os.makedirs(d, exist_ok=True)
        open(f"{d}/c.rs", "w").write(code)
        v = hostdiff.run(path, func, f"{d}/c.rs", [], KSRC, 200_000, quiet=True)["verdict"]
        if v == "MATCH":
            leaked.append(f"{func} ({v})")
    return leaked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--local-attempts", type=int, default=1)
    ap.add_argument("--out", default=os.path.join(HERE, "scale_result.json"))
    a = ap.parse_args()
    t0 = time.time()

    print("[scale] canary pre-check (wrong candidates must NOT verify)...")
    leaked = run_canaries()
    if leaked:
        print(f"[scale] ABORT — canary leaked (false pass): {leaked}"); return 2
    print("  ✓ canaries rejected")

    widerun.DIRS = WIDE_DIRS
    print(f"[scale] harvesting scalar-exported leaves across {len(WIDE_DIRS)} dirs...")
    work = widerun.harvest()
    print(f"  harvested {len(work)} leaves")

    # route
    pn = set()
    for _ in range(3):
        pn = {w["sym"] for w in work if __import__("purity").classify(w["body"], pn, w["sym"])[0] == "pure"}
    routes: dict[str, list] = {}
    for w in work:
        r, _why = router.route_one(w, pn, set())  # empty ksyms: T1-vs-unlinkable decided later
        routes.setdefault(r, []).append(w)
    print("[scale] routing:")
    for rt in sorted(routes, key=lambda k: -len(routes[k])):
        print(f"    {rt:16s} {len(routes[rt])}")

    # execute T0 at scale (boot-free) — the ladder+hostdiff, parallel
    t0set = routes.get("T0_HOST", [])
    print(f"\n[scale] executing T0_HOST on {len(t0set)} fns ({a.workers} workers, boot-free)...")
    verified, unsolved, spend = [], [], 0.0
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(router.run_t0, w, a.local_attempts): w for w in t0set}
        for fut in concurrent.futures.as_completed(futs):
            w = futs[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = {"rung": None, "cost": 0.0, "err": str(e)[:80]}
            spend += res.get("cost", 0.0)
            done += 1
            if res.get("rung"):
                verified.append({"func": w["sym"], "file": w["file"], "rung": res["rung"]})
            else:
                unsolved.append(w["sym"])
            if done % 20 == 0:
                print(f"    {done}/{len(t0set)} done | verified {len(verified)} | ${spend:.4f}")

    # canary re-check AFTER the run (nothing drifted)
    leaked_post = run_canaries()

    by_rung: dict[str, int] = {}
    for v in verified:
        by_rung[v["rung"]] = by_rung.get(v["rung"], 0) + 1
    print("\n=== SCALE RUN (boot-free T0) ===")
    print(f"  harvested {len(work)} | T0-eligible {len(t0set)} | "
          f"VERIFIED_T0 {len(verified)} | unsolved {len(unsolved)}")
    print(f"  by rung: {by_rung}")
    print(f"  spend ${spend:.4f} | wall {round(time.time()-t0)}s | "
          f"false passes {len(leaked_post)} (canary)")
    print(f"  routed-not-T0: " + ", ".join(f"{k}={len(v)}" for k, v in sorted(routes.items()) if k != 'T0_HOST'))
    json.dump({"harvested": len(work), "t0_eligible": len(t0set),
               "verified_t0": len(verified), "verified": verified,
               "by_rung": by_rung, "unsolved": unsolved, "spend": round(spend, 4),
               "false_passes": len(leaked_post),
               "routes": {k: len(v) for k, v in routes.items()}},
              open(a.out, "w"), indent=1)
    return 0 if not leaked_post else 1


if __name__ == "__main__":
    sys.exit(main())
