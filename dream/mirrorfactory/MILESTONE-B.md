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

## Opaque-primitive probe (Run-3 widener, landed)

The probe (`dream/mirror/probe_primitives.py`) measures config-dependent opaque
types **in-kernel** — a probe.c compiled inside the real kernel build with
`char cgir_sz_T[sizeof(T)]` per type; the ELF symbol size IS the value, read
via `nm`. Extended census-driven (reads the factory's own refusal registry) +
a curated set of the top blockers. Now robust: an undeclared type (subsystem
typedef without its header, or config-gated) is dropped and re-probed, so the
probe measures whatever the real build declares and honestly reports the rest.

Measured this pass (43 primitives total, up from 28): `struct kobject` 64,
`struct device` 1248, `struct device_node` 208, `struct kset` 152,
`struct cpumask` 64, `ktime_t`/`pgoff_t`/`loff_t` 8, `kuid_t`/`kgid_t` 4.
Correctly dropped (unavailable in this config / header): acpi_handle,
cpu_stop_fn_t, ftrace_func_t, mempool_t, ….

**Re-bank delta: 131 → 153 banked (+22), 92 → 70 refused** — exactly the
census projection. 22 new parents unblocked (damon_sysfs_* families,
platform_device, tick_sched, file_ra_state, seq_file, …); 67 banked mirrors
now carry `opaque_probed` provenance. Export: 172 repr(C) types, compiles.

Soundness: a probed type is emitted as an alignment-matching blob of the
measured size; the license to emit is PRIMITIVE_SIZES membership (probing a
type IS the license — an unprobed opaque type still refuses). The blob anchors
the parent's downstream field offsets; the probe (in-kernel) is ground truth
for the size, and the parent's BUILD_BUG_ON re-certifies at transplant. The
host cc guard checks the generator's packing around the blob — the opaque
field's real size is anchored by probe + in-kernel gate, flagged
`opaque_probed` so that dependency is recorded, never hidden.

## Refusal backlog (census-order, remaining Run-3+ wideners)

After the opaque probe landed (70 refused remaining):

| count | class | widener |
|---|---|---|
| 12 | contains a union | tagged-union host layout (largest remaining) |
| 4 | bitfield | bitfield → explicit mask accessors |
| 4 | unparsable field | parser hardening (fn-ptr-in-typedef, multi-line) |
| 3 | non-CONFIG `#if` | expression evaluator beyond CONFIG symbols |
| ~6 | enum-as-field-type | `enum X` → i32 (kernel enums are int) |
| tail | fn-ptr typedefs, mempool_t, subsystem types | header-broadening in the probe |

The next highest-ROI widener is **`enum X` → i32** (~6, trivial and sound —
kernel enums are `int`) plus **union host-layout** (12, the biggest but needs
tagged-union care). Neither is a percentage-mover on its own; the lever now
shifts from wideners to provisioning workers (`dream/infra/`).

## How it plugs in

Consumers opt into config-pinning with `MIRROR_CONFIG=<.config>` (env
auto-load in mirror.py) — no per-harness edits. The bank is the ksdk crate's
mirror module; transplant weaves `ksdk_mirrors.rs` and the in-kernel guard
re-certifies. Rebuild the bank any time: `factory.py bank && factory.py export`.
