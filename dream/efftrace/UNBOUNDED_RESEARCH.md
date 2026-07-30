# Reaching the "unbounded" core — a research pass

Thesis (measured, not asserted): **"unbounded" is largely an artifact of the
STATIC view giving up, not fundamental unverifiability.** A sub-census of the core
tail (6,731 functions carrying an unbounded signal) shows only ~6% is the genuine
nondeterminism frontier; ~94% falls into three attackable shapes:

| sub-shape | share | count | technique |
|---|---|---|---|
| external_only | 37.9% | 2,551 | annotation table + whole-program call graph |
| alloc | 31.6% | 2,130 | allocator model in the recorder |
| container_op | 24.1% | 1,622 | ADT-level oracle + shared verified container core |
| reactive | 5.8% | 391 | record-the-environment (the genuine frontier) |
| iter_loop | 0.5% | 37 | concrete under a recorded workload |

## The unifying principle

The effect-trace oracle is record/replay under a workload. **Every CONCRETE
execution touches a bounded, recordable set — even when the STATIC footprint is
unbounded.** So the barrier isn't the runtime; it's that a static footprint pass
refuses. Three moves convert static-unbounded into concrete-bounded:

1. **Model the unbounded effect** (allocation) as a deterministic input, the way
   MMIO is modeled — then it's just more recorded state.
2. **Abstract the unbounded structure** (containers) to its ADT, verifying the
   OPERATION at ADT granularity instead of byte-by-byte over an arbitrarily large
   heap graph.
3. **Resolve the unknown** (external callees) with interprocedural facts /
   annotations, so "opaque" stops meaning "unbounded".

What it costs in assurance — stated honestly below — is a drop from *bit-exact
exhaustive* (the pure/bounded case) to *workload-coverage-gated* (fuzzing-grade,
coverage-governed) for the tail. Reaching the tail is real; the assurance tier is
lower, and the coverage gate is the governor that keeps it sound-for-what-it-covers.

## Technique per shape

### 1. container_op (24%) — ADT-family oracle + shared verified core
CONFIRMED by evidence pass (Haiku over kernel/mm/lib): the pointer-graph ops are a
**finite closed vocabulary of ~15–20 core mutations** — `list_{add,del,move,splice}`
(2,924 calls, dominant ~4:1), `hlist_*` (299), `rb_{insert,erase,link_node}` (108),
`xa_{store,erase}` (272), `idr/ida_{alloc,remove,free}` (122) — no ad-hoc
reimplementation. Each is backed by a **single shared verified core**:
`include/linux/list.h` + `lib/list_debug.c` (`__list_add`/`__list_del`),
`lib/rbtree.c` (`rb_insert_augmented`/`rb_erase`/`__rb_rotate_*`), `lib/xarray.c`
(`xas_store`/`xa_erase`), `lib/idr.c`, `lib/radix-tree.c` — exactly the gpio-mmio.c
shape, one core reused by thousands of callers. **Rust-for-Linux already ships the
rewrite targets**: `rust/kernel/list.rs` (`ListArc`/`ListLinks`),
`rust/kernel/rbtree.rs` (`RBTree<K,V>`), `rust/kernel/xarray.rs` (`XArray<T>`),
`rust/kernel/id_pool.rs`. Real core mutations are **bounded compositional sequences
of these API calls** (e.g. mm/interval_tree.c: exactly `rb_link_node` then
`rb_insert_augmented`; kernel/async.c: two `list_del_init`), not raw pointer
surgery — so they are recognizable/verifiable at ADT level. Oracle: record the
**ADT trace** (list as a sequence, rbtree/xarray as an ordered map), check the Rust
op produces the same ADT state; bounded because one op touches O(1)/O(log n) nodes.
**FAMILY lever: rewrite the container cores once (RfL already did) + verify each
caller's op sequence at ADT level.** Highest-value tail build.

NUANCE (from the same pass): container mutations are frequently bracketed by LOCKS
or use RCU variants (`list_add_tail_rcu`, `spin_lock_irqsave` around the op). So
container_op overlaps the `concurrent` class — full verification of those is "correct
ADT transition AND correct locking", i.e. the ADT oracle COMPOSED with the loom/
KCSAN concurrency gate, not the ADT oracle alone.

### 2. alloc (32%) — allocator model in the recorder
Allocation is a modelable EFFECT with a clean contract (`kmalloc(n)` -> a pointer
to n bytes, or NULL; `kfree` releases). Extend the effect-trace recorder with a
deterministic arena: `kmalloc` -> bump pointer (recorded), `kfree` -> tracked, the
returned region -> tracked state. Once the allocation is modeled, the function's
use of the memory reduces to the **bounded-footprint** case the effect-trace oracle
already handles. So alloc is not fundamentally unbounded — it's MMIO-shaped. Build:
an allocator model + treating alloc'd regions as recordable footprint.

### 3. external_only (38%) — annotation + whole-program call graph
These refuse only because a callee's body isn't in the corpus. Recover with:
(a) a **known-kernel-API purity/effect table** (memset/memcpy/ktime accessors/
string helpers — a few hundred entries cover the bulk), (b) **corpus completeness**
(inline helpers in headers), (c) **CGIR's function-pointer/macro-aware call_graph**
for edges the syntactic CALL scan misses. HONEST LIMIT: annotation only recovers
external callees that are ACTUALLY pure/bounded; some are genuinely unbounded and
merely looked external — so this recovers a FRACTION, not all 2,551. Pure
engineering, no new research.

### 4. iter_loop (0.5%) — concrete under a recorded workload
A loop over a list/array of unknown length: the per-iteration transfer is bounded,
and under a recorded workload the iteration count is CONCRETE, so replay handles it
directly (the trace has the real iterations). Already covered by record/replay;
negligible count anyway.

### 5. reactive (6%) — record-the-environment (the genuine frontier + residue)
`jiffies`/`ktime`/`random`/`get_cycles`/interrupt-driven reads. Technique: **record
the environment** — capture the nondeterministic inputs (clock reads, RNG draws,
IRQ arrivals) as part of the trace, so replay is deterministic against the recorded
environment. This works for verifying the TRANSLATION (same logic given the same
environment) but cannot certify behavior under environments the recording didn't
sample — the irreducible residue. This ~6% + the arch-asm floor is the honest
"stays hard / by-design" slice.

## The soundness ladder (the honest cost of reaching the tail)

| tier | class | assurance |
|---|---|---|
| exhaustive bit-exact | pure/leaf, struct-reader | strongest (differential over the whole input/footprint) |
| in-kernel exact | bounded_state effect-trace | strong (recorded footprint, ordered) |
| **coverage-gated** | **container/alloc/external tail** | **fuzzing-grade: verified over the exercised workload + ADT model; a coverage gate REFUSES un-exercised paths** |
| environment-gated | reactive | verified only against the recorded environment |
| by-design C | arch-asm, true nondeterm | unsafe Rust / stays C |

The move down the ladder is the price of reach. The coverage gate (refuse closes
whose workload didn't exercise the branch/state) is what keeps the tail sound — the
same path-coverage discipline structdiff already uses, applied to state/workloads.

## Revised reach (honest, coverage-caveated)

With existing + prototyped + effect-trace + these tail techniques:
- container-family core + ADT oracle: recovers much of the 24%
- allocator model: recovers much of the 32%
- annotation/corpus: recovers a FRACTION of the 38% (only the truly-bounded ones)
- residue: ~6% reactive + arch floor + the genuinely-unbounded-through-external

So the core plausibly climbs from ~48% (bit-exact/strong) toward **~85–90%
addressable**, but with the upper band at **coverage-gated (fuzzing-grade)
assurance**, not proof — and a hard **~6–10% by-design residue**. The honest
headline: the tail is REACHABLE (your intuition holds for ~94% of it), at a lower
but still-sound assurance tier, governed by workload coverage.

## Ranked research roadmap

1. **Container-family ADT oracle + rewrite the shared cores** (24%, family lever,
   highest value; RfL abstractions are the target) — build order like gpio-mmio.
2. **Allocator model in the effect-trace recorder** (32%, mechanically closest to
   the proven MMIO recorder).
3. **Known-kernel-API effect/purity annotation table + CGIR whole-program call
   graph** (recovers a fraction of 38%, pure engineering).
4. **Coverage gate for state/workload** (the soundness governor for all of the
   above — must land alongside, not after).
5. **Record-the-environment** for the reactive 6% (frontier; diminishing returns).

Each is gate-arbitrated (0 false passes preserved); the sub-shape says which
technique APPLIES, not that synthesis always succeeds.
