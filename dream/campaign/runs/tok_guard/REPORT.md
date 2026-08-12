# REPORT — tok_guard (5) — confirm/refute hygiene

Graded 2026-08-12. Verdict: **SUCCESS (3/3 bars)** — 0 realized, 5 refused-by-name, all sound.

## Sub-shape census (the name was a bucket)

| fn | shape | refusal | sound? |
|---|---|---|---|
| iort_delete_fwnode | tok-eq (`iort_node==node`) + **break** | `tok_guard:break` | yes — delete-first-and-stop |
| iort_deregister_domain_token | tok-eq (`translation_id==trans_id`) + **break** | `tok_guard:break` | yes — delete-first-and-stop |
| pci_dev_res_remove_from_list | tok-eq (`res==res`) + **break** | `tok_guard:break` | yes — delete-first-and-stop |
| iavf_clear_cloud_filters | flag-truthiness (`cf->add`) + **else** member-write (`cf->del=true`) + counter-- | `tok_guard:else_branch` | yes — out of ADT vocab |
| kprobe_remove_area_blacklist | compound range (`<start \|\| >=end`) + **continue** | `tok_guard:break` | yes (fail-closed); reason imprecise |

**Ladder: 5 census → 5 refused-by-name → 0 realized.** No shrinkage into realizable — the expected hygiene outcome.

## Soundness argument (why the break-variants stay refused)

The three break-variants delete the FIRST matching entry and stop. The tokf
arena deliberately assigns DUPLICATE tokens, so delete-first ≠ delete-all:
modeling a break-variant as the delete-all ADT op would DIVERGE on the arena
(the existing `test_tok_dropped_guard_diverges` is exactly this negative
control — unconditional/over-deletion is caught). The refusal is therefore
load-bearing, not conservatism. Realizing them faithfully would require
modeling delete-first-and-stop as a distinct op-sequence proven equal on both
sides under duplicates — a FEATURE, out of hygiene scope; deferred, not forced.

## One finding (imprecise reason, sound refusal)

`kprobe_remove_area_blacklist` refuses on `tok_guard:break` because of its
`continue`, but that `continue` is semantically benign (skip-if-outside-range
= delete-all-in-range, no early stop). The true blocker is the compound range
predicate `ent->start_addr < start || ent->start_addr >= end`, out of the
single-equality tokf vocabulary. The refusal is sound (fail-closed) but the
name under-describes the root cause. Not fixed this slice: reordering the
`_parse_tok_guard` guard checks risks the byte-identical guarantee for other
fns, and it changes no count or soundness — noted for a future precision pass.

## Regression pins

`test_tok_guard_packet_refused_by_name` (5 cases) locks each refusal-by-name.
Container totals unchanged (no realizer edit): T2 180/184 + T3 109/131 = 289.
