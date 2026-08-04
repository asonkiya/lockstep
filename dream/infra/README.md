# infra — the three wall-clock levers (COSTDOWN §1–§5, built)

The 1–3-month single-laptop wall-clock collapses to days via three legs, in
order of build:

## 1. `oracle/` + `grinder/` — the always-on $0 grinder

One Oracle Always Free A1 box (4 OCPU / 24 GB arm64, real KVM) grinding while
every laptop is closed. `oracle/README.md` has the console steps (account +
instance are user-only steps); then provisioning is one command
(`grinder/setup_grinder.sh` over ssh). `grinder/grind.sh` runs on a 30-min
systemd timer: pull main → `overnight.py` pass (env-phase flags, resumable,
budget-capped) → push banked results to the **grinder-results** branch.

## 2. `hvf/` — kill the TCG double penalty locally

`boot_hvf.sh [--build]`: build in the kbuild container (unchanged), boot the
Image **natively on macOS under Hypervisor.framework**.

**Measured 2026-08-04, same Image booted to panic:** TCG-in-Docker **216.3 s**
→ HVF **5.3 s** — **41×** on the boot leg. The ccache'd incremental build is
now the bottleneck. Gates adopt it by replacing their in-container qemu step
with this script (follow-on wiring).

## 3. `hetzner/` — burst a fleet for a weekend

`burst.sh up N [cax31|cax41]` rents N arm64 KVM boxes hourly (~€0.02/hr each),
provisions them with the same grinder kit (repo via rsync — no secrets on
ephemeral boxes), shards work by phase; `status` / `collect DIR` / `down`.
4 boxes × a weekend ≈ **€5–8 total**. Work shards properly: every box runs all
phases with `GRIND_SHARD=k`/`GRIND_OF=N`; `firstrun/shardlib.py` slices each
raw worklist modulo — disjoint and exhaustive by construction
(`dream/tests/test_shard.py`).

## Secrets discipline

No credentials are ever written by these scripts: the Oracle deploy key and
`ANTHROPIC_API_KEY` are manual one-liners documented in `oracle/README.md`;
`burst.sh --with-haiku` copies the key from the gitignored sibling `.env` only
on explicit flag. Burst results return via rsync, the grinder's via a
results-only branch — `main` never carries generated data.
