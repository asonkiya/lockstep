# The scale run — the tested machine, pointed at the tree

With the soundness suite green (145 adversarial tests, 0 false passes pinned) and
the gates hardened, this is the production sweep: harvest wide, route through the
hardened 4-tier router, and verify at scale. It reports the real numbers and,
more importantly, *where the single-machine throughput ceiling actually is* — the
input to the worker-weeks estimate.

Every run carries a live **canary**: deliberately-wrong candidates that must
never verify. A false pass at scale trips it and voids the run. Both phases below
ran with `false_passes = 0`.

## Phase 1 — boot-free T0, at scale

Harvested **86** scalar-exported leaves across 29 subsystem dirs (lib, kernel,
mm, crypto, block, fs, net, sound, security, ipc, arch, and eight `drivers/`
trees). Routed and executed the boot-free T0 tier:

```
harvested 86 | T0-eligible 6 | VERIFIED_T0 5 | spend $0.0039 | 23s | false passes 0
routing: T3_EFFECT 51 · T2_MIRROR 15 · T1_UNLINKABLE 8 · T0_HOST 6 · TC_REGION 3 · C_FOREVER 2 · T3_TRACE 1
```

The headline is the **ceiling, not the count**: widening the harvest from 72 → 86
did *not* widen T0. Only 6 leaves have a translation unit that compiles
standalone with the host shim — the rest `#include` subsystem headers the shim
doesn't carry. **Boot-free host verification is lib-bounded** (the widerun-72
finding, now confirmed at wider scope): scaling the harvest scales the *other*
buckets, not T0. What T0 does deliver, it delivers for ~$0 and instantly.

## Phase 2 — one T1 batched boot

The 6 pure/read-only linkable functions synth'd and verified in **one** kernel
build + boot against the live symbols:

```
__kfifo_max_r            -> verified_T1
nfs_check_flags          -> verified_T1
xas_try_split_min_order  -> verified_T1
__node_distance          -> verified_T1_at_boot_state   (read-only: checked at ONE boot state, not full equivalence)
cper_severity_to_aer     -> T1_rejected (bad=1998)       (enum/table over-tested by the 0..2000 domain — conservative)
pci_rebar_bytes_to_size  -> T1_rejected (bad=2001)       (   "                                                      )

wall 445s (one boot) | spend $0.0048 | false passes 0
```

The hardening is visible in the verdicts. `__node_distance` is now
**`verified_T1_at_boot_state`**, not a flat `verified_T1` — the relabel that
honestly distinguishes "read-only, agrees at one boot-time state" from "full
behavioral equivalence." The two rejects are the oracle being conservative in
the safe direction (a false *reject*, not a false *accept*), which also proves
the boot gate is non-vacuous.

## Combined result

```
harvested 86 (wide) + 72 (widerun) worklists
VERIFIED sound        : 8   (5 boot-free T0 + 3 in-kernel T1)
boot-state-attested   : 1   (__node_distance, honestly labeled)
conservatively rejected: 2  (non-vacuous)
false passes          : 0   (canary + delegation + no-forward gates all live)
total spend           : ~$0.009
```

## The three throughput ceilings (the real output of the run)

The scale run's value is quantifying why one machine can't just grind to a
majority-Rust kernel in a session — and exactly what removes each ceiling:

1. **Host-TU compilability (bounds T0).** Boot-free verification needs the C TU
   to compile standalone with a shim. Only lib-shaped self-contained TUs do →
   ~single-digit T0 per harvest regardless of size. *Removed by:* growing the
   shim per header family (or accepting the boot tier for the rest).
2. **Config coverage (bounds T1).** The in-kernel differential can only verify a
   function whose symbol is *linked in this config*. A minimal kernel links a
   fraction of what's harvested → most pure leaves route to `T1_UNLINKABLE`.
   *Removed by:* a fuller config (more symbols linked, more testable) — at the
   cost of longer builds.
3. **Per-family artifacts (bound T2/T3 — the bulk).** The large buckets
   (`T2_MIRROR` structs, `T3_EFFECT`/`T3_TRACE` drivers) each owe a mirror or a
   recording. *Removed by:* the generator grind — mirror per struct family,
   MMIO harness per driver family (the itemized backlogs in `mmiogen/RESULTS.md`
   and `DRIVER-RUN.md`).

None of these is a *mechanism* gap — every tier's machine is built and
test-hardened. They are throughput gates, and each has a named lever. Multiply
them out and you get the worker-weeks + per-family-artifact estimate the project
has carried throughout: a majority-Rust minimal kernel is a provisioned grind,
not a missing invention.

## What this run establishes

- The hardened pipeline runs end to end at wide scale with **0 false passes**,
  now enforced live by the canary, not just asserted by the test suite.
- The boot-free tier's throughput ceiling is empirical and lib-shaped.
- The volume is in the boot/mirror/recorder tiers, each with a concrete unlock.
- Cost is noise ($0.004 for the T0 sweep) — verification throughput, not tokens,
  is the whole cost, exactly as `COSTDOWN.md`/`RESEARCH.md` predicted.

## Files

`scale_run.py` (wide harvest + route + boot-free T0 at scale, with canary),
`scale_result.json`. The T1 phase is the router's own `t1_boot` (batched, one
boot). Companion: `RESULTS.md` (the router), `../COSTDOWN.md`, `../SWEEP.md`.
