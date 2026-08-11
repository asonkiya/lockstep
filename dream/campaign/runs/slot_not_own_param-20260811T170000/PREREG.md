# PREREG — slot_not_own_param (cross-slot) — handle-alias resolution

Frozen 2026-08-11T16:44:18, BEFORE the run. Graded in REPORT.md.

**Frozen denominator:** 23 census -> 22 alias (in-scope) + 1 slot-arithmetic (refused) -> target 22 MATCH

## Blind bars

- **realize-22**: 22/22 in-scope (alias) fns realize to MATCH via the host efftrace differential
- **zero-diverge**: zero unexplained diverges in the full realize census re-pass
- **priors-nondecreasing**: efftrace realized total is non-decreasing (was 583/635); no prior MATCH regresses
- **negctl-crossnode**: a compile-clean cross-node misroute on a 2-node alias fn DIVERGES (measured, not BUILD_FAIL)
- **arith-refused**: the 1 slot-arithmetic fn stays refused by name (slot_handle_arithmetic), never realized

## Required negative controls

- cross-node misroute: resolve a node alias to a DIFFERENT node param's pointer on a 2-node fn (ds35xx_ooblayout_free / w35n01jw_ooblayout_free) -> must DIVERGE, compile-clean
- the slot-arithmetic case (Huion...bpf.c:probe, `a0 + F0_RDESC_SIZE`) must remain refused

## Sub-shape census (frozen)

The class name is a hypothesis; on contact the 23 split into:

- **22 handle-alias**: `let NAME = aK;` binds the node handle to a readable local, then `field(F.., NAME)` uses the local as the slot. `NAME` resolves to the node param's OWN slot — trivially sound (immutable `let`, no shadowing, no `let mut`). Fix = resolve the alias, then strip the consumed binding (it references the unbound node handle aK and often collides with the real pointer param name).
- **1 slot-arithmetic** (`drivers/hid/bpf/progs/Huion__KeydialK20-Bluetooth.bpf.c:probe`): `a0 + F0_RDESC_SIZE` is address arithmetic on the handle — genuine computed addressing, refused by name.

## Soundness

Alias resolution preserves every genuine refusal: a slot resolving to a scalar's or a foreign node's slot still `!= node_slot[pi]` -> refused. No in-scope fn has a VARYING slot index (all resolve to one constant node handle), so the coverage-gate '>1 index value' concern is vacuous here — it is exactly the refused arithmetic case that would have needed a driven index. Both differential sides deref the same real pointer, so aliasing is identical by construction.
