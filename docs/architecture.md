# Architecture

This document is the map of *how the pieces fit*. For the original design
rationale (why regions, why sanitizers) read [`design.md`](design.md); for the
findings read `dream/{SUMMARY,RESEARCH,SWEEP}.md`; to run things read
[`GETTING-STARTED.md`](GETTING-STARTED.md).

## The one invariant

**Nothing is accepted on trust in the generator.** Every path from "C function"
to "Rust in the tree" passes through an oracle that can *reject*, and every
oracle ships a negative control proving it rejects the broken case. The
generator is interchangeable (transpiler, local model, API model); the oracles
are the project.

## Two eras, one spine

Lockstep grew in two phases that share a harness:

1. **The milestone ladder (`baseline/` … `rfc-export/`, née M0–M5)** — proving
   the concurrency loop end to end on hand-picked regions: capture a sanitizer
   baseline, extract the lock→field map, hand-transplant one region, have a
   model synthesize one, gate it in a booting kernel, emit an RFC. This is the
   *depth-first* proof that the idea works.

2. **The ratchet (`dream/`)** — the pivot to breadth: rewrite the kernel
   function by function, keep a running %-Rust metric, and never move backward.
   Everything novel about scaling, cost, and the limits was learned here.

Both eras run on the same **containerized kbuild + QEMU harness** inherited from
CGIR (`cgir-kernel-gate` image, `cgir-kbuild` volume). The ladder added KCSAN /
lockdep configs to it; the ratchet drives it in a loop.

## The pipeline (breadth era)

```
   harvest ──► classify ──► synthesize ──► VERIFY ──► weave ──► boot-gate ──► manifest
   (census)   (purity)     (the ladder)  (oracle)   (excise    (ratchet     (source of
                                          tiers)     C → Rust)  forward-only) truth)
```

Each stage is a small script; the arrows are plain data (JSON worklists, `.rs`
candidates). The stages:

### 1. Harvest & census — the denominator

`dream/sweep/census.py` classified 24,194 real functions into tiers:

- **A — leaf-scalar** (34.0%): scalar in, scalar out, no struct fields, no lock.
- **B — struct** (47.8%): reads struct fields, no lock/entanglement.
- **C — locked** (7.2%): takes a spin/mutex/rcu/seq lock.
- **D — entangled** (11.0%): container_of, per-cpu, RCU-deref, lists, ops
  tables — the **C-forever floor**.

~89% (A+B+C) is mechanically reachable. That is a *reachability* number, not a
verifiability number — see stage 4.

### 2. Classify — the purity router

`dream/widerun/purity.py` decides *which oracle a function is allowed to use*.
Conservative by construction: **not-provably-pure = impure**. Pure functions
(args + locals, no effect markers) may use a value differential; everything else
is quarantined for an effect-trace oracle. This is the single most important
soundness component — it's why a return-value check never over-credited a
side-effecting function after the `__refrigerator` incident (`dream/SWEEP.md`).

### 3. Synthesize — the ladder

`dream/ladder/` runs generators cheapest-first, the oracle arbitrating each:

```
c2rust ($0, deterministic)  →  local model ($0, ollama)  →  API model (tail only)
```

Because the gate is sound, a weaker generator only costs *retries*, not
correctness — synthesizer quality is a wall-clock knob. The deterministic rung
alone swept the fleet battery (`dream/ladder/RESULTS.md`).

### 4. Verify — the oracle tiers (the heart)

The generator's output is meaningless until an oracle certifies it. Which oracle
depends on the tier, and **each certifies a different, explicitly-labeled
strength**:

| tier | oracle | mechanism | strength |
|---|---|---|---|
| pure leaf | `dream/hostdiff/` | real C TU + shim on the host, auto-probe, ~2M cases, no boot | bit-exact differential (sampled) |
| pure, small domain | `dream/exhaustive/`, `dream/formal/` | full-domain enumeration / Kani-CBMC | **proof** over the domain |
| struct-reading (B) | `dream/mirror/` + differential | `#[repr(C)]` mirror proven `rustc == generator == kernel`, then diff | layout-proven + behavior-diffed |
| driver / MMIO | `dream/recorder/` | record the C's register trace once, replay candidates | trace-equal (register program) |
| locked / concurrent (C) | `dream/concgate/`, the M-ladder gates | KCSAN + lockdep + invariant, stock-vs-transplant under load | no-new-races vs baseline |
| everything else | *(quarantined)* | owes an effect trace; not auto-verifiable | — |

The **oracle wall** lives in this table: ~17% of the kernel reaches a *strong*
row, ~73% only reaches a weak one ("boots + KCSAN quiet" = didn't-crash, not
correct), ~11% reaches none. Reachable ≠ verifiable. This is the binding
constraint, not compute or tokens (`dream/RESEARCH.md`).

### 5. Weave — excise C, insert Rust

`dream/ratchet/weave.py` performs the in-tree edit: a verified function's body
is replaced by a forwarding shell that calls the Rust seam symbol; the Rust
compiles freestanding (`rustc --emit=obj`, `-C panic=abort`) and ships as a
`.o_shipped` object wired into the subsystem Makefile. Two mechanics matter:

- **Static-helper clusters** (`dream/cluster/`): weaving a function that calls a
  file-local `static` helper orphans the helper (`-Werror=unused-function`). The
  fix is to weave the whole cluster — entry `#[no_mangle]`, helpers private,
  all excised — verified at the exported boundary.
- **Panic-handler collisions**: N freestanding objects each define
  `rust_begin_unwind`; all but one get `objcopy --localize-symbol`.

### 6. Manifest — forward-only source of truth

`dream/ratchet/manifest.json` records `{file, symbol, status, tier}` per
function. `verified` and `woven` are **distinct states** (a function can be
proven equal to C yet not-yet-weavable — e.g. `gcd` before cluster weaving). The
ratchet only advances the manifest on a green boot; `weave.py status` renders
the %-Rust dashboard from it.

## Scaling (why it's a constant-factor problem)

Capability was complete once one model-synthesized leaf booted (Ring 1).
Everything after is wall-clock:

- **Batching** (`ring2`): many functions woven into one Image, one boot verifies
  the whole batch (~292k comparisons).
- **Fleets** (`ring5`): parallel synth → one batch boot → counterexample-retry
  on failures. The gate *caught* a wrong synth and the retry converged.
- **Workers** (`ring7`): independent build+boot pipelines, near-linear (1.99× on
  2). The bottleneck is provisioning, not intelligence.
- **Boot elimination** (`hostdiff`): the biggest lever — most verifications never
  need a boot at all (~900× cheaper). See `dream/COSTDOWN.md`.

Formula: `wall-clock ≈ (functions / batch) / workers × boot_time`, with the pure
majority taken out of the boot path entirely.

## Extension seams

- **New oracle** → add a `dream/<name>/` with a `gate.sh` (must include a
  negative control) and a `RESULTS.md`; wire it into the verify table above.
- **New generator** → add a rung to `dream/ladder/ladder.py`; it must produce a
  candidate the oracle can judge, nothing more.
- **New shim symbol** → grow `dream/hostdiff/kshim.h` (host) or a per-family
  header; each addition unlocks every TU that needed it.
- **New target subsystem** → point `census.py` / `harvest.py` at it via `KSRC`;
  mirror any struct families first (`dream/mirror/`).

## Directory index

| dir | role |
|---|---|
| `baseline/` | M0 — sanitizer baseline capture |
| `extraction/` | M1 — concurrency IR (lock→field), TSan-crosschecked |
| `transplant/` | M2 — first hand transplant, loom-proven |
| `synthesis/` | M3 — first model-synthesized region + `_api_key`/candidate parsing (imported repo-wide) |
| `kernel-gate/` | M4 — in-kernel KCSAN gates (depth + breadth) |
| `rfc-export/` | M5 — RFC series emitter |
| `dream/ratchet/` | manifest, weaver, rings 0–9 |
| `dream/diffgate/` | in-kernel differential oracle |
| `dream/sweep/`, `dream/widerun/` | census + production runs + purity router |
| `dream/hostdiff/` | boot-free differential oracle (T0) |
| `dream/recorder/`, `dream/mirror/`, `dream/cluster/` | the three critical-path libraries |
| `dream/ladder/`, `dream/localmodel/` | synthesis ladder + local-model bench |
| `dream/concgate/`, `dream/exhaustive/`, `dream/formal/` | concurrency gate, exhaustion, Kani/CBMC |

Note: `synthesis/synthesize.py` is imported by many `dream/` scripts (via
`sys.path`) for its `_api_key` and candidate-parsing helpers — the milestone
directories are not dead history, they're still load-bearing.
