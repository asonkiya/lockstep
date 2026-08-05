# Pre-registration: defconfig weave run (Phase A of the realize campaign)

Committed BEFORE the weave batch runs on the defconfig base. The stock
defconfig kernel is built and its denominator measured; no weave result has
been observed.

## Question

Does a platform-true (defconfig-class) config lift the present-in-vmlinux
woven-reader count past the minimal config's 16 — i.e., is config coverage the
binding lever for integration, as the larger-config run (5963d35) hypothesized?

## Frozen denominator (measured on the STOCK defconfig vmlinux, pre-weave)

- 104 verified readers across 92 source files (the sweep bank, unchanged).
- Files built under arm64 defconfig: **21/92** (71 NOTBUILT — driver hardware
  even defconfig omits for this arch; 1 built file has no global syms to
  confirm linkage and is conservatively excluded).
- Files with objects linked into the stock vmlinux: **19**.
- **D = 20** verified readers live in linked files. This is the ceiling.
- Recorded in `defconfig_denominator.json` (generated artifact).

## The pre-committed bar, graded against the measurement — BEFORE weaving

The campaign plan committed SUCCESS = present ≥ max(2×16, 0.6×D) = **32**.
With D = 20 that bar is **unachievable**: the defconfig lever CANNOT reach
SUCCESS no matter how well the weave goes. We record that verdict now, from
the denominator alone: **the "defconfig doubles presence" hypothesis is
FALSIFIED by measurement** — the verified-reader population is concentrated
in files (drivers) that platform configs don't build for arm64-virt.

The weave batch still runs, judged on the honest residual question:

## Residual decision rule (also preset, before the batch)

Let P = readers present in the woven defconfig vmlinux (nm-checked seams).

- **LEVER-ALIVE**: P ≥ 17 and P > (minimal-config carryover ceiling) — the
  defconfig adds net new in-vmlinux readers beyond what minimal certified.
- **LEVER-MARGINAL**: P in 12..16 with boot green — defconfig certifies a
  *different* subset (e.g. loses lockdep.c, gains driver files) but no net
  growth; presence is config-relative and the union across configs is the
  honest aggregate metric.
- **LEVER-DEAD**: P < 12 or boot red.

Invariants (unchanged from RUN2/SWEEP1): file-qualified keys, no silent drops
— every one of the 104 must appear in exactly one of {woven-present,
woven-not-linked, compile-dropped, link-dropped, file-not-built}; boot gate =
smp_up + boot_complete + no early panic; 0 false passes (guards + differential
unchanged).

## Known asymmetries recorded up front

- kernel/locking/lockdep.c requires CONFIG_LOCK_STAT (not in defconfig): the
  minimal config's lock_time_add / lock_time_inc CANNOT appear here. Presence
  is per-config; cross-config union is a separate (honest) metric.
- The batch's funnel may drop files whose struct layouts drift under
  defconfig (in-tree _Static_assert fails the compile). Those are named
  re-verify worklist entries, not failures of the weave mechanism.
