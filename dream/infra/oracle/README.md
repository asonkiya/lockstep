# Oracle Always Free A1 — the always-on grinder

One arm64 KVM box, permanently free, grinding while every laptop is closed.
Account creation + instance launch are console steps only you can do; after
that, provisioning is one command.

## Your steps (once, ~15 min)

1. **Account**: sign up at cloud.oracle.com (free tier; a card is required for
   identity but Always Free resources never bill).
2. **Instance** (console → Compute → Create):
   - Shape: `VM.Standard.A1.Flex`, **4 OCPU / 24 GB** (the full free allotment
     as ONE box — one 4-core worker beats four 1-core ones for kernel builds).
   - Image: **Ubuntu 24.04 (aarch64)**.
   - Add your ssh public key; note the public IP.
   - Free-tier A1 capacity is regional roulette: if "out of capacity", retry
     off-peak hours or another availability domain.
3. **Boot volume**: bump to 100 GB (free tier includes 200 GB total block
   storage; the kernel tree + build wants the room).

## Provision (one command from the Mac)

```bash
ssh ubuntu@<IP> 'bash -s' < dream/infra/grinder/setup_grinder.sh
```

Then the two secrets the script cannot handle for you:

- **Deploy key** (results channel): on the box,
  `ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 && cat ~/.ssh/id_ed25519.pub`,
  add it as a **read-write deploy key** on github.com/asonkiya/lockstep, then
  re-run the setup script (it finishes the lockstep clone).
- **Haiku tail** (optional): `echo 'ANTHROPIC_API_KEY=...' > ~/grind/.env &&
  chmod 600 ~/grind/.env`. Use a low-limit throwaway key; without it the ladder
  still runs the $0 template+qwen rungs.

## What runs

`grind.timer` (systemd user timer, survives reboots via linger) fires
`grind.sh` every 30 min: pull main → run `overnight.py` (phases from
`GRIND_PHASES` in `~/grind/.env`, resumable, budget-capped) → push banked
results to the **grinder-results** branch. Collect on the Mac any time with
`git fetch origin grinder-results`.

## Health

```bash
ssh ubuntu@<IP> systemctl --user status grind.timer
ssh ubuntu@<IP> tail -20 grind/last-pass.txt
```
