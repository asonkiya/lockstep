# Getting started

Lockstep has two tiers of gate. The **host tier** needs only a C compiler, Rust,
and Python and runs in seconds — start here. The **kernel tier** boots real
Linux under QEMU in a container and takes minutes — set it up only when you want
to reproduce the in-kernel results.

## Prerequisites

**Host tier (all the boot-free gates):**

- Python 3.10+
- A C compiler (`cc` / clang or gcc)
- `rustc` (stable; `rustup` recommended). No cargo project needed — the gates
  invoke `rustc` directly.
- A Linux source checkout somewhere, pointed to by the `KSRC` environment
  variable. The host gates *read* kernel C; they don't build it. Any recent
  mainline tree works:
  ```bash
  git clone --depth 1 https://github.com/torvalds/linux /path/to/linux
  export KSRC=/path/to/linux
  ```

**Synthesis ladder (optional, for `dream/ladder/` and `dream/localmodel/`):**

- `c2rust` — the deterministic rung. Needs LLVM **18–21** (LLVM 22 removed APIs
  it depends on) + cmake:
  ```bash
  brew install llvm@21 cmake          # or your distro's llvm-18..21 + cmake
  LLVM_CONFIG_PATH=$(brew --prefix llvm@21)/bin/llvm-config cargo install c2rust
  ```
- `ollama` + a local coding model for the free LLM rung:
  ```bash
  ollama pull qwen2.5-coder:14b
  ```
- An API key **only** for the top rung. The scripts read `ANTHROPIC_API_KEY`
  from the environment or a `.env` file; never commit it.

**Kernel tier (in-kernel gates):**

- Docker.
- The `cgir-kernel-gate` image and the `cgir-kbuild` volume (an arm64 Linux
  tree configured with `CONFIG_KCSAN=y`, `CONFIG_PROVE_LOCKING=y`). These come
  from the CGIR harness setup — see the CGIR repo. The gates run QEMU arm64
  inside the container.
- For the formal tier (`dream/formal/`): `cargo install --locked kani-verifier
  && cargo kani setup` (installs CBMC).

## Run your first gate (30 seconds)

The boot-free differential oracle verifies 7 real kernel functions against their
Rust rewrites — ~16M differential cases, then a negative control:

```bash
KSRC=/path/to/linux bash dream/hostdiff/gate.sh
```

Expect: `HOSTDIFF GATE: PASS (7 MATCH + 1 control DIVERGE, ~16M ... 0 boots)`.

Two more host-tier gates, each self-contained:

```bash
KSRC=/path/to/linux bash dream/cluster/gate.sh   # static-cluster weaving
bash dream/recorder/gate.sh                       # MMIO record/replay
```

## Verify your own rewrite

`hostdiff` is a standalone CLI. Point it at a pure kernel function and a Rust
candidate that exports `cgir_<name>` (or uses `#[export_name]`):

```bash
python3 dream/hostdiff/hostdiff.py lib/math/gcd.c gcd --cand my_gcd.rs
# cross-TU dependency? pass --deps:
python3 dream/hostdiff/hostdiff.py lib/math/lcm.c lcm --cand my_lcm.rs --deps lib/math/gcd.c
```

Verdicts: `MATCH` (bit-exact over the probe), `DIVERGE` (with the first
counterexample), or a build-stage failure (`CC_TU_FAIL` = the shim needs a
symbol — grow `dream/hostdiff/kshim.h`; `RUSTC_FAIL`; `LINK_FAIL`; `HANG` =
candidate loops; `NO_EXPORT`).

**Soundness note.** `hostdiff` is the T0 oracle for *pure* functions only. Use
`dream/widerun/purity.py` to classify first — anything not provably pure must go
to the trace oracle (`dream/recorder/`) or the in-kernel gate, never a value
differential. This is the discipline that keeps the false-pass count at zero.

## Run the synthesis ladder

Translate + verify with no API bill, escalating only when the free rungs fail:

```bash
export PATH="$HOME/.cargo/bin:$PATH"          # for c2rust
python3 dream/ladder/ladder.py                 # c2rust -> local 14B -> Haiku
python3 dream/ladder/ladder.py --skip-haiku    # free rungs only, $0 guaranteed
```

Each function reports which rung solved it, the cost, and the log. See
`dream/ladder/RESULTS.md` for what to expect.

## Reproduce an in-kernel result (kernel tier)

With the Docker harness in place, the ratchet weaves verified Rust into the tree
and boots it:

```bash
python3 dream/ratchet/weave.py status   # the %-Rust dashboard from the manifest
python3 dream/ratchet/weave.py gate      # apply + build + boot + boot-digest gate
```

`weave.py apply` leaves the tree woven; `build` stops after the Image; `gate`
boots and checks. Always gate against a **pristine** tree (you can't
differentially test against C you've already removed) — the ring scripts under
`dream/ratchet/ring*/` handle restore-then-weave for you.

## Layout of a typical component

Every mechanism directory follows the same shape, so once you've read one you
can read them all:

- `*.py` / `*.rs` / `*.c` — the mechanism and its fixtures
- `gate.sh` — reproduces the result, including a **negative control** (the gate
  must reject a deliberately-broken variant, or it proves nothing)
- `RESULTS.md` — the measured numbers and the honest scope

## Troubleshooting

- **`CC_TU_FAIL` on a host gate** — the kernel TU uses a symbol the shim doesn't
  define yet. Add it to `dream/hostdiff/kshim.h` (or `dream/cluster/kdefs.h`);
  each addition unlocks every TU in that family.
- **c2rust panics on a path assertion** — it dislikes symlinked temp dirs; the
  scripts already canonicalize to `/private/tmp` on macOS.
- **`rustc` link errors with N Rust objects** — multiple `#[panic_handler]`s
  collide (`rust_begin_unwind`); `weave.py` localizes all but one with
  `objcopy --localize-symbol`. See `dream/ratchet/weave.py`.
- **A boot gate hangs** — a fast racy transplant can outrun KCSAN's ~1/4000
  sampling; the probes pace with `udelay` to give the detector dwell time.
