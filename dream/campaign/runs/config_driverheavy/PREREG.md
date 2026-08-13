# PREREG — Config-coverage Volume 1 (cgir-kbuild-driverheavy)

Frozen 2026-08-13T14:23:17, BEFORE the run. Graded in REPORT.md.

**Frozen denominator:** 578 buildable-in-principle ineligible files / ~750 fns; 8 s390 files arch-locked (excluded)

## Blind bars

- **newly-eligible**: >=40 currently-ineligible realized fns have their file build =y in the new volume (measured by cweave_census built-map on the new WEAVE_VOL)
- **boot-present**: >=25 of the newly-eligible fns weave + boot-present in vmlinux (nm + boot digest green)
- **priors-intact**: all 44 defconfig-eligible priors still present; base boot green

## Required negative controls

- bad_offset sabotage MUST fail the kernel build (fail-closed)
- guard-drop control on ONE guarded weave (cxgb4/gfs2/bnxt) MUST be caught (build-fail or boot-probe reject) — FIRST in-kernel guard-aware fire; undetected green boot = gate hole, STOP and report

Two-partials rule armed: a second PARTIAL on config-coverage means re-plan (allmodconfig ceiling probe or accept the long-tail wall), not a third targeted volume.
