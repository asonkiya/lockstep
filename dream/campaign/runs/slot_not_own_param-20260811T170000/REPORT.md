# REPORT — slot_not_own_param (cross-slot) — handle-alias resolution

Graded 2026-08-11 against the frozen PREREG. **Mechanism + soundness SUCCESS;
one bar PARTIAL (21/22), the single miss characterized and non-soundness.**

## Sub-shape census (the class name was a hypothesis)

`slot_not_own_param` fires when a `field(F<pi>_X, slot)` helper's slot token is
not the param's own canonical `a{k}`. The name suggested foreign/cross-node
access; on contact the 23 split:

| sub-shape | n | disposition |
|---|---|---|
| handle-alias (`let rqd = a0; ... field(F0_X, rqd)`) | 22 | in-scope -> realize |
| slot arithmetic (`field(F0_RDESC_SIZE, a0 + ...)`) | 1 | refused: slot_handle_arithmetic |

## Ladder

    census 23 -> 22 alias (in-scope) + 1 arithmetic (refused)
             -> 21 MATCH + 1 BUILD_FAIL_RS

- 21 MATCH (host efftrace differential, zero-trust re-gate over the full 635).
- 1 BUILD_FAIL_RS: drivers/fpga/dfl-fme-perf.c:fabric_event_destroy — the alias
  resolved correctly, but its struct field is named `priv`, a Rust *reserved*
  keyword with no valid `r#priv` raw form. Pre-existing emission limit the
  resolution merely exposed; reassigned to the BUILD_FAIL_RS tail (16->17), not
  forced. Not a soundness event.
- 1 refused: Huion__KeydialK20-Bluetooth.bpf.c:probe -> slot_handle_arithmetic.

## Aliasing soundness

Immutable `let NAME = aK;` means NAME === aK for its whole scope. The transpiler
resolves the alias then STRIPS the consumed binding (un-stripped it keeps the
unbound handle aK alive -> handle_arg_used_as_value, and since NAME usually
equals the real pointer param name it would shadow the *mut Mirror param with an
i64). Fail-closed guards preserved: `let mut` / shadowed aliases never resolve;
an alias used as a VALUE -> handle_alias_used_as_value; an alias resolving to a
scalar's or foreign node's slot still != node_slot[pi] -> refused. Both
differential sides deref the SAME real pointer. Engagement is gated to fns that
use an alias in slot position, so every prior candidate is byte-identical.

## Blind bars

| bar | pre-registered | measured | verdict |
|---|---|---|---|
| realize-22 | 22/22 in-scope MATCH | 21/22 MATCH; 1 -> BUILD_FAIL_RS (`priv` keyword) | PARTIAL |
| zero-diverge | zero unexplained diverges | 0 diverges over 635 | PASS |
| priors-nondecreasing | efftrace non-decreasing, no MATCH regresses | 583 -> 604 (+21), 0 regressions | PASS |
| negctl | cross-node misroute DIVERGES, compile-clean | premise-corrected; load-bearing control DIVERGES | PASS |
| arith-refused | slot-arithmetic fn stays refused by name | slot_handle_arithmetic, never realized | PASS |

negctl note: a cross-NODE misroute is not compile-clean — the two node params
are distinct #[repr(C)] mirror types, so a wrong-node deref is a TYPE error,
caught before the differential (stronger than divergence). The measured control
instead corrupts an alias-resolved store by +1 on pcpu_block_update
(compile-clean) -> DIVERGE. Both pinned in test_realize.py.

## Funnel / ledger

- efftrace realized 583 -> 604 (funnel.json, provenance 2026-08-11).
- ledger regenerated: slot_not_own_param EXTINCT; new top-5: BUILD_FAIL_RS 17,
  unknown_const_token 11, plain_iteration_with_mutation 8 (research),
  tok_guard 5, conditional_body 3.

## Discipline

Full census re-pass solo (playbook harness), logs to file, resumable (survived a
2-min wrapper SIGTERM with no duplicate keys). First PARTIAL on this lever, but
the lever is COMPLETE: the class is extinct and the 1 residual is a named tail
entry, not a retry candidate. $0 model spend.
