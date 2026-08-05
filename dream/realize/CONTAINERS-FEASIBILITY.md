# Containers realization — feasibility census (measure before building)

The container class (344 verified candidates) is the largest unrealized bank
after effect-trace. Before building its model→real step we measured what the
verified bodies actually *do*, the same discipline that repeatedly stopped this
project from grinding a dead lever. Census script:
`~/.claude/jobs/*/tmp/container_feas.py`; result:
`dream/realize/container_feasibility.json`.

## Result: containers is a REAL build, not a mechanical extension

| tier | meaning | n | share |
|---|---|---:|---:|
| **T2_SIMPLE_MUT** | single list, needs real `list_del`/`list_add` pointer surgery | **184** | 53% |
| **T3_RETIRE** | uses `retire()` (kfree) — allocation lifetime, must compose with the allocator model | **131** | 38% |
| T0_NO_ADT | no container op in the body | 15 | 4% |
| T4_MULTI | 2+ distinct lists, or token-field writes | 9 | 3% |
| **T1_PURE_READ** | reads only — realizable with the EXISTING reader machinery | **5** | 1% |

Operation frequency: `del` 211, `retire` 131, `iter` 108, `push_back` 76,
`push_front` 31, `tokf` 26, `empty` 23, `set_field` 19, `first` 18.

## What this says

**The effect-trace shortcut does not transfer.** Effect-trace realized
deterministically (75.6%) because its cell model was a *flat field table* whose
indices came from real struct fields — a rename away from real access. The
container model is an **abstract ADT** (`{list_id -> [node_id]}`): node
identities are indices into an arena, not addresses, and `del(id)` carries no
information about which `list_head` field to unlink or where the node's
`container_of` offset is. Realization must *reconstruct* pointer structure the
model deliberately abstracted away.

**Only 5 of 344 (1%) realize with machinery we already have.** Those are
read-only shapes (`empty`/`first`/`tokf`) that behave like readers.

**The critical path is `del`.** 211 of 344 bodies mutate a list, and the
dominant primitive is unlink. So the build is, in order:

1. A **`list_head` mirror** with real `prev`/`next` pointer fields (the existing
   mirror generator treats pointers as opaque; here they are load-bearing) plus
   the node's `container_of` offset, probed in-kernel like every other layout.
2. Faithful **`list_del`/`list_add`/`list_add_tail`** emission (the real kernel
   inline semantics: `__list_add(n, prev, next)` and the poison-free unlink used
   in-tree).
3. A **structure-level differential**: the current container oracle compares
   *ADT sequences*; a realized function must be compared by walking the REAL
   pointer chain on both sides, so a translation that produces the right
   membership with a corrupted chain is rejected. The structdiff arena harness
   already walks real structs and is the right host for this.

**T3 (38%) is gated on composition, not on lists.** `retire()` is `kfree` — a
lifetime effect. Verifying it soundly means composing the container oracle with
the allocator-init model (already built, `dream/allocmodel`), not extending the
list oracle. Attempting T3 with a list-only oracle would reproduce exactly the
over-crediting failure the purity router was built to prevent (right membership,
dropped free).

## Recommendation

Containers realization is worth building — 184 T2 candidates is a large,
well-defined population, and the mechanism (mirror → real ops → structural
differential) is a known shape. But it is a **multi-session build with a
research edge** (faithful intrusive-list semantics + a chain-walking oracle),
**not** the deterministic transpile that effect-trace turned out to be. Budget
it as such; do not promise it as an extension of `realize.py`.

Sequencing suggestion, cheapest-first:
1. The 5 T1 candidates through the existing reader path (hours, proves the
   plumbing end-to-end).
2. The `list_head` mirror + `list_del`/`list_add` emission + chain-walking
   differential, proven on a handful of T2 candidates with a negative control
   (corrupt the chain, oracle must reject).
3. Batch the remaining T2.
4. T3 only after composing with the allocator model — and route it there
   explicitly rather than letting a list-only oracle certify a `kfree`.
