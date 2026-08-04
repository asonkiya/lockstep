#!/usr/bin/env bash
# run2.sh — Run 2 (PREREG-RUN2.md) as resumable steps. Frozen surface per
# amendment 2: any mid-run edit to this file invalidates the affected step.
#
#   run2.sh prep        cold-start archive + freeze BOTH denominators + 9/9 controls
#   run2.sh readers|containers|efftrace|alloc|leaves|boot|status
#
# Per-step Haiku caps sum $4.50 under the $5 envelope.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
PY=${PY:-/Users/aryaman/Documents/Programming/llm-semantic-compilers/.venv/bin/python3}
export KSRC=${KSRC:-/Users/aryaman/.claude/jobs/8a8bcefc/tmp/linux}
R2=$HERE/run2
mkdir -p "$R2"
STEP=${1:?usage: run2.sh prep|readers|containers|efftrace|alloc|leaves|boot|status}

CONTROLS="test_over_credit_diverges or test_or_for_add_mistranslation_diverges \
or test_sabotages_diverge or test_wrong_member_diverges \
or test_no_init_over_credit_diverges or test_double_alloc_diverges_on_ret \
or test_linear_range_close_and_sabotage or test_subpool_is_free_close_and_sabotage"

phase() {
    local name=$1; shift
    [ -f "$R2/controls-ok" ] || { echo "run 'run2.sh prep' first (9/9 controls)"; exit 2; }
    echo "== run2 step $name ($(date -u +%H:%M:%SZ)) — rerun this step if interrupted =="
    ( cd "$REPO" && env "$@" RUNTIME_CAP_H=12 \
        "$PY" dream/firstrun/overnight.py ) 2>&1 | tee -a "$R2/$name.log"
    echo "== step $name done; checkpoint: $(ls "$HERE/verified" 2>/dev/null | wc -l | tr -d ' ') verified total =="
}

case "$STEP" in
prep)
    cd "$REPO"
    echo "HEAD $(git rev-parse --short HEAD)" | tee "$R2/freeze.txt"
    git diff --quiet dream/ || { echo "DIRTY dream/ tree — commit before launch"; exit 2; }
    echo "-- cold start: archiving Run-1 checkpoint (amendment 3) --"
    A=$HERE/run1-archive; mkdir -p "$A"
    for f in progress.json REPORT.md run.log; do
        [ -f "$HERE/$f" ] && mv "$HERE/$f" "$A/" && echo "  archived $f"; done
    [ -d "$HERE/verified" ] && mv "$HERE/verified" "$A/verified" && echo "  archived verified/"
    echo "-- freezing scoped-leaf denominator (amendment 4) --"
    "$PY" -c "
import sys, os, json
for d in ('widerun', 'hostdiff', 'cluster'): sys.path.insert(0, 'dream/' + d)
import widerun, hostdiff, purity
KSRC = os.environ['KSRC']
scoped = []
for w in widerun.harvest():
    if purity.classify(w['body'], set())[0] != 'pure': continue
    if not hostdiff.tu_compiles(w['file'], KSRC, w['sym'])[0]: continue
    scoped.append(w['sym'])
json.dump(scoped, open('$R2/run2_leaves.json', 'w'))
print(f'leaves denominator FROZEN: {len(scoped)} -> {scoped}')"
    echo "-- freezing readers denominator (amendment 5: prepare-passing subset) --"
    "$PY" -c "
import sys, json
for d in ('structdiff', 'mirror', 'cluster'): sys.path.insert(0, 'dream/' + d)
import harness as sd
wl = json.load(open('dream/structdiff/reach_accepted.json'))
ok = []
for it in wl:
    try:
        sd.prepare(it['file'], it['fn']); ok.append({'file': it['file'], 'fn': it['fn']})
    except Exception:
        pass
json.dump(ok, open('$R2/run2_readers.json', 'w'), indent=0)
print(f'readers denominator FROZEN: {len(ok)}/{len(wl)} prepare-passing')"
    echo "-- negative controls: 8 named + readers-sabotage (amendment 6) --"
    "$PY" -m pytest dream/tests/ -q -k "$CONTROLS" 2>&1 | tee "$R2/controls.txt"
    "$PY" -c "
import sys, json, tempfile
for d in ('structdiff', 'mirror', 'cluster'): sys.path.insert(0, 'dream/' + d)
import harness as sd
wl = {it['fn']: it['file'] for it in json.load(open('dream/structdiff/reach_accepted.json'))}
p = sd.prepare(wl['bitmap_check_region'], 'bitmap_check_region')
with tempfile.TemporaryDirectory() as d:
    v, _ = sd.close(wl['bitmap_check_region'], 'bitmap_check_region',
                    p['mirror_rust'] + '\n' + p['sig'] + ' { unsafe { core::mem::zeroed() } }', d)
assert v == 'DIVERGE', f'readers control NOT rejected: {v}'
print('READERS_CONTROL: DIVERGE (rejected) — 9/9')" 2>&1 | tee -a "$R2/controls.txt"
    grep -qE "^8 passed" "$R2/controls.txt" && grep -q "9/9" "$R2/controls.txt" \
        && { touch "$R2/controls-ok"; echo "CONTROLS 9/9 — cleared to run"; } \
        || { echo "CONTROLS NOT 9/9 — NOT cleared (invariant 2)"; exit 1; }
    ;;
readers)    phase readers    READERS=1    PHASE2=0 BUDGET_CAP=0.50 N_LEAVES=0 ;;
containers) phase containers CONTAINERS=1 PHASE2=0 BUDGET_CAP=0.50 N_LEAVES=0 ;;
efftrace)   phase efftrace   EFFTRACE=1   PHASE2=0 BUDGET_CAP=0.50 N_LEAVES=0 ;;
alloc)      phase alloc      ALLOCMODEL=1 PHASE2=0 BUDGET_CAP=0.50 N_LEAVES=0 ;;
leaves)     phase leaves     N_LEAVES=200 PHASE2=0 BUDGET_CAP=2.50 ;;
boot)       phase boot       PHASE2=1 N_LEAVES=0 BUDGET_CAP=0.05 ;;
status)
    echo "verified: $(ls "$HERE/verified" 2>/dev/null | wc -l | tr -d ' ') candidates"
    "$PY" -c "
import json, os
p = '$HERE/progress.json'
print('done:', len(json.load(open(p))['done']) if os.path.exists(p) else 0)"
    grep -ho '\$[0-9.]* spent' "$R2"/*.log 2>/dev/null | tail -8 || true
    ;;
*) echo "unknown step: $STEP"; exit 2 ;;
esac
