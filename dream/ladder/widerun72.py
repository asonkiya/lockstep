#!/usr/bin/env python3
"""Point the synth ladder at the widerun's 72 harvested functions.

The 8-fn battery measured the ladder's shape; this measures its RATES at n=72 on
the real tree-wide harvest (scalar exported leaves across lib/kernel/mm/fs/pci/
drivers…). Same rungs, same arbiter: c2rust ($0) → qwen2.5-coder:14b ($0, ≤2)
→ Haiku (≤2, ledgered), every candidate judged by hostdiff.

Router discipline (the widerun's own lesson): purity.json partitions the 72 into
13 PURE / 59 quarantined. A hostdiff MATCH is a SOUND T0 verdict only for the
pure set. For quarantined fns the ladder still runs — the number measures the
SYNTH rates and the shim's coverage — but a MATCH is recorded as
`host_attested` (equal to the shim-pinned C on the host), NOT `verified`; those
functions still owe the in-kernel trace oracle. No unsound passes, ever.

Failure classes are data, not noise: CC_TU_FAIL = shim gap (grow kshim.h);
LINK_FAIL = cross-TU dep the runner didn't provide; SIG_SKIP = signature
outside T0's scalar map.

Usage: widerun72.py [--skip-haiku] [--out widerun72_results.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for p in ("hostdiff", "cluster", "localmodel", "widerun"):
    sys.path.insert(0, os.path.join(HERE, "..", p))
sys.path.insert(0, os.path.join(HERE, "..", "widerun"))
import hostdiff  # noqa: E402
import ladder  # noqa: E402
import localbench  # noqa: E402
import widerun  # noqa: E402

KSRC = os.environ.get("KSRC", hostdiff.KSRC_DEFAULT)


def host_tu_ok(path: str, func: str) -> bool:
    """Can hostdiff build a C reference for `func` on the host? True if the whole
    TU compiles with the shim OR — the function-scoped fallback — just the
    function + its file-static callees + constants do. Mirrors hostdiff.run's
    own two-step (whole-TU, then minimal_tu), so the router routes to T0 exactly
    what hostdiff can actually verify. The limit is now genuine (the function
    itself needs an untranslatable type), not sibling/struct pollution."""
    import shutil
    import subprocess
    import tempfile
    w = tempfile.mkdtemp(prefix=f"tucheck_{func}_", dir="/private/tmp")
    shutil.copy(os.path.join(HERE, "..", "hostdiff", "kshim.h"), w)

    def _compiles(src: str) -> bool:
        open(f"{w}/tu.c", "w").write(src)
        return subprocess.run(["cc", "-O0", f"-I{w}", "-fsyntax-only", f"{w}/tu.c"],
                              capture_output=True, text=True).returncode == 0

    if _compiles(hostdiff.shim_tu(path, KSRC)):
        return True
    try:
        return _compiles(hostdiff.minimal_tu(path, KSRC, func))
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-haiku", action="store_true")
    ap.add_argument("--out", default=os.path.join(HERE, "widerun72_results.json"))
    a = ap.parse_args()

    work = widerun.harvest()  # deterministic re-walk -> {sym, file, ret, args, body}
    pure = set(json.load(open(os.path.join(HERE, "..", "widerun", "purity.json")))["pure"])
    print(f"harvest: {len(work)} fns ({len([w for w in work if w['sym'] in pure])} pure / "
          f"{len([w for w in work if w['sym'] not in pure])} quarantined)")

    rows, spend, t_all = [], 0.0, time.time()
    for w in work:
        path, func = w["file"], w["sym"]
        src = open(os.path.join(KSRC, path)).read()
        row = {"func": func, "file": path, "pure": func in pure,
               "rung": None, "cost": 0.0, "log": {}}
        t0 = time.time()

        # signature must be in T0's scalar map (probe generation needs it)
        try:
            ret, params = hostdiff.parse_sig(src, func)
        except SystemExit as e:
            row["log"]["sig"] = f"SIG_SKIP: {e}"
            row["secs"] = round(time.time() - t0, 1)
            rows.append(row)
            print(f"  {func:28s} -> SIG_SKIP")
            continue
        _, sig_line = localbench.rust_sig(func, ret, params)
        csrc = localbench.context_of(src, func)

        # PRECHECK (the $0.25 lesson): if the shimmed TU won't even compile on
        # the host, NO rung can be verified here — the failure is the harness's
        # shim coverage, not the translation. Skip all rungs, spend nothing.
        if not host_tu_ok(path, func):
            row["log"]["precheck"] = "CC_TU_FAIL (shim gap — not host-verifiable)"
            row["secs"] = round(time.time() - t0, 1)
            rows.append(row)
            print(f"  {func:28s} -> UNVERIFIABLE_HOST [shim gap]  $0.0000")
            continue

        # rung 0: c2rust
        rs, note = ladder.c2rust_rung(path, func, [])
        if rs is not None:
            res = ladder.verify(path, func, [], rs, "c2rust")
            row["log"]["c2rust"] = res["verdict"]
            if res["verdict"] == "MATCH":
                row["rung"] = "c2rust"
        else:
            row["log"]["c2rust"] = f"skip: {note[:80]}"

        # rung 1: local 14B
        if row["rung"] is None:
            ok, log = ladder.local_rung(path, func, [], csrc, sig_line)
            row["log"]["local"] = log
            if ok:
                row["rung"] = "local-14b"

        # rung 2: Haiku
        if row["rung"] is None and not a.skip_haiku:
            ok, log, cost = ladder.haiku_rung(path, func, [], csrc, sig_line)
            row["log"]["haiku"] = log
            row["cost"] = round(cost, 6)
            spend += cost
            if ok:
                row["rung"] = "haiku"

        # router discipline: MATCH is sound only for the pure set
        if row["rung"] is not None:
            row["status"] = "verified_T0" if row["pure"] else "host_attested"
        row["secs"] = round(time.time() - t0, 1)
        rows.append(row)
        tag = row["rung"] or "UNSOLVED"
        print(f"  {func:28s} -> {tag:10s} [{row.get('status','-'):13s}] "
              f"${row['cost']:.4f} {row['secs']:6.1f}s")

    n = len(rows)
    sig_skip = [r for r in rows if "sig" in r["log"]]
    attempted = [r for r in rows if "sig" not in r["log"]]
    by = {k: sum(1 for r in rows if r["rung"] == k) for k in ("c2rust", "local-14b", "haiku", None)}
    solved = n - by[None] - len(sig_skip)
    pure_v = sum(1 for r in rows if r.get("status") == "verified_T0")
    att = sum(1 for r in rows if r.get("status") == "host_attested")
    # failure taxonomy over unsolved — the DEEPEST rung's real verdict, so a
    # shim gap reads as CC_TU_FAIL, not as "c2rust skipped"
    tax: dict[str, int] = {}
    for r in attempted:
        if r["rung"] is not None:
            continue
        lg = r["log"]
        if "precheck" in lg:
            key = "UNVERIFIABLE_HOST (shim gap)"
        elif isinstance(lg.get("haiku"), list) and lg["haiku"]:
            key = lg["haiku"][-1]
        elif isinstance(lg.get("local"), list) and lg["local"]:
            key = lg["local"][-1]
        else:
            key = (lg.get("c2rust", "?") or "?").split(":")[0][:24]
        tax[key] = tax.get(key, 0) + 1

    print(f"\nWIDERUN-72 LADDER: {solved}/{len(attempted)} solved "
          f"({len(sig_skip)} SIG_SKIP excluded) | "
          f"c2rust {by['c2rust']} | local {by['local-14b']} | haiku {by['haiku']}")
    print(f"  sound T0 verified (pure) : {pure_v}")
    print(f"  host-attested (quarantined; trace oracle still owed): {att}")
    print(f"  unsolved taxonomy: {tax}")
    print(f"  spend ${spend:.4f} | wall {round(time.time() - t_all, 1)}s")
    json.dump({"rows": rows, "spend": round(spend, 6)}, open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
