# M5 results — the transplant as a maintainer-reviewable series

M5 is a formatter, not a milestone of new capability: everything in the emitted
series already existed as verified artifacts (M4 breadth's manifest, winner
cluster, and gate consoles). `emit.py --verify` regenerates and gates it; it is
green.

## The series (`out/`)

| file | content |
|------|---------|
| `0000-cover-letter` | methodology, the gate table, the KCSAN conviction excerpt, honest limitations |
| `0001` | `drivers/ptp/ptp_mock_regions.rs` — **byte-identical to the boot-verified artifact** — + the freestanding build rule |
| `0002` | real diff to `drivers/ptp/ptp_mock.c`: the four op bodies become calls to the Rust regions — **exactly the topology the in-kernel gate verified** |
| `0003` | RFC-only: the idiomatic `kernel::sync::SpinLock<T>` destination, missing bindings (`timecounter`, `cyclecounter`, PTP class) called out in the header |

The series structure mirrors the verification claim: what is *proposed* (0001 +
0002) is what was *proven*; what is *aspirational* (0003) is labeled RFC-only
and says it does not compile. Evidence travels in the cover letter: the
three-leg verdict table, the `lockstep_phc_adjfine`-named KCSAN report from the
negative control, cost ($0.0084), and the limitations list (harness naming,
arm64-only build rule, unchanged registration path).

## M5's own gates

- **Applies**: the three patches `git apply` in sequence onto the stock tree
  (fresh repo containing the vendored `ptp_mock.c` + `Makefile`). ✓
- **The kernel's own reviewer**: `scripts/checkpatch.pl` from the gate
  container's tree — **0 errors on all four files** (9 warnings, all expected
  and disclosed: externs in a .c file per the partial-conversion shape, RFC
  long lines). ✓

## Status

M5 done in its role as export format. Per the project pivot (2026-07-26): the
goal is no longer upstream submission — it is the private full-kernel rewrite
ratchet — so this series will not be mailed anywhere. The emitter is the
keepsake: any future ratchet entry (function or cluster + its gate evidence)
can be exported to this form by the same machinery if that ever changes.

**The design ladder M0–M5 is complete.**
