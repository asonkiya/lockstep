#!/usr/bin/env bash
# Scheduler: wait for the first-rewrite run to finish, THEN run the wide test.
# Launched detached now; polls for completion (firstrun writes dream/firstrun/
# REPORT.md at the very end, or its pid disappears), caps the wait at 8h, then
# runs the wide test at low priority. Survives terminal close (nohup).
set -uo pipefail
cd "$(dirname "$0")/../.."                         # -> lockstep repo root
export KSRC="${KSRC:-/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux}"
export PATH="$HOME/.cargo/bin:$PATH"

FRPID="${FRPID:-39307}"                            # the first-rewrite pid
REPORT="dream/firstrun/REPORT.md"                  # firstrun's completion signal
mkdir -p dream/widetest/reports
SLOG="dream/widetest/reports/schedule.log"; : > "$SLOG"
say(){ echo "[$(date '+%m-%d %H:%M:%S')] $*" | tee -a "$SLOG"; }

say "waiting for first-rewrite run (pid $FRPID, or $REPORT) — cap 8h"
for i in $(seq 1 480); do                          # 480 * 60s = 8h
  if [ -f "$REPORT" ]; then say "firstrun finished (REPORT.md present)"; break; fi
  if ! kill -0 "$FRPID" 2>/dev/null; then say "firstrun pid gone"; break; fi
  sleep 60
done
sleep 15                                            # let firstrun's phase-2 settle

say "launching WIDE test (nice -15)"
nice -n 15 python3 dream/widetest/widetest.py >> "$SLOG" 2>&1
rc=$?
say "wide test finished (exit $rc) — see dream/widetest/reports/REPORT.md"
