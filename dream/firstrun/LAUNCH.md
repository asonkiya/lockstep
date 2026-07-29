# First official minimal rewrite — how to run it overnight

Unattended, guarded, machine-friendly. Kick it off before bed; read `REPORT.md`
in the morning.

## Prerequisites (verify before launching)

- **API balance** ~$7.75 (no top-up needed). The run caps Haiku at **$7.5**.
- **ollama running** with `qwen2.5-coder:7b` (the $0 local rung). Check: `ollama ps`.
  If the ollama app isn't running, local synth fails and everything falls to Haiku
  (still capped — just less gets done for the money).
- **Docker running** (only needed for Phase 2's single boot).
- **KSRC points at a persistent kernel checkout** — the default
  `/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux` must still exist overnight.
  If unsure, set `KSRC=` to a stable kernel source path in the command below.

## Launch (run this, then go to bed)

```bash
cd /Users/aryaman/Documents/Programming/lockstep
KSRC=/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux \
  nohup nice -n 15 python3 dream/firstrun/overnight.py \
  > dream/firstrun/nohup.out 2>&1 &
echo "started pid $!"
```

`nice -n 15` + 4 of 12 cores → the machine stays usable if you're on it; it won't
lock you out.

## Defaults (override with env vars before the command)

| var | default | meaning |
|---|---|---|
| `BUDGET_CAP` | `7.5` | hard $ ceiling on Haiku; skipped once hit (never exceeded) |
| `RUNTIME_CAP_H` | `7` | wall-clock ceiling, then graceful stop + report |
| `WORKERS` | `4` | concurrent synth workers (of 12 cores) |
| `N_LEAVES` | `80` | scalar exported leaves to attempt |
| `PHASE2` | `1` | set `0` to skip the boot-weave (Phase 1 only) |

## In the morning

```bash
cat dream/firstrun/REPORT.md      # verified count, $ spent, boot verdict
tail -40 dream/firstrun/run.log   # timeline
```

## Controls

- **Progress / live**: `tail -f dream/firstrun/run.log`
- **Stop early**: `pkill -f dream/firstrun/overnight.py` (Phase-1 results already
  banked in `verified/` + `progress.json`; a re-launch resumes).
- **Resume**: just re-run the launch command — completed functions are skipped.

## What it does (recap)

- **Phase 1 (boot-free, the bulk):** GPIO template family ($0, deterministic) +
  scalar leaves via local Qwen ($0) → Haiku tail (capped), each gated by the
  boot-free host differential. Verified candidates saved to `verified/`.
- **Phase 2 (one boot):** weave the verified freestanding leaves into vmlinux and
  boot-verify → a booting kernel carrying the Rust set. Best-effort and guarded —
  a Phase-2 hiccup cannot lose Phase-1 results.

Sound by construction: every candidate faces a gate with zero false passes across
all prior runs; a cheaper synthesizer only costs retries, never correctness.
