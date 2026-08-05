# RUN-DEFCONFIG report — graded against PREREG-DEFCONFIG.md

Question: does a platform-true (arm64 defconfig) config lift present-in-vmlinux
woven readers past the minimal config's 16?

## Verdict: LEVER-DEAD (pre-registered rule: P < 12)

**P = 10** readers present in the woven defconfig vmlinux (nm-checked seam
symbols), boot-digest green (smp_up + boot_complete + no early panic). The
pre-committed SUCCESS bar (≥ 32) was already falsified by the frozen
denominator (D = 20) before the weave ran; the weave then delivered 10 of that
20 ceiling — fewer than minimal's 16. **Config coverage is NOT the lever for
this population.** Presence is config-relative: defconfig certifies a
different, smaller subset.

## Full accounting (104 verified readers; no silent drops)

| bucket | n | detail |
|---|---|---|
| present-in-vmlinux, boot green | **10** | resource_clip, bitmap_check_region, linear_range_get_value, wrap_area_index, bpf_vlog_update_len_max, step_forward, sh_pfc_enum_in_range, pwm_check_rounding, pwmwfcmp, _normalize_load |
| link-dropped (div-panic core refs) | 2 | bcm_qspi_calc_spbr, round_voltage |
| guard-dropped (LAYOUT DRIFT, see below) | 9 | iort.c, clk-versaclock5.c, clk-scu.c, clk-regmap-phy-mux.c, acpi-dma.c, input-poller.c, touch-overlay.c, sierra_ms.c, hugetlb.c |
| file not built under defconfig | 83 | driver hardware defconfig omits for arm64 |

vs minimal config: 16 present there, 10 here; lockdep.c's 2 (lock_time_add/inc)
are structurally impossible under defconfig (no CONFIG_LOCK_STAT). Union across
the two configs: 16 ∪ 10 → **20 distinct readers boot-verified in at least one
config** (6 of the 10 are re-certifications of minimal-config fns under a
second config; sh_pfc/pwm×2/_normalize_load/step_forward etc. overlap).

## The two headline findings

**1. Layout is config-dependent, and the in-tree guards caught it live.**
All sampled guard-drops are real `_Static_assert` failures against defconfig
headers: `struct hugepage_subpool` (112→? bytes; count offset 64→ moved),
`struct input_dev_poller` (200→?), `struct acpi_iort_smmu_v3` (56→?). Mirrors
verified under the minimal config REFUSED to weave into a kernel where
config-gated fields change the struct — exactly the fail-closed design (a
wrong layout fails the kernel build, never boots wrong). These 9 files are the
re-verify-under-defconfig worklist, not losses.

**2. Two gate holes found and closed (both defconfig-only, both fail-closed now).**
- *Stale-Image false pass*: the batch's link-repair loop didn't `rm` the old
  Image and had no pipefail, so a FAILED relink "passed" `test -f Image` and
  the boot gate booted a stale kernel with zero weave content. Caught by the
  nm presence check (0/12 present including kernel/resource.c — impossible);
  fixed (rm + pipefail). The boot gate alone would have been vacuous — the
  presence check is load-bearing.
- *Warning-line link matching*: defconfig's linker emits `.eh_frame`
  orphan-section warnings naming every reader object; the link-repair key
  match read those as failures and dropped all 12 readers (only 2 were bad).
  Fixed: keys match only non-warning lines.

## Where the presence lever actually is

The 104-reader population is ~80% exotic-driver files no arm64 config builds.
Growing in-vmlinux presence means (a) harvesting candidates FROM the config
that will run them (defconfig-targeted sweep), and/or (b) weaving the classes
whose files ARE built everywhere — which is the realize (model→real) campaign:
efftrace/container candidates live disproportionately in core files (block/,
kernel/, lib/, mm/).

Run cost: ~$0 model spend; one defconfig build (3m20s wall in docker on this
host — full `make mrproper` rebuild), 3 weave-batch iterations while closing
the gate holes, 4 boots.
