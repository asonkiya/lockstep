#!/usr/bin/env bash
# push_3080.sh — drive a borrowed GPU box (see setup_3080.sh) from the Mac.
# burst.sh discipline for a single ssh host: repo up via rsync (no .git, NO
# secrets), results back via rsync, box treated as disposable.
#
#   push_3080.sh up user@host          provision + push repo + start pass
#   push_3080.sh status user@host      GPU load, ladder log tail, progress
#   push_3080.sh collect user@host DIR rsync banked results into DIR
#   push_3080.sh stop user@host        kill the running pass (results keep)
#
# The pass = grind.sh burst-mode (no git on the box): overnight.py, boot-free
# phases only, resumable via progress.json — safe to re-run `up` after an
# interruption; completed work is skipped.
#
# The default pass is strictly $0: the ladder stops at the local model. To
# enable the budget-capped Haiku tail rung, add the key ON THE BOX yourself:
#   ssh user@host 'echo ANTHROPIC_API_KEY=sk-... >> ~/grind/.env'
# (this script never transports secrets).
#
# Collected candidates are BANKED, not yet trusted-merged: after collect, copy
# new files into dream/firstrun/verified/ only via the zero-trust re-verify
# pass on the Mac (same rule as grinder-results).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
PHASES="EFFTRACE=1 CONTAINERS=1 ALLOCMODEL=1 READERS=0 PHASE2=0"
LOCAL_MODEL=${LOCAL_MODEL:-qwen2.5-coder:14b}
BUDGET_CAP=${BUDGET_CAP:-2.0}

cmd=${1:?usage: push_3080.sh up|status|collect|stop user@host [...]}
BOX=${2:?usage: push_3080.sh $cmd user@host [...]}
SSH="ssh -o StrictHostKeyChecking=accept-new $BOX"

case "$cmd" in
up)
    echo "== provision =="
    $SSH 'bash -s' < "$HERE/setup_3080.sh"
    echo "== push repo =="
    rsync -az --delete -e "ssh -o StrictHostKeyChecking=accept-new" \
        --exclude .git --exclude 'dream/firstrun/verified' \
        --exclude 'dream/firstrun/progress.json' --exclude 'sweep' \
        "$REPO/" "$BOX:~/grind/lockstep/"
    echo "== env =="
    $SSH "mkdir -p ~/grind && { printf 'GRIND_PHASES=\"%s\"\n' '$PHASES';
          printf 'LOCAL_MODEL=%s\nBUDGET_CAP=%s\nRUNTIME_CAP_H=12\n' '$LOCAL_MODEL' '$BUDGET_CAP'; } \
          > ~/grind/.env && chmod 600 ~/grind/.env"
    echo "== start pass =="
    $SSH 'nohup bash ~/grind/lockstep/dream/infra/grinder/grind.sh \
          </dev/null >~/grind/pass.out 2>&1 & echo "pass started (pid $!)"'
    ;;
status)
    $SSH 'nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader 2>/dev/null;
          ollama ps 2>/dev/null;
          echo "--- run.log ---";
          tail -8 ~/grind/lockstep/dream/firstrun/run.log 2>/dev/null || echo "(no log yet)";
          echo "--- banked ---";
          ls ~/grind/lockstep/dream/firstrun/verified 2>/dev/null | wc -l'
    ;;
collect)
    DIR=${3:?usage: push_3080.sh collect user@host DIR}
    STAMP=$(date -u +%Y%m%dT%H%M%SZ)
    DEST="$DIR/3080-$STAMP"
    mkdir -p "$DEST"
    rsync -az -e "ssh -o StrictHostKeyChecking=accept-new" \
        "$BOX:~/grind/lockstep/dream/firstrun/verified" \
        "$BOX:~/grind/lockstep/dream/firstrun/REPORT.md" \
        "$BOX:~/grind/lockstep/dream/firstrun/progress.json" \
        "$BOX:~/grind/lockstep/dream/firstrun/run.log" \
        "$BOX:~/grind/pass.out" \
        "$DEST/" 2>/dev/null || true
    echo "collected -> $DEST ($(ls "$DEST/verified" 2>/dev/null | wc -l | tr -d ' ') banked candidates)"
    echo "next: zero-trust re-verify on the Mac before merging into dream/firstrun/verified/"
    ;;
stop)
    $SSH 'pkill -f "dream/firstrun/overnight.py" && echo stopped || echo "no pass running"'
    ;;
*)
    echo "unknown: $cmd" >&2; exit 2 ;;
esac
