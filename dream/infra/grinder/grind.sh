#!/usr/bin/env bash
# grind.sh — one grind pass. Invoked by grind.timer every 30 min; exits fast if
# a pass is already running. Laptop-independent by design: work definition
# arrives via git (main), results leave via git (grinder-results branch).
#
# The pass runs dream/firstrun/overnight.py exactly as it exists: env-flag
# phases, self-harvested worklists from $KSRC, resumable via progress.json (a
# re-run skips completed work, so the 30-min timer is safe). Which phases run
# on this box comes from ~/grind/.env, e.g.:
#   GRIND_PHASES="EFFTRACE=1 CONTAINERS=1 PHASE2=0"
#   BUDGET_CAP=2.0            # Haiku ceiling for THIS box; $0 rungs unaffected
#   ANTHROPIC_API_KEY=...     # optional; without it the ladder stops at qwen
set -euo pipefail

GRIND=~/grind
REPO=$GRIND/lockstep
LOCK=$GRIND/.grind.lock
exec 9>"$LOCK"; flock -n 9 || { echo "pass already running"; exit 0; }

[ -f "$GRIND/.env" ] && set -a && source "$GRIND/.env" && set +a
export KSRC=$GRIND/linux
export PATH="$HOME/.cargo/bin:/usr/lib/ccache:$PATH"
export WORKERS=${WORKERS:-$(( $(nproc) > 2 ? $(nproc) - 1 : 1 ))}
export RUNTIME_CAP_H=${RUNTIME_CAP_H:-6}

# Burst boxes get the repo via rsync (no .git) — git sync/publish only applies
# to the always-on grinder, which clones with a deploy key.
cd "$REPO"
HAS_GIT=0; [ -d .git ] && HAS_GIT=1
if [ "$HAS_GIT" = 1 ]; then
    git fetch -q origin
    git checkout -q main && git reset -q --hard origin/main
fi

# progress.json/verified/ live in dream/firstrun/ and persist across passes
# (they are gitignored, so the reset above does not clobber them).
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
env ${GRIND_PHASES:-EFFTRACE=1 CONTAINERS=1 PHASE2=0} \
    python3 dream/firstrun/overnight.py 2>&1 | tail -50 > "$GRIND/last-pass.txt" || true

# Publish the banked results on the grinder-results branch (never main).
# Burst boxes skip this — burst.sh collect rsyncs their results back instead.
[ "$HAS_GIT" = 1 ] || { echo "pass done (no git — collect via burst.sh)"; exit 0; }
git checkout -q -B grinder-results origin/main
DEST=grinder-results/$(hostname)-$STAMP
mkdir -p "$DEST"
cp -r dream/firstrun/verified "$DEST/" 2>/dev/null || true
cp dream/firstrun/{REPORT.md,progress.json,run.log} "$DEST/" 2>/dev/null || true
cp "$GRIND/last-pass.txt" "$DEST/" 2>/dev/null || true
git add -f grinder-results
git -c user.name="grinder" -c user.email="grinder@lockstep" \
    commit -q -m "grind: $(hostname) $STAMP" || { git checkout -q main; exit 0; }
git push -q origin grinder-results || true
git checkout -q main
echo "pass done: $DEST"
