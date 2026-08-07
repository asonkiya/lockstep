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

---

## Full T2 pass: 163/181 chain-verified, and a finding about the BANKED models

`t2_census.py` runs the realizer's front gate over the entire T2 population and
(`--gate`) the chain-walking differential over every acceptee.

**Conditional loop bodies were NOT built — the census said not to.** The v1
front gate already accepts **181 of 184 (98%)**; the complete refusal set is
2 conditional bodies + 1 cross-list move. Building a condition parser would
have unlocked 2 functions while 181 sat unverified. Measured, then redirected.

**Two real abstraction gaps found and closed** (by understanding the model, not
by weakening the check):
- `INIT_LIST_HEAD` is a list op: re-initialising a node's `list_head` detaches
  it at the ADT level, and the models render it as `del`. The C scanner now
  recognises it (6 occurrences).
- `move_tail` ≡ `del` + `push_back`: either side may express a move either way,
  so both are canonicalised before comparison.

**Result: 163 / 181 chain-verified (90%), 18 refused.**

### The 18: the verified ADT models disagree with the C, structurally

The refusals are NOT gate failures — they are the correspondence check
reporting that the banked model and the real C describe different op sequences:

| shape | n | example |
|---|---|---|
| model has an EXTRA `del` before a push | **15** | `add_tail` (klist): C is a bare `list_add_tail`; model emits `del(n); push_back(...)` |
| model omits an op the C has | 2 | `response_list_add`, `rdmacg_register_device` |
| model duplicates a push | 1 | `net_unlink_todo` |

**Why this matters, and why the ADT oracle could not have caught it.** A
spurious `del` before a push is *behaviourally invisible* unless the workload
pushes a node that is already linked — which the container workload never does.
So these models passed their differential while over-specifying the function.
Realized naively they would emit `list_del` + `list_add_tail` where the kernel
writes only `list_add_tail`; on an already-linked node those differ, and on a
freshly-poisoned node the extra `list_del` is a wild-pointer write.

This is the third instance of the same theme (after `list_del`/`list_del_init`
and `_safe`/plain iteration): **the ADT abstraction hides exactly the axis that
matters when you make it real.** It is also the first instance where the defect
is in the *banked verified candidates* rather than in a translation step.

Nothing unsound reaches emission: ops come from the C, and disagreement is
REFUSED, tallied and named. The 18 are a re-verification worklist for the
container oracle (its workload needs a push-an-already-linked-node case), not
lost coverage.

> **CORRECTION (T3 audit, 2026-08-07): 163 → 139.** The conditional refusal
> lived only in the iteration path, so a straight-line body whose ops sit
> under an `if` (the pop-if-nonempty shape: `if (!list_empty(h)) { e = ...;
> list_del(...); }`) was accepted with its guard silently dropped — and the
> gate could not catch it, because the C reference is re-emitted from the same
> unconditionally-extracted ops. An audit during the T3 build found **24 such
> fns inside the 163** (plus 4 more hiding among the op-mismatch refusals, and
> 3 in T3's acceptees). Fixed fail-closed: `conditional_body` now refuses any
> straight-line body containing `if`/`?`, pinned by
> `test_conditional_straightline_refused`. Honest T2 numbers now:
> **184 = 139 chain-verified + 14 op-count (banked-model worklist) + 31
> conditional/cross-list refused.**

---

## T3 DONE: the retire/kfree class, verified by the COMPOSED gate

Census first (`t3_census.py`), per the T2 lesson. The population is clean:
**103/131 in scope** — 71 unconditional safe-iteration flushes + 32
straight-line del+kfree — and every single candidate frees with **one bare
`kfree(node)`** (129 bare + 2 double, both in the 2 multi-head fns; ZERO
`kfree_rcu`/`kvfree`/`kmem_cache_free` — the reach gate filtered them at
banking). So "compose with the allocator model" reduces to exactly one new
axis: **the free-event log**.

**The composed gate** (`container_realize.py`, same module — T2+T3 now unify):
`kfree` is a concrete op read from the real C, kept in order with the list
ops, class `retire`. It emits an *event*, not a memory op: the gate records
`(slot, chain-digest-at-free-time)` on both sides and compares the streams
order-sensitively after every call, on top of the full chain-walking
differential.

**The digest is the load-bearing addition.** Comparing free *slots* alone (the
ADT retire-log view) cannot see WHEN in the op sequence the free fired.
`kfree(p); list_del(&p->list)` — a use-after-free in situ — produces identical
chain states at call boundaries and identical freed slots; only the digest of
the chain *at the moment of the free* differs. Measured, not asserted
(`test_uaf_free_order_caught_structurally_only`):

| sabotage | composed gate | ADT retire-log view |
|---|---|---|
| dropped free (over-credit) | DIVERGE | DIVERGE |
| wrong free target | DIVERGE | DIVERGE |
| **free before unlink (UAF)** | **DIVERGE** | **MATCH — blind** |

Fourth instance of the theme, and the exact over-crediting failure this
document predicted for a list-only oracle — now structurally excluded.

**Fail-closed guards added:** `multi_head_iteration` (two `for_each` heads
must not collapse into one walk — the 2 `vhost_clear_msg`-shaped fns),
`free_target_mismatch` (the freed pointer must be the iteration cursor or the
list-op entry base), `free_arg_complex`, and every non-`kfree` allocator entry
point stays refused on sight.

### Result: 95/131 realized + composed-gate verified (73%), zero diverges

| outcome | n |
|---|---:|
| **MATCH (chain + free log)** | **95** |
| conditional loop body | 23 |
| op-count mismatch (banked-model worklist, same class as T2's) | 8 |
| conditional straight-line body | 3 |
| multi-head iteration | 2 |

**The measured next lever is now the condition-scope parser**: 56 fns across
both tiers (28+2 T2, 23+3 T3) refuse on conditionals — when T2 alone was
measured it was worth 2 fns; the T3 census re-priced it. The 8+14 op-count
mismatches are the banked-model re-verification worklist. Weave of realized
containers into the booting kernel remains the integration step after that.

---

## Conditional-class predicate census (measured 2026-08-07, pre-build)

The 56-fn conditional refusal class across T2+T3, by predicate form:

| form | n | what realization needs |
|---|---:|---|
| truthiness null-guard (`if (w)`) | 25 | model `list_first_entry_or_null` results — new extraction ABI |
| token equality (`e->dev == dev`) | 22 | the ADT `tokf` class: real-field reads in the gate's C ref |
| `list_empty` guard | 12 | closed list vocabulary — both gate sides can execute it |
| other (compound/range) | 3 | refuse |

**The honest finding: this is not a "condition parser."** The dominant shape
is pop-if-nonempty (`if (!list_empty(h)) { e = first; del; return e; }`) —
it needs a NEW gate shape (entry extraction + value return, `realized_pop()
-> entry`), plus conditional correspondence against the model's
`empty/first/tokf` vocabulary. Budget it as a build with negative controls
(guard-dropped and wrong-token sabotages must DIVERGE), sequenced:
list_empty class (12) first — smallest sound step — then tokf equality (22),
then or_null truthiness (25).
