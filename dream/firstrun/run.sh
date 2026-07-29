#!/usr/bin/env bash
# First official minimal rewrite — one-command overnight launcher.
# Runs preflight checks, then launches the run DETACHED (nohup + nice) so you can
# close the terminal and get to work. Read dream/firstrun/REPORT.md in the morning.
#
#   ./dream/firstrun/run.sh              # preflight + launch
#   DRY_RUN=1 ./dream/firstrun/run.sh    # preflight only, don't launch
#   BUDGET_CAP=5 N_LEAVES=120 ./dream/firstrun/run.sh   # override any default
set -uo pipefail
cd "$(dirname "$0")/../.."                       # -> lockstep repo root

export KSRC="${KSRC:-/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux}"
ENVFILE="/Users/aryaman/Documents/Programming/llm-semantic-compilers/.env"
LOG="dream/firstrun/run.log"; REPORT="dream/firstrun/REPORT.md"

echo "== preflight =="
fail=0

if [ -d "$KSRC" ]; then echo "  ok   KSRC: $KSRC"
else echo "  FAIL KSRC missing: $KSRC   (re-run with KSRC=/path/to/linux)"; fail=1; fi

if grep -q ANTHROPIC_API_KEY "$ENVFILE" 2>/dev/null; then echo "  ok   API key present (Haiku tail enabled, capped)"
else echo "  warn API key not found -> Haiku tail disabled (local/template only)"; fi

if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then echo "  ok   ollama up (local Qwen = \$0 rung)"
else echo "  warn ollama NOT running -> all synth falls to capped Haiku (start it for more per \$)"; fi

if docker ps >/dev/null 2>&1; then echo "  ok   docker up (Phase 2 boot enabled)"
else echo "  warn docker NOT running -> Phase 2 boot-weave will be skipped"; fi

if command -v rustc >/dev/null 2>&1 && command -v cc >/dev/null 2>&1; then echo "  ok   cc + rustc"
else echo "  FAIL cc/rustc missing (host gate needs them)"; fail=1; fi

if [ "$fail" = 1 ]; then echo "preflight FAILED — fix the FAIL lines above."; exit 1; fi

if [ "${DRY_RUN:-0}" = 1 ]; then echo "dry run: preflight passed, not launching."; exit 0; fi

echo "== launching (budget cap \$${BUDGET_CAP:-7.5}, runtime cap ${RUNTIME_CAP_H:-7}h, nice -15, ${WORKERS:-4} workers) =="
: > "$LOG"; rm -f "$REPORT"
nohup nice -n 15 python3 dream/firstrun/overnight.py > dream/firstrun/nohup.out 2>&1 &
pid=$!
echo "  started, pid $pid"
echo "  watch:    tail -f $LOG"
echo "  morning:  cat $REPORT"
echo "  stop:     kill $pid    (Phase-1 results are checkpointed; a re-run resumes)"
echo "done — safe to close this terminal."
