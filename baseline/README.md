# M0 — the sanitizer baseline

The first rung. Before Lockstep can *judge* a region transplant, it needs the
oracle that will do the judging, and a known-good reading from it on unmodified
code.

`baseline.sh` boots a kernel with the two concurrency sanitizers that are
Lockstep's core oracle — **KCSAN** (data-race detector) and **lockdep**
(lock-order / deadlock validator) — under a real in-kernel workload (the crypto
self-tests + KUnit), on **SMP** (4 CPUs, so there is actual concurrency to
observe), and records exactly what they report on **stock** code.

That recording is the **baseline**: the set of KCSAN/lockdep findings present in
the unmodified kernel. Every later transplant is accepted only if it adds **no
new** finding to this set — the M2+ gate is a diff against this baseline, not an
absolute "zero reports" (a stock kernel is not always perfectly clean, and
honesty means measuring the delta, not pretending).

## Run

```bash
./baseline/baseline.sh /path/to/linux-src ./m0-out
```

Reuses CGIR rung 4's container (`cgir-kernel-gate`) and kernel-tree volume
(`cgir-kbuild`) — M0 *is* rung 4's build+boot gate plus the sanitizer configs,
exactly as the design doc frames it. Lockstep grows its own container once M0 is
proven.

## What M0 proves (and doesn't)

- **Proves**: the sanitizer harness builds and boots; the baseline is captured and
  reproducible; we can read KCSAN races and lockdep splats off a real workload.
- **Doesn't yet**: find races in a transplant (that's M2, once there is a
  transplant to test), or exercise deep concurrency (a boot-time crypto/KUnit
  load is a start; syzkaller as the adversarial driver is a later rung).

## Outputs

- `baseline-console.txt` — the full boot + workload console.
- `baseline-findings.txt` — extracted KCSAN/lockdep lines (the baseline set).
- A summary: KCSAN report count, lockdep splat count, whether init was reached.
