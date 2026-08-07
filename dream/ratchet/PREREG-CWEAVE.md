# Pre-registration — containers weave (Summit 1.1)

Written 2026-08-07, BEFORE any weave attempt. Denominator frozen by
`cweave_census.py` → `cweave_denominator.json` with the weave front gate
(full-body coverage + lock class + defconfig-built) applied fail-closed.

## Frozen denominator

**D = 40 weave-eligible** of 234 chain/composed-verified container fns
(22 T2 + 18 T3; locks: 24 none, 8 mutex, 7 spin, 1 spin_irq).

Full accounting (invariant 4): 234 = 40 eligible + 4 built-but-residual +
190 not-built-in-defconfig (of which 23 also residual). Residual leaders:
13 value-returns, 14 singleton statement shapes (counters, field writes).
Locks in the FULL population: 141 none / 42 mutex / 28 spin_irq / 23 spin.

## What "woven" must mean (the honest bar)

The emitted Rust replaces the WHOLE C body: list ops at probed offsets,
real `kfree` extern, real lock symbols (`mutex_lock`/`mutex_unlock`,
`_raw_spin_lock`/`_raw_spin_unlock`, `_raw_spin_lock_irqsave`/
`_raw_spin_unlock_irqrestore`) called in the original order, dual layout
guards (rustc `offset_of!` + in-tree `_Static_assert`). File-static heads and
locks are passed by the C seam (it has file scope). No partial-body weaves —
a fn that cannot be fully reproduced is DROPPED and tallied, not half-woven.

## Blind judgment (committed before the batch runs)

- **SUCCESS**: ≥ 24 (0.6×D) woven AND present in vmlinux (`nm`) AND
  boot-digest green AND the readers+realized base (64) still fully present.
- **PARTIAL**: 10–23 present, boot green.
- **FAILED**: < 10, or boot red, or any base fn lost.

Negative control (must run): one eligible fn woven with a deliberately
wrong `_Static_assert` offset — the kernel build MUST fail (guard is
load-bearing in-tree, not just in the gate).

Funnel-dropped fns are named in the report with their refusal reason; the
report states presence per lock-class (the lock-extern path is new machinery
and gets its own accounting).

---

## Addendum (list_empty class, 2026-08-07) — re-freeze: D unchanged, batch not run

After the list_empty class landed (+8 realized, 242 total), the weave front
gate re-ran with guard-aware coverage (list_empty + guard shells masked;
weave_containers now emits guarded bodies via the SAME parser the
differential gate proved, single-block guards so a flush cannot skip its own
unlock).

**Re-frozen D = 40 — unchanged.** The 8 new fns add ZERO defconfig
weave-eligibles, for named reasons: the 4 pop shapes return values (nonvoid
is weave-refused by design — no partial-body weaves), `__kthread_cancel_work`
returns bool, and the void guarded fns live in files no defconfig builds
(gfs2, cxgb4, bnxt). Pre-registered decision rule: a batch re-weaving an
IDENTICAL eligible set proves nothing and burns a boot — NOT RUN. Emitter
regression instead: all 40 existing artifacts emit unchanged (40/40), and
the guarded path is shape-verified (bnxt single-cond, cxgb4 loop-guard
lock+walk+unlock in one block). The guarded emission machinery is in place
for the first config that builds one of these files (config-coverage
campaign, Summit 2.3).
