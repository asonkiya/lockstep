# The ratchet + Ring 0 — a real driver's regions, Rust, in a booting kernel

The ratchet (design: `DESIGN.md`) turns "N verified transplants" into "a booting
kernel that is X% Rust, tracked, non-regressing." Ring 0 is its first real run:
`drivers/ptp/ptp_mock`'s locked-region cluster, woven into the live kernel by the
manifest, booting.

## The pipeline, run end-to-end (`weave.py gate`)

```
config: CONFIG_PTP_1588_CLOCK_MOCK=y        # weaver enables the real driver
wove drivers/ptp/ptp_mock.c: 4 bodies -> Rust seam calls
✓ compiled + wired drivers/ptp/ptp_mock_regions.o (Rust)
✓ Image built (woven kernel)
boot-digest: smp_up=True boot_complete=True early_panic=False
✓ woven kernel boots — Rust ptp_mock regions live in vmlinux

=== ratchet dashboard ===
  functions -> Rust : 4/9  (44.4% of tracked bodies)
  strongly gated    : 4/4 (differential:PASS)
  drivers/ptp/ptp_mock.c: mock_phc_adjfine, adjtime, settime64, gettime64  [differential:PASS]
```

This is the "one-command pass" from the original dream, made real and mechanical:
`weave.py` reads the manifest, **excises each `status:rust` function from the
actual in-tree `.c`** (body → Rust seam call, the M5 mechanism generalized),
inserts the extern block, compiles the Rust regions into one object, wires kbuild,
enables the config, builds, and boots. No hand-editing.

## Why this is Ring 0 and not just another gate

Every prior milestone ran a *bespoke* gate on a *vendored* copy. Ring 0 is the
**infrastructure** doing it against the **live kernel tree**, driven by a
**manifest** that is the single source of truth and the dashboard. Adding the
next function is a manifest row, not a new script. That is the difference between
"we proved a transplant" and "we have a ratchet."

## The two-part correctness claim (honest)

1. **Compiles + links + boots** — the boot-digest above: the woven vmlinux (real
   driver, Rust regions, config enabled) brings up SMP and completes kernel init
   without panic. The Rust `lockstep_phc_*` object is linked into the kernel image
   and does not break it.
2. **Behaviorally correct** — the differential gate (`dream/diffgate`, committed
   separately) already proved these exact regions bit-identical to the C original
   across 64 ops, and rejected a wrong-but-non-crashing variant. The manifest
   records each as `gate: differential, verdict: PASS`.

Note kept honest: `ptp_mock` has no consumer in this config, so the regions do not
*execute* during the boot-digest — the boot proves link/compile/no-breakage, the
differential harness proves behavior. Wiring the differential probe into the woven
build (so the same driver-linked object is exercised in the same boot) is the
obvious Ring-0.1 hardening.

## Dashboard semantics (the metric that matters)

- **44.4% of ptp_mock's function bodies are Rust** — the 4 locked regions. The
  other 5 (cc_read callback, refresh wrapper, index accessor, create/destroy init
  glue) are the classify-and-skip glue from the research's tier analysis.
- **4/4 strongly gated (differential)** — not weakly attested. The dashboard keeps
  the distinction the research demands: proven (differential/kunit) vs
  boot-survival-only, never conflated.

## State + reproducibility

The weaver mutates the live `cgir-kbuild` volume (ratchet semantics: the tree
stays woven). The woven `ptp_mock.c` is saved to `out/` and the transform is
deterministic from `manifest.json`, so the state is reproducible / restorable from
the source tree.

## Status: the ratchet exists and carried its first real driver

- Manifest → weave → build → boot → dashboard, end to end, on the live tree. ✅
- A real in-tree driver's whole locked-region cluster is Rust in a booting kernel. ✅
- Every Rust region strongly gated (differential), tracked as such. ✅
- Next: Ring 0.1 (differential probe in the woven boot), then Ring 1 (lib/ leaves,
  where the oracle is real — accumulate manifest rows, watch %-Rust climb, prove
  the ratchet never regresses a green entry).
