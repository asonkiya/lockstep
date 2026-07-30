#!/usr/bin/env bash
# Chain: wait for the WIDE TEST to finish, and ONLY if it's clean (0 false passes,
# regression PASS) launch the ambitious $7 rewrite run. Soundness gate: don't spend
# $7 on infrastructure the wide test just flagged. Detached (nohup); survives close.
set -uo pipefail
cd "$(dirname "$0")/../.."                          # -> lockstep repo root
export KSRC="${KSRC:-/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux}"
# NB: do NOT prepend /opt/homebrew/bin — it shadows the .venv python3 (which has
# `anthropic`) with Homebrew's (which doesn't), silently killing the Haiku rung.
# The inherited PATH already has the venv python + rustc via ~/.cargo/bin.
export PATH="$HOME/.cargo/bin:$PATH"

WT="dream/widetest/reports/REPORT.md"; WTPID="${WTPID:-59006}"
AMB="dream/firstrun/ambitious"; mkdir -p "$AMB"
CLOG="$AMB/chain.log"; : > "$CLOG"
say(){ echo "[$(date '+%m-%d %H:%M:%S')] $*" | tee -a "$CLOG"; }

say "waiting for wide test (pid $WTPID / $WT) — cap 3h"
for i in $(seq 1 180); do                            # 180 * 60s = 3h
  [ -f "$WT" ] && { say "wide-test report present"; break; }
  kill -0 "$WTPID" 2>/dev/null || { say "wide-test pid gone"; break; }
  sleep 60
done
sleep 5

if grep -q "## VERDICT: PASS" "$WT" 2>/dev/null; then
  say "wide test CLEAN (VERDICT: PASS) -> launching ambitious run (\$7 cap, whole-tree)"
  nohup nice -n 15 python3 dream/firstrun/ambitious.py >> "$AMB/nohup.out" 2>&1 &
  say "ambitious launched, pid $!"
else
  say "SOUNDNESS GATE TRIPPED: wide test not clean (or no report) -> NOT launching ambitious."
  say "  inspect $WT ; to run manually once satisfied: python3 dream/firstrun/ambitious.py"
fi
