# realize — model→real translation for the state classes

The sweep banked 635 efftrace candidates verified as CELL MODELS (flat i64
state vector, closed helper vocabulary). They could not weave: the kernel
needs real-struct functions. This module closes that gap deterministically —
no model re-synthesis, no new trust.

## The mechanism (realize.py)

1. **Deterministic transpile.** The verified `rs_call` body's helper calls
   (`field/set_field`, `g/set_g`, `out/set_out`) are rewritten into real
   accesses (`field(F0_BD_WRITERS, a0)` → `((*bdev).bd_writers as i64)`);
   every other token stays VERBATIM; resolved defines become fn-local consts;
   the result is a real-signature `#[no_mangle] extern "C" fn <fn>_rs(...)`.
   Out-of-vocabulary bodies are REFUSED by reason (fail-closed worklist).
2. **Zero-trust re-verification.** The transpiler is NOT trusted: the realized
   fn is re-gated by the SAME efftrace differential (identical C reference
   arena + workload + directed coverage); only the Rust side swaps the cell
   model for a real-layout `#[repr(C)]` arena. A transpile bug is a state
   divergence. Selfcheck pins: correct → MATCH, sabotaged store → DIVERGE.
3. **Kernel weave (weave_realized.py).** The real offsets are PROBED in-kernel
   (`char cgir_off_f[offsetof+1]` arrays compiled by kbuild, read via nm -S —
   the compiler itself reports the layout under the volume's .config). The
   object carries a minimal PADDED mirror (accessed fields only, at probed
   offsets) with dual guards: rustc `offset_of!` const-asserts AND in-tree
   `_Static_assert`s — drift fails either compile. Then the standard ratchet:
   seam, nm presence, boot digest.

## Live catches (all by the differential/guards, none by luck)

- Unemitted define consts silently became Rust MATCH-PATTERN BINDINGS (first
  arm matched everything) — compiled clean, diverged, fixed + unknown-ALLCAPS
  refusal added.
- C fields named `type` (Rust keyword) → r# escaping at every emission site.
- C `bool` params emitted a nonexistent `u1` type → width→type helper.

## Census over the 635-candidate bank (census.jsonl, resumable)

| outcome | n |
|---|---|
| **REALIZED + RE-VERIFIED (MATCH)** | **480 (75.6%)** |
| refused: early `return` in body | 79 |
| refused: non-const field base | 36 |
| refused: cross-slot access | 18 |
| refused: unknown const token | 10 |
| refused: unknown global const | 1 |
| residual BUILD_FAIL_RS (tail) | 11 |

**Zero DIVERGEs** across 480 full differentials — every candidate that
transpiles and compiles passes. The refusal classes are the v2 worklist
(early-return needs a labeled-block transform; non-const bases are computed
field indices; cross-slot is multi-instance access).

## Boot capstone (defconfig base)

`weave_realized.py gate block/bdev.c bdev_block_writes` — the realized fn
woven CUMULATIVELY with the 10-reader defconfig batch: **11/11 seams present
in vmlinux** (`bdev_block_writes_rs` at kernel-probed offset 176 of the
992-byte real `struct block_device`), boot green. The first model→real
state-transition function in a booting kernel; block/bdev.c is core (built in
every config), which is exactly why the realize classes matter for presence:
they live in core files, unlike the driver-heavy readers.

## Why this changes the presence math

RUN-DEFCONFIG graded config coverage LEVER-DEAD for readers (83/104 in files
no arm64 config builds). The efftrace bank skews to block/, kernel/, lib/,
mm/, fs/ — files built in EVERY config. 480 realized fns are the new
weave-ready pool; presence now scales with the batch weave, not with config
archaeology.
