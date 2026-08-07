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

---

## Step 2 DONE: the `list_head` mirror + faithful ops + chain-walking gate

`dream/container_adt/listmirror.py` (tests: `test_listmirror.py`, 7).

**Mirror.** `#[repr(C)] ListHead { next, prev }` with LOAD-BEARING pointer
fields (the general mirror generator treats pointers as opaque blobs; here they
are the thing under test), layout probed in-kernel — `size=16, next@0, prev@8`,
node `container_of` offset probed too — and pinned by rustc const-asserts so a
drift fails the compile.

**Config-dependence was real.** `LIST_POISON1` is **not** the header's `0x100`:
arm64 defconfig sets `CONFIG_ILLEGAL_POINTER_VALUE=0xdead000000000000`, so the
real values are `0xdead000000000100/122`. Probed from the volume's `.config`;
a test asserts the delta is applied (assuming the literal would have made every
poison comparison silently wrong). `CONFIG_DEBUG_LIST` is off, so the
`__list_add_valid`/`__list_del_entry_valid` hooks are the no-op forms.

**Ops** are transcribed from `include/linux/list.h` write-for-write **and in
the same order** (`next->prev`, `new->next`, `new->prev`, `prev->next`), incl.
`list_del`'s poisoning vs `list_del_init`'s re-initialization.

**The gate** drives the real C inlines and the Rust ops through an identical op
script over separate arenas, comparing after EVERY op: forward chain, backward
chain, and every node's raw next/prev, with pointers normalized to arena
indices so the comparison is address-independent. Walkers never dereference a
non-arena pointer (a corrupted chain is recorded as data, not a segfault).

Result — 4/4, and the strictly-stronger property is **measured inside the
gate**, not asserted:

| variant | ADT-only view | structural |
|---|---|---|
| correct | MATCH | MATCH |
| forward_only (prev not fixed) | DIVERGE | DIVERGE |
| **no_poison** | **MATCH** | **DIVERGE** |
| add_wrong_side | DIVERGE | DIVERGE |

**Honest correction to this document's earlier expectation.** We predicted
*chain corruption* would be the ADT-invisible class. Measurement says
otherwise: `forward_only` is caught by the ADT view too, because a later
`list_add_tail` reads `head->prev`, so backward-chain corruption propagates
into forward order. The class the structural oracle uniquely catches is
**unlink-without-poison** — perfect membership and order, wrong node state.
Chain corruption tends to become observable once any op reads `prev`; poison
state never does.

**Next (step 3):** emit realized container functions against this mirror
(`del`→`list_del`, `push_back`→`list_add_tail`, `iter`→chain walk with
`container_of`), gated by this differential; then T3 only after composing with
the allocator model.

---

## Step 3 DONE: realized container functions, chain-verified

`dream/container_adt/container_realize.py` (tests: `test_container_realize.py`, 7).

**The design rule step 2 forced.** The ADT model renders `list_del` and
`list_del_init` as the SAME abstract op (`del`) — they differ only in the
removed node's state, exactly the axis step 2 measured the ADT view blind to.
So realization takes the **concrete op sequence from the real C** and uses the
verified ADT body only to *check correspondence*. The model says what the
function does abstractly; the C says which kernel primitive to emit.

Pipeline: parse real C → ordered concrete ops · parse verified model → ordered
abstract ops · require 1:1 same-order same-class correspondence (else REFUSE,
fail-closed) · emit Rust over the `ListHead` mirror · gate with the
chain-walking differential (real C vs realized Rust over an arena).

**Result: 8 real kernel functions realized and chain-verified, 0 failures**,
2 refused fail-closed (function not found in this tree):

| function | ops |
|---|---|
| `adf_service_add` / `adf_service_remove` | `list_add` / `list_del` |
| `response_list_add`, `acpi_scan_add_handler`, `__clkdev_add` | `list_add_tail` |
| `__dma_buf_list_add` | `list_add` |
| `dm_cache_policy_unregister`, `get_work` | `list_del_init` |

**The headline, on a real function.** Emitting `list_del` where
`dm_cache_policy_unregister` writes `list_del_init`:

    structural oracle = DIVERGE      ADT-only oracle = MATCH

i.e. a defect with perfect list membership and order, wrong node state —
invisible to the abstract oracle, caught by the chain-walking one. That is the
concrete payoff of steps 2–3 and the reason realization reads the C.
The gate is otherwise load-bearing too (`wrong_op` → DIVERGE).

**v1 scope** (all refusals tallied, never guessed): single list, straight-line,
no allocation. Refused: `_rcu` variants, splice/cut/replace/swap/rotate,
`hlist_`, iteration, and anything containing `kfree` — T3 routes to the
allocator model, not a list oracle.

### Iteration support (`list_for_each_entry[_safe]`)

The `safe` vs plain distinction is **load-bearing and, like `list_del` vs
`list_del_init`, invisible to the ADT model** (whose `iter()` returns a
snapshot, i.e. always `_safe`-like). `list_for_each_entry_safe` caches the next
pointer BEFORE the body, so a body that unlinks `pos` is sound; plain
`list_for_each_entry` reads `pos->next` AFTER the body — which, if the body
unlinked `pos`, reads `LIST_POISON1`. So the emitted walk shape is taken from
the C, and plain-iteration-with-mutation is REFUSED outright (we will not emit
a use-after-poison even if asked).

**Batch: 11 real functions realized + chain-verified, 0 failures, 4 refused
fail-closed** — now including three `_safe` iteration functions
(`mlx5_fw_tracer_clean_ready_list`, `stub_priv_pop_from_listhead`,
`qedi_cleanup_active_cmd_list`).

Three load-bearing controls, all on real functions:

| control | verdict |
|---|---|
| `wrong_op` (list_add ↔ list_add_tail) | DIVERGE |
| `del_not_init` on `dm_cache_policy_unregister` | **structural DIVERGE / ADT MATCH** |
| `unsafe_iter` on `mlx5_fw_tracer_clean_ready_list` | **CRASH** (signal 11, wild-pointer deref) |

The last is the iteration analogue of the poison finding: emitting the plain
walk over a deleting body dereferences `LIST_POISON1` — a segfault in the
harness, a kernel oops in situ. The gate classifies a crash or a hang as a
REJECTION, never as UNKNOWN and never as a pass.

**A scope violation the gate caught, then made static.** `dev_exceptions_move`
HUNG: it iterates `orig` and moves to `dest` — genuinely two lists, which v1's
single-list harness collapsed into a self-move that never terminates. Now
refused statically (`cross_list_move`) by comparing each `list_move*`
destination against the iterated head, rather than discovered by timeout.

**Refusal classes (all tallied, never guessed):** `_rcu`, splice/cut/replace/
swap/rotate, `hlist_`, allocation (`kfree` → T3), conditional loop bodies,
cross-list moves, unsupported iteration forms.

**Next:** conditional loop bodies (`if (pred) list_del(...)` — the
`abx500_remove_ops` shape) to widen iteration coverage; then T3 composed with
`dream/allocmodel`; then weave realized containers into the booting kernel.
