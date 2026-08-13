# Config-coverage campaign — Phase A measurement (2026-08-13)

The presence lever, measured before any heavy build. `config_gap.json` is the
data; `dream/ratchet/configgap*` in scratchpad is the method.

## The gap

Over the full realized set (**921 realized fns** — containers via
`cweave_census.verified_pairs` + efftrace census MATCH — across 701 files):

| | files | realized fns |
|---|---|---|
| ELIGIBLE (built in defconfig today) | 115 | 160 |
| INELIGIBLE | 586 | **761** |
| — of which ARCH-LOCKED (s390 etc., can NEVER build on arm64) | 8 | — |
| — buildable-in-principle on arm64 | 578 | ~750 |

## The finding: a long tail, no fat head

There is **no single config that unlocks hundreds of fns.** The ineligible
mass sits under broad *subsystem* descent-gates (ETHERNET 75 files, DVB_CORE
42, HID_SUPPORT 24, MTD 18, WLAN 18, INFINIBAND 15, …) but each specific
driver file has its own *leaf* config, so the resolvable leaf unlocks are all
single-to-low-double digits (top: `MTD_SPI_NAND` 28, then <10 each across
*hundreds* of unrelated driver configs). The plan's "driver-heavy volume"
intuition is only half right: one hand-picked config set gives **tens**, not
hundreds.

Consequence: the only *one-recipe* multiplier is **allmodconfig** (enable
every tristate at once → ~578 files compile). But allmodconfig builds them as
**=m modules** → the `.o` exists (weave-ELIGIBILITY) but the fn is NOT in
vmlinux (no BOOT-PRESENCE, the funnel's `present` metric). Boot-presence needs
**=y**.

## Recommendation (two honest options; heavy build is the coordinator's call)

- **Volume 1 — `cgir-kbuild-driverheavy` (RECOMMENDED first, boot-present):**
  defconfig + force **=y** the leaf configs of the top co-buildable ineligible
  files (MTD_SPI_NAND, RC_CORE, USBIP_CORE, ZCRYPT excluded-arch, MLXSW_CORE,
  MWIFIEX, and the mid-tail). `make olddefconfig` will drop the ones with
  unsatisfiable deps (report which). Predicted boot-eligible unlock after
  dropouts: **40–120 fns**. Incremental build on the warm defconfig tree:
  **~30–90 min**.
- **Volume 2 — `cgir-kbuild-allmod` (eligibility CEILING probe, optional):**
  allmodconfig. Measures the weave-eligibility ceiling (~578 files / ~750 fns
  `.o`-buildable) and is a substrate for per-module weave, but =m ≠ present.
  Heavy: **~1–3 h**, large volume (~15–25 GB). Do only if we want the ceiling
  number and module-load boot-presence later.

## Guard-aware first-fire: IN SCOPE

cxgb4 (4 files), gfs2 (2), bnxt (2) are all in the ineligible set — the exact
guarded-container files that were ORPHAN/refused in the 2026-08-09 batch.
Enabling their configs (CHELSIO_T4, GFS2_FS, BNXT) in Volume 1 makes this the
**first in-kernel use of guard-aware emission**. The PREREG below requires the
guard-drop negative control to be CAUGHT; an undetected green boot is a gate
hole → stop and report, do not paper over.
