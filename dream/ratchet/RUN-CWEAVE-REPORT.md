# Containers weave — graded report (PREREG-CWEAVE.md)

**Verdict: SUCCESS** (blind bar: ≥24 of D=40 containers present + boot green
+ base intact — actual: **40/40 present**, boot-digest green, 54 realized +
10 readers base fully present).

Headline: **104 Rust functions in one booting defconfig kernel**
(40 containers + 54 efftrace-realized + 10 readers; 38 tier-b machine-checked
safe cores). The woven containers replace WHOLE bodies: list surgery at
probed offsets, real `kfree`, real lock symbols (mutex_lock/_raw_spin_lock*
in original order), file-statics passed by the C seam, dual guards.

Funnel accounting (invariant 4): D=40 → 40 parsed → 40 artifacts → 40 woven
→ 40 present. Zero drops. Negative control (pre-registered): sabotaged
in-tree `_Static_assert` offset failed the kernel build for syscore.c —
the guard is load-bearing where it matters (the kernel's own compiler).

Two defects found and fixed by batch attempt 1's failure (both committed):
- containers extern block lacked forward struct decls → -Werror
  incompatible-pointer-type in files defining the struct late
  (stop_machine.c);
- latent reset-scope bug in the batch rounds: a file dropped in round N
  stayed woven (broken seam) in rounds N+1+ because _reset_stock only
  covered current survivors — never exercised before (all prior drops were
  pre-apply). Reset now covers the union of everything ever woven.

Lock-class accounting for the 40 present: 24 none, 8 mutex, 7 spin
(_raw_spin_lock/_raw_spin_unlock), 1 spin_irq (irqsave symbol layer).

---

## Addendum (post-repair re-freeze batch, 2026-08-09)

**Verdict: SUCCESS** against the blind bars frozen in PREREG-CWEAVE.md
(2026-08-09 addendum) BEFORE the batch: boot-digest green ✓; **43/45
containers non-vacuously present** (bar: ≥42) ✓; 3 of the 4
non-vacuous-predicted new fns present (pinctrl_add_gpio_range,
padata_work_free, add_tail — bar: ≥3) ✓; **all 40 prior containers
present** ✓; bad_offset negctl FAILED the kernel build (syscore.c,
fail-closed) ✓; zero vacuous weaves ✓.

Headline: **107 Rust functions in one booting defconfig kernel**
(43 containers + 54 efftrace-realized + 10 readers; tier-b 38 = 31 realized
+ 7 lifted readers). Funnel: D 40 → 45 at freeze (+5 from the conditional
classes + banked-model repair) → 44 after the ORPHAN finding (below) → 43
woven (net_unlink_todo probe-refused by name) → 43 present.

### Three machinery defects found by this slice (all fixed in-slice)

1. **Batched probe make without `-k`** — net_unlink_todo's
   CONFIG_LOCKDEP-only field failed dev.c's probe TU, which aborted the
   whole `-j` make and collaterally sank OTHER files' probes: batch attempt
   1 lost net_set_todo and unix_free_vertices (both previously
   boot-verified). Fixed: `-k`, plus per-field salvage retries, plus
   per-field (not all-or-nothing) layout assembly — a missing field now
   skips only the fns that NEED it.
2. **`.o existence ≠ linked into vmlinux`** — our own probe/census passes
   force-build orphan objects, so cxgb4_mps.o existed with CONFIG_CHELSIO_T4
   entirely unset; batch attempt 1 wove it and the nm gate correctly caught
   the _rs absent ("not-linked"). Census now checks the .o's first defined
   global against vmlinux: cxgb4_free_mps_ref_entries → **ORPHAN**, D 45→44
   (disclosed mid-run; bars stayed as frozen and were graded at 45).
   Symbol-level probing alone was tried first and REJECTED: it would have
   dropped 21 boot-verified inlined statics — eligibility stays file-level,
   with a new batch-time **seam-reference check** (a linked _rs never
   referenced by its .o = vacuous weave, counted absent).
3. **flip_guard was a no-op on the loop-guard emission shape** — measured
   cxgb4 flip → MATCH before the fix: the sabotage never reached the
   early-return-before-walk wrapper, so the guard negative control was
   vacuous. Fixed; re-measured: clean → MATCH, drop_guard → MATCH
   (pre-registered equivalence — the guard is a skip-lock-on-empty
   optimization), **flip_guard → DIVERGE**.

### Prediction vs outcome

Pre-registered: net_unlink_todo would weave VACUOUSLY (CONFIG_LOCKDEP) and
be caught by the seam-reference check. Actual: it was caught one stage
EARLIER — the per-field probe honestly refused
(`probe_failed:net_device.unlink_list`), so it was never woven. The
seam-reference check remains armed as the belt for any fn whose field
survives probing but whose body is compiled out.

### Guard-aware path status

NO guarded weave shipped: the only two guarded eligibles are cxgb4 (ORPHAN
— not in this config's vmlinux) and net_unlink_todo (probe-refused). The
guard-aware in-kernel first use still awaits a config that links one
(config-coverage campaign, Summit 2.3); its gate-level controls are
measured above. The boot leg proves presence/ABI/liveness, not behavior —
behavioral soundness rides on the gate differential (standing limit).

### Runs

Batch attempt 1 (pre-fix): 42 woven / 41 present, 2 collateral probe drops,
cxgb4 not-linked — triggered the three fixes. Attempt 2: 43/43 but
LIFT_READERS unset (operator omission) → tier-b readers 0. Attempt 3
(final): LIFT_READERS=1, 43/43 present, tier-b 38, boot green. Container
suites 64/64 green after the flip fix. $0 model spend.
