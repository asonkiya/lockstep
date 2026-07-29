#!/bin/bash
# The overnight suite — a sequence of SAFE, unattended jobs, each writing a
# report, consolidated into reports/SUMMARY.md for the morning. No kernel boots,
# no API calls (the synth grind uses the LOCAL model only) => $0, no hang path,
# safe to leave running. One job failing never aborts the suite.
#
#   1. recorder census        whole-tree coverage + soundness (fast)
#   2. analyze                refusal taxonomy (next-increment backlog) + tree census
#   3. soundness megatest     thousands of adversarial candidates at both oracles
#   4. synth grind            REAL progress: verify pure leaves at $0 (c2rust + local qwen)
#   5. consolidate            SUMMARY.md
#
# Usage: bash dream/overnight/run_suite.sh   (run detached: nohup ... &)
set -uo pipefail
export KSRC="${KSRC:-/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux}"
export PATH="$HOME/.cargo/bin:$PATH"
G="$(cd "$(dirname "$0")" && pwd)"
OUT="$G/reports"; mkdir -p "$OUT"
LOG="$OUT/suite.log"; : > "$LOG"
log(){ echo "[$(date '+%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

run(){  # name script args...
  local name="$1"; shift
  log "JOB START: $name"
  local t=$(date +%s)
  if python3 -u "$@" >>"$OUT/$name.out" 2>&1; then
    log "JOB OK: $name ($(( $(date +%s) - t ))s)"
  else
    log "JOB FAILED (rc=$?): $name ($(( $(date +%s) - t ))s) — continuing"
  fi
}

log "=== OVERNIGHT SUITE START ==="
log "KSRC=$KSRC"

run recorder_census  "$G/../mmiogen/overnight_sweep.py" --root . --out "$OUT/recorder_census"
run analyze          "$G/analyze.py" --census "$OUT/recorder_census/sweep.jsonl" --out "$OUT/analysis"
run soundness_megatest "$G/soundness_megatest.py" --census "$OUT/recorder_census/sweep.jsonl" \
                       --mutants-per 300 --max-pure 400 --out "$OUT/soundness_megatest"
run synth_grind      "$G/synth_grind.py" --out "$OUT/synth_grind"

log "consolidating..."
python3 "$G/consolidate.py" > "$OUT/SUMMARY.md" 2>>"$LOG" && log "SUMMARY.md written"
log "=== OVERNIGHT SUITE DONE ==="
