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
