#!/usr/bin/env bash
# setup_3080.sh — turn a borrowed x86_64 Linux box with an NVIDIA GPU (RTX
# 3080 class) into a lockstep BIG-PASS worker. Time-boxed-box design: no
# systemd timer, no deploy keys, no secrets by default — the repo arrives via
# rsync and results leave via rsync (burst.sh discipline). Driven from the Mac
# by push_3080.sh.
#
# Idempotent; run as a sudo-capable user:
#   ssh user@box 'bash -s' < setup_3080.sh
#
# Why a GPU box: localbench (dream/localmodel/RESULTS.md) measured
# qwen2.5-coder:14b at 62.5% first-pass on real kernel fns — ~85% of Haiku at
# $0 — but 14b was wall-clock-bound on the M2. A 3080 runs the 14b rung at
# interactive speed, so the overnight.py ladder (local -> Haiku, gate-
# arbitrated) can take a BIG boot-free harvest pass with the local model doing
# the bulk. VRAM note: 14b q4_K_M needs ~9 GB — fits a 10 GB 3080; if ollama
# reports partial CPU offload, fall back to LOCAL_MODEL=qwen2.5-coder:7b.
#
# SOUNDNESS (do not remove the cc shim): gates here verify candidates for an
# arm64 kernel target, but this host is x86_64, where plain `char` defaults to
# SIGNED (arm64: unsigned). Every gate C compile goes through `cc`, so we
# install /usr/local/bin/cc -> gcc -funsigned-char, pinning arm64 char
# semantics for both differential sides. On arm64 hosts this flag is the
# default, so gates remain comparable across boxes.
#
# NOT done here: NVIDIA driver install (system setting — user-only). If
# nvidia-smi is absent the setup continues but warns: ollama will run CPU-only
# and the 14b rung will be too slow to be worth the box.
set -euo pipefail

GRIND=~/grind
KVER=v6.16
MODEL=${LOCAL_MODEL:-qwen2.5-coder:14b}
mkdir -p "$GRIND"

echo "== [0/5] GPU check =="
if command -v nvidia-smi >/dev/null; then
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
else
    echo "WARNING: nvidia-smi not found — NVIDIA driver missing. ollama will be CPU-only."
    echo "         Driver install is a system change: do it manually, then re-run."
fi

echo "== [1/5] packages =="
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq build-essential git python3 python3-pip curl rsync

echo "== [2/5] cc shim (arm64 char semantics — see header) =="
sudo tee /usr/local/bin/cc >/dev/null <<'EOF'
#!/bin/sh
# lockstep gate shim: pin arm64-kernel plain-char semantics on this x86 host.
exec gcc -funsigned-char "$@"
EOF
sudo chmod +x /usr/local/bin/cc
# fail loudly if the shim isn't what `cc` resolves to
[ "$(command -v cc)" = /usr/local/bin/cc ] || { echo "FATAL: cc shim shadowed by $(command -v cc)"; exit 2; }
# grind.sh prepends /usr/lib/ccache to PATH — a ccache cc there would shadow
# the shim and silently reopen the char-signedness hole. Refuse to proceed.
if [ -e /usr/lib/ccache/cc ]; then
    echo "FATAL: /usr/lib/ccache/cc exists and would shadow the shim during passes."
    echo "       sudo rm /usr/lib/ccache/cc (or uninstall ccache), then re-run."
    exit 2
fi

echo "== [3/5] rust =="
if ! command -v rustup >/dev/null && [ ! -x ~/.cargo/bin/rustup ]; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y -q
fi
[ -f ~/.cargo/env ] && source ~/.cargo/env
rustup -q toolchain install stable

echo "== [4/5] ollama + $MODEL =="
if ! command -v ollama >/dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
fi
sudo systemctl enable --now ollama || (ollama serve >/dev/null 2>&1 &)
sleep 2
ollama pull "$MODEL"
# measure GPU offload with a real generate, then inspect placement
curl -s http://localhost:11434/api/generate \
    -d "{\"model\":\"$MODEL\",\"prompt\":\"fn main(){}\",\"stream\":false,\"options\":{\"num_predict\":8}}" >/dev/null || true
ollama ps
if ollama ps | grep -qi "cpu"; then
    echo "WARNING: $MODEL not fully on GPU — consider LOCAL_MODEL=qwen2.5-coder:7b in ~/grind/.env"
fi

echo "== [5/5] kernel source (source-only — this box never builds or boots kernels) =="
if [ ! -d "$GRIND/linux" ]; then
    git clone --depth 1 --branch $KVER \
        https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git "$GRIND/linux"
fi

echo "3080 BOX READY — push the repo + kick a pass with push_3080.sh from the Mac"
