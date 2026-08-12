# REPORT — conditional_body (6) — confirm/refute hygiene

Graded 2026-08-12. Verdict: **SUCCESS (3/3 bars)** — 0 realized, 6 refused-by-name, all sound.

## Sub-shape census

| fn | shape | refusal | sound? |
|---|---|---|---|
| arpc_del | flag-truthiness (`rpc->active`) + member WRITE | `conditional_body:non_list_empty_pred` | yes — member write out of vocab |
| mmc_pwrseq_register | compound 3-way null guard + early return, then add | `conditional_body:non_list_empty_pred` | yes — nested-member compound |
| nand_ecc_register_on_host_hw_engine | null-check + in-loop dedup early-return + add | `conditional_body:multi_guard` | yes — multiple guards |
| pool_free | range compare (`rx_pool_size>6`), list ops in BOTH branches | `conditional_body:else_branch_ops` | yes — else-branch ops |
| pcistub_device_id_add_list | found-flag + break + member writes + guarded add/free | `conditional_body:multi_guard` | yes — multiple guards |
| module_unload_ei_list | scalar-count preamble guard + clean tokf delete-all loop | `conditional_body:multi_guard` | yes (fail-closed); NEAR-MISS |

**Ladder: 6 census → 6 refused-by-name → 0 realized.** No shrinkage into realizable.

## The near-miss (module_unload_ei_list)

Its loop body is a CLEAN tokf-equality delete-all (`if (ent->priv == mod) {
list_del_init; kfree; }`, no break) — realizable in principle. The ONLY
blocker is the preamble `if (!mod->num_ei_funcs) return;`, which makes the fn
multi-guard. That preamble is an optimization (skip the loop when the count is
zero) whose behavior-equivalence depends on the invariant
`num_ei_funcs == #{ent : ent->priv == mod}` — state the arena does not model.
Refusing is therefore SOUND, not conservative: we cannot prove the preamble is
behavior-preserving. This is the scalar-count analogue of the already-realized
list_empty early-return preamble; a future "count↔list invariant" preamble
class would unlock it. Recorded, not forced.

## Note

mmc_pwrseq_register and nand_ecc_register_on_host_hw_engine had their BANKED
models repaired 2026-08-09 (null-return/panic dispositioned, bank 344/344
clean). That is orthogonal to REALIZATION: a clean bank model does not make the
C body transpilable — the realize gate still refuses these on their compound /
multi-guard structure. realized ≠ banked, as always.

## Regression pins

`test_conditional_body_packet_refused_by_name` (6 cases) locks each
refusal-by-name. Container totals unchanged (no realizer edit): 289/344.
