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

## Batch weave (2026-08-05, the presence-math payoff)

`weave_realized.py batch` — all census-verified, node-only realized fns whose
files build under the defconfig volume, woven cumulatively with the 10-reader
base. Batched in-kernel probe (ONE kbuild pass measures every struct layout).

**59 eligible → 54 realized woven AND present in vmlinux (+1 already Rust via
the reader path, 4 dropped: erst_exec_add/subtract, netdev_hw_stats64_add,
qfprom_fixup_dt_cell_info), + 10 readers = 64 Rust functions in one booting
kernel** (boot-digest green) across 50 source files in 15 subsystems:
block, mm (filemap/percpu/mremap/page-writeback), net (core/sched/devlink/
ethtool/packet), kernel (sched/fair×3, time×2, cgroup, pid, locking/semaphore),
fs (fat/seq_file/hugetlbfs), drivers (scsi/usb-xhci/mmc/mtd/tty-vt/watchdog/
i2c/input/thermal/firmware/soc-fsl/base). Every fn: sweep-differential PASS +
realized re-differential PASS + dual layout guards at kernel build + boot.

Presence progression: 16 (minimal readers) → 10 (defconfig readers,
LEVER-DEAD) → **64 (defconfig readers+realized)** — the realize class, living
in core files, is the presence lever the config hunt wasn't.

## The safety lift: tier (a) → tier (b) (2026-08-05, per STRATEGY.md)

Mirror-% is pipeline progress; the mission metric is %-Rust × safety tier.
First lift shipped: the transpiler emits a tier-(b) form — the verified logic
in a `#![forbid(unsafe_code)]` module operating on `&mut Mirror` (rustc PROVES
no raw pointers in the core), plus an extern "C" boundary whose entire unsafe
surface is ONE `&mut *p` deref carrying the same validity invariant the C body
relied on. Restricted to single-node fns (two &mut from two C pointers could
alias = UB; multi-node stays tier (a) honestly).

Gates (all load-bearing, pinned in test_lift.py):
- the lifted form must MATCH the SAME differential (behavior pinned across
  the lift, not assumed) — 52/59 eligible MATCH, 0 failures, 7 not-liftable;
- sabotaged safe-core store → DIVERGE (differential holds over the lift);
- a raw-pointer deref smuggled into the core → rustc BUILD_FAIL (the "safe"
  claim is machine-checked, not naming).

### A1 (2026-08-05): field-granular boundary + per-field concurrency audit

The research pass (RESEARCH-SAFE-RUST.md) found the first lift OVER-CLAIMED:
whole-struct `&mut Mirror` asserts LLVM `noalias` over EVERY byte, and the
padded mirror's `[u8; N]` padding covers OTHER REAL kernel fields — a
concurrent access to any of them during the call is UB even though our code
never touches it; kernel-"benign" (`data_race`/KCSAN-tolerated) races are Rust
UB too. A1 closes it two ways:

- **Field-granular boundary**: the core takes one `&mut TY` per ACCESSED field;
  the boundary is `core(&mut (*p).field1, &mut (*p).field2, ...)`. Each borrow
  is field-scoped (Tree Borrows scopes the tag to the field; rustc emits
  `noalias dereferenceable(sizeof field)`), so no whole-struct exclusivity is
  asserted and padding is outside every borrow. Same text works for the host
  arena and woven padded mirror. NO `&mut *p` is ever formed.
- **Per-field concurrency audit** (`realize.field_audit`): a field named in any
  `READ_ONCE`/`WRITE_ONCE`/`data_race` marker anywhere in the tree is
  conservatively lockless → the fn holding it stays tier (a). Name-level
  over-approximation (safe direction). Robust-by-construction (fixed-string
  marker grep + Python `->field` extraction; the mega-regex alternation with
  `\w`-in-bracket silently matched NOTHING — a vacuous zero, do not
  reintroduce) and self-proves non-vacuous (`flags` must appear, 142× tree-wide).

Audit over the 317 single-node liftable efftrace candidates: **199 (63%)
audit-pass = tier-(b) eligible, 118 (37%) demoted** (top demoters: size,
flags, timeout, head, tail — generic names shared with lockless structs).
This is the honest safe-Rust ceiling for efftrace.

Gates (load-bearing, pinned in test_lift.py, 10 tests): field-granular shape
(no whole-struct `&mut *p`); lifted form MATCHes the SAME differential;
sabotaged core store → DIVERGE; raw-pointer deref smuggled into the forbid
module → rustc BUILD_FAIL; audit non-vacuous + demotes a lockless-field fn;
multi-node not liftable.

Reweave + boot (`weave_realized.py batch --lift`): **31 of the 54 woven
realized fns carry machine-checked, field-scoped, audit-clean safe cores in
the booting defconfig kernel**; 23 tier-(a) (20 audit-demoted — incl.
update_load_add/sub (scheduler, lockless), seq_set_overflow, timer_set_idx —
+ 3 multi-node copiers). Boot-digest green.

Safety-tier dashboard: **31 tier-b + 33 tier-a (23 realized + 10 readers)
= 64 Rust fns.** The tier-b count DROPPED from the pre-A1 51 because A1 made
the claim SOUND (whole-struct → field-scoped + concurrency audit) — the
correct number, not the flattering one.

### A2 (2026-08-05): literature-comparable safety metrics (`metrics.py`)

The C→Rust lifting field reports unsafe-LOC% and raw-pointer decl/deref counts
(Laertes, Crown, C2SaferRust, CRustS). We report the same, per fn and fleet-
wide, so the tier claim is backed by numbers. Over the 54 woven realized fns:

- **safe-logic % = 32%** — 139 of 434 translated logic LOC live in a
  `#![forbid(unsafe_code)]` core (rustc-enforced). This is the mission metric
  (%-Rust × safety tier collapsed to one number); it is 0% before any lift and
  rises as tier-b grows AND as tier-a fns get lifted (proven monotone in
  test_metrics.py).
- **raw-ptr derefs: 214, ALL in boundaries, 0 in cores.** The lift both
  REDUCES and CONFINES them: a tier-(a) fn re-derefs the raw pointer for every
  field access (read + write), while a tier-(b) fn borrows each field ONCE in
  the boundary and the core operates on safe references.
- **tier-b unsafe surface** = one field-scoped `&mut (*p).field` per accessed
  field, on the single boundary line; the core logic is 100% forbid-safe by
  construction. (The strict literature unsafe-LOC% — all boundary lines,
  including the trivial call/return glue — averages ~35% per tier-b fn only
  because efftrace leaves are tiny and the fixed 2-line boundary is a large
  fraction of a 4-line core; the raw-deref count is the meaningful surface.)

`weave_realized.py batch --lift` now prints this dashboard over the
present-in-vmlinux set; `metrics.fn_metrics`/`aggregate`/`format_dashboard` are
the API; `test_metrics.py` pins the properties (4 tests).

### A3 (2026-08-05): the reader class lifts too — DETERMINISTICALLY ($0)

Readers are model-WRITTEN (not transpiles), so the plan expected a model-driven
SACTOR-stage-2 refactor. But every verified reader uses exactly one pointer
idiom — `(*p).field` for struct params, `*outp` for scalar out-params — so
`lift_readers.py` lifts them DETERMINISTICALLY (no model, $0), re-gated by the
readers' OWN oracle (`structdiff.harness.close`). Same tier-(b) shape as A1:
logic in a `#![forbid(unsafe_code)]` core over one reference per accessed field
(`&mut`/`&` by write-status; `&` for every field of a `*const` param — sound
even if two `*const` params alias, since shared refs may alias), boundary =
per-field `&mut/& (*p).field`, no whole-struct borrow. Fail-closed refusals:
2+ `*mut` struct pointers (cross-struct &mut aliasing), any non-`(*p).field`
pointer use, a write through `*const`. Same per-field concurrency audit.

Batch over the 10 woven readers (`lift_readers.py batch`): **7 tier-b
(machine-checked safe core, structdiff-MATCH), 3 audit-demoted** (resource_clip
`start`, bitmap_check_region `start`, linear_range_get_value `min` — generic
names flagged lockless somewhere), 0 failures. test_lift_readers.py 3 tests
(clean reader → safe core + MATCH; audit demotes a lockless-field reader;
non-field pointer use refused).

**Combined safety-tier reach if the lifted readers are woven: 31 realized +
7 reader = 38 tier-b of 64.** Weaving them is the mechanical follow-on
(weave_readers already boots reader objects; the lifted candidate keeps the
same `<fn>_rs` ABI + mirror struct + guards, so it is a drop-in source swap —
not done here to avoid perturbing the booting kernel late in the session).
