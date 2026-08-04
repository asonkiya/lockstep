# Milestone B — the ksdk mirror factory

The %-moving lever (Run 2 decision). Ring 8 hand-wrote one mirror; Ring 9
showed one mirror frees a function family; this builds them at scale,
fail-closed, and banks a reusable ksdk crate.

## What shipped

`dream/mirrorfactory/factory.py` — `census | bank | build | export`, plus the
core widener in `dream/mirror/mirror.py`: **config-aware `#if` resolution**
under a pinned `.config` (extracted from the real build volume,
`pinned.config`, 4923 CONFIG symbols). Struct bodies with `CONFIG_*`
conditionals — previously refused wholesale ("layout not fixed", the single
largest struct-resolution wall) — now resolve to the pinned layout, flagged
`config_pinned` so the claim stays scoped.

## Measured results

- **Config-pinning delta**: readers reach **78 → 84 (+6)** — real functions
  (`lock_time_add`, `__refill_cfs_bandwidth_runtime`, `pcpu_block_update`, …)
  whose structs were `#if`-refused, now admitted. This is the mirror factory's
  struct-shaped lever for the readers corpus; the residual blockers there are
  all non-struct (unions, field-type maps, const arrays).
- **The ksdk bank**: **131 mirrors banked, 92 refused** out of 223 structs the
  accepted Tier-B surface uses. Of the 131: 115 host-dual-verified (rustc const
  layout asserts + cc `_Static_assert` guard TU), 16 c-guard-deferred to the
  in-kernel BUILD_BUG_ON (nested by-value types, flagged), 3 config-pinned.
- **Export**: `ksdk_mirrors.rs`, 148 `#[repr(C)]` types, dependencies
  deduplicated, **compiles clean**.

## Soundness

Every banked mirror passed BOTH host guards (rustc + cc) — a wrong layout
fails to build. The dual guard is non-circular: the cc guard defines the
struct by the REAL C ABI and `_Static_assert`s it against the generator's
arithmetic, so a bug in `mirror.py`'s layout model is caught, not blessed.
The final in-kernel BUILD_BUG_ON re-certifies against real headers at
transplant time (Ring 8's gate) — the host guards catch generator/layout
drift early and cheaply. Nothing is guessed: 92 refusals carry reasons
(`registry.json`) and are the factory's own census-fix backlog.

## Refusal backlog (census-order, Run-3 wideners)

| count | class | widener |
|---|---|---|
| 17 | `field kobj` (struct kobject etc.) | opaque-primitive probe (probe_primitives.py exists; unrun) |
| 10 | contains a union | tagged-union host layout |
| 4 | bitfield | bitfield → explicit mask accessors |
| 3 | `field dev` (struct device) | opaque-primitive probe |
| others | enums-as-field-types, fn-ptr typedefs, `#if` non-CONFIG | type-alias table |

The opaque-primitive probe alone (kobj + dev + the other kernel primitives)
would convert ~25 of the 92 — the highest-ROI next widener, and it reuses the
existing `probe_primitives.py` + PRIMITIVE_SIZES machinery (in-kernel sizeof,
gate-recertified).

## How it plugs in

Consumers opt into config-pinning with `MIRROR_CONFIG=<.config>` (env
auto-load in mirror.py) — no per-harness edits. The bank is the ksdk crate's
mirror module; transplant weaves `ksdk_mirrors.rs` and the in-kernel guard
re-certifies. Rebuild the bank any time: `factory.py bank && factory.py export`.
