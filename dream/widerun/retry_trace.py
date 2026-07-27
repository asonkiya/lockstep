#!/usr/bin/env python3
"""Retry the quarantined (impure) functions with the observation oracle.

The purity router quarantined 59 functions for reading kernel state. They split:

  READ-ONLY  (read state, no side effect): checkable by the IN-KERNEL differential
             — the C reads the real state, the Rust is a pure reimplementation,
             and for a read-only function return-equivalence IS behavior-
             equivalence (there is no effect to miss). Passing here means "behaves
             like the C in this config" — config-sound, which is exactly what a
             per-config rewrite needs. It recovers the functions the conservative
             router over-quarantined (state present but output-irrelevant, e.g.
             gcd/lcm) and correctly still-rejects the ones whose output actually
             depends on state the Rust can't see.

  EFFECTFUL  (write state / register / IPI / freeze / ...): return-equivalence is
             NOT enough (the Ring 3 lesson). These need a recorded EFFECT trace,
             which is per-function modeling — reported here, not auto-run.

So this is the honest trace-oracle retry: recover what's soundly recoverable,
quantify what genuinely needs an effect model.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import purity  # noqa: E402
import widerun  # noqa: E402


def main():
    harvested = widerun.harvest()
    pn = set()
    for _ in range(3):
        pn = {w["sym"] for w in harvested if purity.classify(w["body"], pn, w["sym"])[0] == "pure"}
    quarantined = [w for w in harvested if w["sym"] not in pn]
    # SOUND recoverable set: differs from pure only by reading state (no effect
    # marker, no opaque/non-pure call that could hide one).
    read_only = [w for w in quarantined if purity.recoverable_readonly(w["body"], pn, w["sym"])]
    effectful = [w for w in quarantined if w not in read_only]
    print(f"quarantined {len(quarantined)}: {len(read_only)} read-only-recoverable "
          f"(in-kernel differential), {len(effectful)} effectful/opaque (need effect trace)")

    print(f"[retry] synthesizing {len(read_only)} read-only candidates...")
    total, compiled = 0.0, []
    idx = {w["sym"]: w for w in read_only}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for sym, ok, c in ex.map(widerun.synth, read_only):
            total += c
            if ok:
                compiled.append(idx[sym])
    print(f"[retry] synth: {len(compiled)}/{len(read_only)} compiled, ${total:.4f}")

    widerun.restore()
    allv, alldrop = {}, {}
    batches = [compiled[i:i + widerun.BATCH] for i in range(0, len(compiled), widerun.BATCH)]
    for bi, batch in enumerate(batches):
        print(f"[retry] batch {bi+1}/{len(batches)} ({len(batch)}): build+boot...")
        verd, dropped = widerun.build_boot(batch, f"retry{bi}")
        if verd is None:
            print(f"  batch {bi+1} unrecoverable"); continue
        allv.update(verd); alldrop.update(dropped)
        npass = sum(1 for v in verd.values() if v[0] == "PASS")
        print(f"  batch {bi+1}: {npass}/{len(verd)} recovered, {len(dropped)} dropped")

    recovered = sorted(k for k, v in allv.items() if v[0] == "PASS")
    stilldiv = sorted(k for k, v in allv.items() if v[0] == "FAIL")
    result = {
        "quarantined": len(quarantined), "read_only": len(read_only), "effectful": len(effectful),
        "read_only_compiled": len(compiled), "read_only_booted": len(allv),
        "recovered_config_sound": recovered, "still_state_dependent": stilldiv,
        "dropped_unlinkable": len(alldrop),
        "effectful_need_trace": sorted(w["sym"] for w in effectful),
        "synth_cost_usd": round(total, 4),
    }
    json.dump(result, open(os.path.join(HERE, "retry_result.json"), "w"), indent=1)
    print(f"\n=== TRACE-ORACLE RETRY ===")
    print(f"quarantined {len(quarantined)} = {len(read_only)} read-only + {len(effectful)} effectful")
    print(f"read-only booted {len(allv)} -> RECOVERED (config-sound) {len(recovered)}, "
          f"still state-dependent {len(stilldiv)}")
    print(f"recovered: {', '.join(recovered)}")
    print(f"effectful {len(effectful)} -> need per-function effect trace (not auto-run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
