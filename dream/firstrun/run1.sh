#!/usr/bin/env bash
# run1.sh — Run 1 (PREREG-RUN1.md) as RESUMABLE STEPS. Each step is its own
# overnight.py invocation; progress.json + verified/ checkpoint everything, so
# an interruption costs only the in-flight function — rerun the same step and
# completed work is skipped. Steps run one at a time (they share the
# checkpoint files; never run two concurrently).
#
#   run1.sh prep        freeze harvest + run the 8 negative controls (MUST
#                       pass 8/8 before any phase step)
#   run1.sh readers     phase 1B  (structdiff,   n=78)
#   run1.sh containers  phase 1B2 (container-ADT, n=83)
#   run1.sh efftrace    phase 1B3 (effect-trace, n=110)
#   run1.sh alloc       phase 1B4 (alloc-init,   n=23)
#   run1.sh leaves      phase 1C  (scalar leaves, frozen first 200)
#   run1.sh boot        phase 2   (batched weave + boot)
#   run1.sh status      checkpoint counts + spend so far
#
# Per-step Haiku caps sum under the $5 pre-reg envelope (spent() is
# per-process): 4 oracle steps x $0.50 + leaves $2.50 + boot $0 = $4.50.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
PY=${PY:-/Users/aryaman/Documents/Programming/llm-semantic-compilers/.venv/bin/python3}
export KSRC=${KSRC:-/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux}
R1=$HERE/run1
mkdir -p "$R1"
STEP=${1:?usage: run1.sh prep|readers|containers|efftrace|alloc|leaves|boot|status}

# controls: 2 sabotage-rejection tests per oracle (PREREG invariant 2)
CONTROLS="test_over_credit_diverges or test_or_for_add_mistranslation_diverges \
or test_sabotages_diverge or test_wrong_member_diverges \
or test_no_init_over_credit_diverges or test_double_alloc_diverges_on_ret \
or test_linear_range_close_and_sabotage or test_subpool_is_free_close_and_sabotage"

phase() {  # phase <name> <env...>
    local name=$1; shift
    [ -f "$R1/controls-ok" ] || { echo "run 'run1.sh prep' first (8/8 controls)"; exit 2; }
    echo "== step $name ($(date -u +%H:%M:%SZ)) — resumable; rerun this step if interrupted =="
    ( cd "$REPO" && env "$@" RUNTIME_CAP_H=12 \
        "$PY" dream/firstrun/overnight.py ) 2>&1 | tee -a "$R1/$name.log"
    echo "== step $name done; checkpoint: $(ls "$HERE/verified" 2>/dev/null | wc -l | tr -d ' ') verified total =="
}

case "$STEP" in
prep)
    cd "$REPO"
    echo "HEAD $(git rev-parse --short HEAD)" | tee "$R1/freeze.txt"
    git diff --quiet dream/ || { echo "DIRTY dream/ tree — commit before launch"; exit 2; }
    echo "-- freezing scalar harvest --"
    "$PY" -c "
import sys, os, json
sys.path.insert(0, 'dream/widerun'); sys.path.insert(0, 'dream/cluster')
import widerun
w = widerun.harvest()
json.dump([x['sym'] for x in w], open('$R1/run1_harvest.json', 'w'), indent=0)
print(f'harvest frozen: {len(w)} leaves -> denominator = first 200')"
    echo "-- negative controls (8 named sabotage rejections) --"
    "$PY" -m pytest dream/tests/ -q -k "$CONTROLS" 2>&1 | tee "$R1/controls.txt"
    grep -qE "^8 passed" "$R1/controls.txt" \
        && { touch "$R1/controls-ok"; echo "CONTROLS 8/8 — cleared to run"; } \
        || { echo "CONTROLS NOT 8/8 — run is NOT cleared (invariant 2)"; exit 1; }
    ;;
readers)    phase readers    READERS=1    PHASE2=0 BUDGET_CAP=0.50 ;;
containers) phase containers CONTAINERS=1 PHASE2=0 BUDGET_CAP=0.50 ;;
efftrace)   phase efftrace   EFFTRACE=1   PHASE2=0 BUDGET_CAP=0.50 ;;
alloc)      phase alloc      ALLOCMODEL=1 PHASE2=0 BUDGET_CAP=0.50 ;;
leaves)     phase leaves     N_LEAVES=200 PHASE2=0 BUDGET_CAP=2.50 ;;
boot)       phase boot       PHASE2=1 N_LEAVES=0 BUDGET_CAP=0.05 ;;
status)
    echo "verified: $(ls "$HERE/verified" 2>/dev/null | wc -l | tr -d ' ') candidates"
    "$PY" -c "
import json, os
p = '$HERE/progress.json'
print('done:', len(json.load(open(p))['done']) if os.path.exists(p) else 0)"
    grep -ho '\$[0-9.]* spent' "$R1"/*.log 2>/dev/null | tail -8 || true
    ;;
*) echo "unknown step: $STEP"; exit 2 ;;
esac
