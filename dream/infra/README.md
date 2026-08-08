# infra — the wall-clock levers (COSTDOWN §1–§5, built)

The 1–3-month single-laptop wall-clock collapses to days via these legs, in
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

## 4. `gpu3080/` — borrowed-GPU big pass (the $0 synth rung at speed)

`push_3080.sh up user@host` turns a temporarily-borrowed x86_64 + NVIDIA box
(RTX 3080 class) into a one-shot boot-free harvest worker: provisions via
`setup_3080.sh` (ollama + qwen2.5-coder:14b on GPU, rustup, kernel source —
no kernel builds, no docker), pushes the repo via rsync, and runs the
`overnight.py` ladder with the local model doing the bulk. localbench measured
14b at 62.5% first-pass (~85% of Haiku) at $0; the 3080 removes the wall-clock
penalty that made the 14b rung impractical on the M2. Soundness: an x86 host
gates for an arm64 kernel target, so setup installs a `cc` shim pinning
`-funsigned-char` (arm64 plain-char semantics) for every gate compile.
`status` / `collect DIR` / `stop`. This script never transports secrets; the
Haiku tail rung activates only if the user appends the key to `~/grind/.env`
on the box. Collected candidates re-enter the bank only through zero-trust
re-verify on the Mac.

## Secrets discipline

No credentials are ever written by these scripts: the Oracle deploy key and
`ANTHROPIC_API_KEY` are manual one-liners documented in `oracle/README.md`;
`burst.sh --with-haiku` copies the key from the gitignored sibling `.env` only
on explicit flag. Burst results return via rsync, the grinder's via a
results-only branch — `main` never carries generated data.
