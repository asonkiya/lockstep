# Entanglement composition of the minimal-kernel core — measured

`python3 dream/router/entangle.py` over **40,065 functions / 1,635 files** in the
core dirs (kernel, mm, lib, arch/arm64, block, security, ipc):

| class | share | oracle | status |
|---|---|---|---|
| bounded_state | **35.5%** | effect-trace (CGIR effects + replay) | **BUILD** ← linchpin |
| pure_leaf | 22.9% | scalar differential (hostdiff) | DONE |
| unbounded_state | 15.4% | in-kernel differential / workload | HARD |
| concurrent | 13.9% | loom + KCSAN (concgate) | PROTOTYPED |
| struct_reader | 8.2% | mirror differential (structdiff) | DONE |
| arch_asm | 4.0% | unsafe/global_asm! + boot-digest | FLOOR |
| mmio | 0.0% | record/replay trace | DONE |

## The headline (the thesis, measured)

The entangled core is **not** a research wall:

- **~31% has a production oracle TODAY** (pure_leaf + struct_reader + mmio).
- **+13.9% is prototyped** (concurrent → loom/KCSAN, proven in miniature M0–M2,
  needs scaling not invention). So **~45% is oracle-covered** on existing mechanism.
- **+35.5% unlocked by ONE build** — the effect-trace oracle. This is the single
  highest-leverage core build (**+36pp**), and it's exactly the CGIR↔lockstep
  seam: CGIR's `effects` layer computes the read-set/write-set footprint; lockstep
  records it under a workload and replays the Rust against it.
- **~19% genuine hard tail + floor** (unbounded pointer-graph state 15.4% +
  arch-asm 4.0%) — the fallback is in-kernel differential-under-workload; the
  arch-asm slice stays `unsafe`/asm by design (still Rust, not safe Rust).

So: existing oracles + the effect-trace build ⇒ **~81% of the core is auto-
rewritable with a sound gate**, ~19% is the hard/floor residue. That is the
concrete, measured refutation of "the core is unautomatable."

## Honest caveats (do not over-read)

- **Signature-based classification**, conservative. `bounded_state` (35.5%) is
  "effectful but no pointer-graph signal in the body" — an **upper bound** on the
  effect-trace oracle's reach: some will turn out unbounded once effects are
  followed **through callees**, which the regex can't see. CGIR's interprocedural
  `effects`/PDG analysis is the precise refinement (and the oracle's real input);
  expect the true `bounded_state` to be somewhat lower after that pass.
- **"Auto-rewritable" means a sound ORACLE exists, not that synthesis succeeds** —
  the model still has to produce correct Rust; the gate just guarantees a wrong
  one can't pass. Synthesis yield is separate (and cheap: template/local/Haiku).
- `concurrent` is PROTOTYPED, not scaled; counting it in "45% now" is optimistic.
- `arch_asm` (4%) likely under-counts the true arch/boot floor (asm hidden in
  macros/headers); a few entries are spurious asm-regex matches. Directionally low.

## What this makes the next build

The **effect-trace oracle**, footprint-driven by CGIR's effects layer — it is the
+36pp move and the reason CGIR + lockstep were built as a pair. Backlog:
`dream/router/reach_accepted.json`-style worklist can be emitted per class from
this scan (`entangle.py` already routes each function).
