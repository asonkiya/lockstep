#!/usr/bin/env bash
# setup_grinder.sh — turn a fresh arm64 Ubuntu 22.04/24.04 box (Oracle A1 free
# tier, Hetzner CAX, anything with KVM) into a lockstep grind worker.
#
# Idempotent: safe to re-run. Run as the default user (ubuntu/root ok):
#   ssh ubuntu@<box> 'bash -s' < setup_grinder.sh
#
# What it does:
#   1. build deps + qemu-kvm (native-speed boots — no TCG penalty on real Linux)
#   2. rustup (stable + rust-src, for kernel Rust objects)
#   3. ollama + qwen2.5-coder:7b (the $0 synth rung)
#   4. shallow-clones linux (v6.16) + lockstep, warms a defconfig build + ccache
#   5. installs the grind systemd service+timer (runs grind.sh every 30 min;
#      skips if one is already running)
#
# NOT done here (manual, once):
#   - lockstep deploy key: on the box `ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519`
#     then add ~/.ssh/id_ed25519.pub as a read-write deploy key on
#     github.com/asonkiya/lockstep (results are pushed to the grinder-results branch)
#   - ANTHROPIC_API_KEY for the Haiku tail: write it to ~/grind/.env (chmod 600).
#     Without it the ladder still runs template+local rungs (most solves are $0).
set -euo pipefail

GRIND=~/grind
KVER=v6.16
mkdir -p "$GRIND"

echo "== [1/5] packages =="
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq build-essential flex bison libssl-dev libelf-dev \
    bc ccache git python3 python3-pip qemu-system-arm cpu-checker curl rsync \
    libncurses-dev clang lld llvm
kvm-ok || echo "WARNING: no KVM — boots will fall back to TCG (still works, slower)"

echo "== [2/5] rust =="
if ! command -v rustup >/dev/null; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y -q
fi
source ~/.cargo/env
rustup -q toolchain install stable
rustup -q component add rust-src
cargo install --quiet bindgen-cli || true   # kernel Rust needs bindgen

echo "== [3/5] ollama (the \$0 synth rung) =="
if ! command -v ollama >/dev/null; then
    curl -fsSL https://ollama.com/install.sh | sh
fi
sudo systemctl enable --now ollama || true
ollama pull qwen2.5-coder:7b || echo "WARNING: qwen pull failed — ladder will skip the local rung"

echo "== [4/5] trees + warm build =="
if [ ! -d "$GRIND/linux" ]; then
    git clone --depth 1 --branch $KVER \
        https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git "$GRIND/linux"
fi
if [ ! -d "$GRIND/lockstep" ]; then
    git clone git@github.com:asonkiya/lockstep.git "$GRIND/lockstep" \
        || echo "WARNING: lockstep clone failed — add the deploy key (see header), then re-run"
fi
export PATH="/usr/lib/ccache:$PATH"
cd "$GRIND/linux"
if [ ! -f .config ]; then
    make -s ARCH=arm64 defconfig
    ./scripts/config -e RUST -e KUNIT -d MODVERSIONS || true
    make -s ARCH=arm64 olddefconfig
fi
make -s ARCH=arm64 -j"$(nproc)" Image && echo "warm build OK: arch/arm64/boot/Image"

echo "== [5/5] grind service =="
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/grind.service <<EOF
[Unit]
Description=lockstep grind pass
[Service]
Type=oneshot
ExecStart=/usr/bin/env bash $GRIND/lockstep/dream/infra/grinder/grind.sh
TimeoutStartSec=10800
EOF
cat > ~/.config/systemd/user/grind.timer <<EOF
[Unit]
Description=lockstep grind loop
[Timer]
OnBootSec=2min
OnUnitInactiveSec=30min
[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now grind.timer
sudo loginctl enable-linger "$USER"   # keep the user timer alive without a login session

echo "GRINDER READY — worklist comes from lockstep/dream/infra/grinder/worklist.json; results push to the grinder-results branch"
