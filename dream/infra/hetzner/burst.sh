#!/usr/bin/env bash
# burst.sh — rent-a-fleet for finishing a milestone by Monday. Creates N arm64
# KVM boxes on Hetzner (hourly billing), provisions each as a grind worker,
# shards work by PHASE assignment, collects results, destroys the fleet.
#
# Prereqs (your steps, once): Hetzner Cloud account -> new project -> API token
# (Read+Write) -> `export HCLOUD_TOKEN=...` (or `hcloud context create lockstep`).
# An ssh key uploaded to the project (`hcloud ssh-key create --name mac
# --public-key-from-file ~/.ssh/id_ed25519.pub`).
#
# Usage:
#   burst.sh up N [cax31|cax41]   create+provision N workers (default cax31,
#                                 8 vCPU / 16 GB, ~€0.02/hr each)
#   burst.sh status               fleet + last-pass tail per box
#   burst.sh collect DIR          rsync every box's banked results into DIR
#   burst.sh down                 destroy ALL burst-labeled servers (hourly
#                                 billing stops; results are NOT auto-collected)
#
# Economics [MEASURED basis]: a CAX31 runs a full overnight.py boot-free pass
# comfortably; 4 boxes x a weekend ~ €5-8 total. Work is sharded properly:
# every box runs ALL phases with GRIND_SHARD=k/GRIND_OF=N — shardlib slices
# each raw worklist modulo, so shards are disjoint and exhaustive by
# construction (dream/tests/test_shard.py pins the contract).
#
# No secrets land on burst boxes: the repo goes up via rsync (not deploy keys),
# results come back via rsync. Pass --with-haiku on `up` to ALSO push the
# ANTHROPIC_API_KEY from lockstep's sibling .env for the Haiku tail rung.
set -euo pipefail

LABEL=lockstep-burst
IMAGE=ubuntu-24.04
REPO=$(cd "$(dirname "$0")/../../.." && pwd)   # lockstep checkout root
ALL_PHASES="READERS=1 CONTAINERS=1 EFFTRACE=1 PHASE2=0"

need() { command -v "$1" >/dev/null || { echo "missing: $1" >&2; exit 2; }; }
need hcloud; need rsync

fleet() { hcloud server list -l "$LABEL" -o columns=name,ipv4,status -o noheader; }

case "${1:-}" in
up)
    N=${2:?usage: burst.sh up N [type]}
    TYPE=${3:-cax31}
    WITH_HAIKU=0; [ "${4:-}" = "--with-haiku" ] && WITH_HAIKU=1
    KEY=$(hcloud ssh-key list -o columns=name -o noheader | head -1)
    [ -n "$KEY" ] || { echo "no ssh key in project — hcloud ssh-key create ..." >&2; exit 2; }
    for i in $(seq 1 "$N"); do
        hcloud server create --name "burst-$i" --type "$TYPE" --image "$IMAGE" \
            --ssh-key "$KEY" --label "$LABEL=" >/dev/null &
    done
    wait; echo "created $N x $TYPE"; sleep 20
    i=0
    fleet | while read -r name ip _; do
        echo "== provision $name ($ip) =="
        SSH="ssh -o StrictHostKeyChecking=accept-new root@$ip"
        $SSH 'bash -s' < "$REPO/dream/infra/grinder/setup_grinder.sh" || { echo "$name provision FAILED"; continue; }
        rsync -az --delete -e "ssh -o StrictHostKeyChecking=accept-new" \
            --exclude .git --exclude 'dream/firstrun/verified' \
            "$REPO/" "root@$ip:/root/grind/lockstep/"
        $SSH "mkdir -p /root/grind && { printf 'GRIND_PHASES=\"%s\"\n' '$ALL_PHASES';
              printf 'GRIND_SHARD=%s\nGRIND_OF=%s\n' '$((i + 1))' '$N'; } > /root/grind/.env \
              && chmod 600 /root/grind/.env"
        if [ "$WITH_HAIKU" = 1 ] && [ -f "$REPO/../llm-semantic-compilers/.env" ]; then
            grep '^ANTHROPIC_API_KEY=' "$REPO/../llm-semantic-compilers/.env" | $SSH 'cat >> /root/grind/.env'
        fi
        $SSH 'systemctl --user start grind.service || bash /root/grind/lockstep/dream/infra/grinder/grind.sh &'
        echo "$name: grinding shard $((i + 1))/$N [$ALL_PHASES]"
        i=$((i+1))
    done
    ;;
status)
    fleet
    fleet | while read -r name ip _; do
        echo "-- $name --"; ssh -o StrictHostKeyChecking=accept-new "root@$ip" \
            'tail -3 /root/grind/last-pass.txt 2>/dev/null || echo "(no pass yet)"'
    done
    ;;
collect)
    DIR=${2:?usage: burst.sh collect DIR}; mkdir -p "$DIR"
    fleet | while read -r name ip _; do
        rsync -az "root@$ip:/root/grind/lockstep/dream/firstrun/verified/" "$DIR/$name-verified/" 2>/dev/null || true
        rsync -az "root@$ip:/root/grind/lockstep/dream/firstrun/REPORT.md" "$DIR/$name-REPORT.md" 2>/dev/null || true
        rsync -az "root@$ip:/root/grind/lockstep/dream/firstrun/progress.json" "$DIR/$name-progress.json" 2>/dev/null || true
    done
    echo "collected into $DIR"
    ;;
down)
    fleet
    read -r -p "destroy ALL of the above? [y/N] " a
    [ "$a" = y ] || exit 1
    hcloud server list -l "$LABEL" -o columns=name -o noheader | while read -r name; do
        hcloud server delete "$name" >/dev/null && echo "deleted $name"
    done
    ;;
*)
    sed -n '2,30p' "$0"; exit 2;;
esac
