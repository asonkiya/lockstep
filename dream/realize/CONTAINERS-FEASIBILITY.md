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

---

## list_empty class DONE: +8 realized (T2 146/184, T3 96/131), 0 diverges

The first conditional sub-class (census above): every predicate a bare
`!?list_empty(expr)` — the one form expressible in list vocabulary, so BOTH
gate sides EXECUTE the real predicate (the C ref emits `if (!?list_empty)`
from the same parsed structure; sabotages never mutate the shared op dicts,
which would silently drop the reference's guard too). A two-phase probe
(populated arena, then drained self-looped arena) exercises both branches of
every guard; supported shapes: guarded-entry (is-linked test), guarded-pop
(`if (empty) return; e = first; del(e)` — FIRST resolves through the
`list_first_entry` local), op-free else branches, and the early-return-
before-flush loop guard.

Negative controls, measured:

| sabotage | verdict |
|---|---|
| flip_guard (wrong polarity) | DIVERGE |
| drop_guard on the POP shape | DIVERGE (phase 2: del of head->next on empty ≠ no-op) |
| drop_guard on guarded del_init | **MATCH — and correctly so**: `list_del_init` on a self-looped node is a no-op; the kernel's guard is an optimization, and a behavioral differential accepts semantically-equivalent translations |

Sub-denominator accounting (12 strict): **8 MATCH** (get_scpi_xfer, get_work,
bnxt_del_one_usr_fltr, get_next_rwi, __ordered_del_inode,
__kthread_cancel_work, padata_work_alloc, cxgb4_free_mps_ref_entries),
**3 banked-model defects** (net_unlink_todo guards the GLOBAL list's
emptiness where the C guards the node's own list_head — the ADT cannot
express per-node emptiness, so the model verified while describing a
different function; o2net_debug_del_nst's model is half comments;
ocfs2_resv_mark_lru has an extra mutator), **1 arena limit**
(fuse_free_dax_mem_ranges needs a second list_head per node). Refusal
taxonomy also sharpened: 8 T3 fns reclassified from conditional_loop_body to
plain_iteration_with_mutation (the use-after-poison class, visible now that
classification proceeds past the conditional check).

---

## tokf-equality class DONE: +7 realized (T2 147/184, T3 102/131), 0 diverges

The second conditional sub-class: a guard INSIDE the iteration body comparing
ONE cursor member against a loop-invariant token (`if (e->field ==|!= x)`
del/kfree on match — the `abx500_remove_ops` shape the ADT models express as
`if tokf(id, T_*) == a0 { del(id); retire(id) }`). Realization reads the REAL
member on both gate sides: the C ref compares `pos->payload` (the arena's
token field), the Rust side recovers the node via `offset_of!(Node, lh)`
container arithmetic and compares at the same member. The arena assigns
DUPLICATE tokens (`i % 3`) so delete-all, delete-none, delete-subset and
wrong-field all produce different chains, and the probe sweeps tokens
t=0..3 (three distinct match-subsets + a no-match value) plus a
drained-arena phase.

**Frozen sub-denominator (strict re-enumeration of the census's 22
"token equality" predicates, single-if iteration bodies only): 16 examined,
10 in the member-compare shape, of which 7 in-scope** (single `==`/`!=` of
`cursor->field` vs a call-free, cursor-free rhs; no break/continue/goto; no
else; every op inside the guard extent). **7/7 MATCH:**

- T2: abx500_remove_ops
- T3: iio_map_array_unregister_locked (reversed operands: `param == e->field`),
  mlx5_macsec_del_roce_gid, mei_cl_vtag_remove_by_fp, bnx2fc_free_vport,
  mem_cgroup_oom_unregister_event, dcbnl_flush_dev (model dialect
  `field(id, F_IFINDEX)` vs abx500's `tokf(id, T_DEV)` — both accepted by
  correspondence, which requires the model's T_*/F_* constant to NAME the
  same member the C compares, with the same operator; a model comparing a
  different member is the net_unlink_todo banked-model-defect family,
  refused by name)

The other 9: **3 break-variants refused** (`tok_guard:break_in_loop` —
iort_delete_fwnode, iort_deregister_domain_token,
pci_dev_res_remove_from_list: delete-FIRST-and-stop differs from delete-all
under duplicate tokens, so modeling break as absent would be exactly the
over-credit this arena was built to catch; next sub-class), 6 out-of-class
(truthiness+else = or_null, per-node list_empty, range compare, 3 multi-if).

Negative controls, measured on BOTH shapes (del-only abx500, del+kfree
dcbnl_flush_dev):

| sabotage | abx500_remove_ops | dcbnl_flush_dev |
|---|---|---|
| wrong_field (compare `id`, not the token member) | DIVERGE | DIVERGE |
| flip_guard (`==` -> `!=`) | DIVERGE | DIVERGE |
| drop_guard (unconditional del/kfree) | DIVERGE | DIVERGE |

Full census re-pass: **T2 147/184 MATCH** (164 front-accepted, 17
op_count_mismatch banked-model defects unchanged), **T3 102/131 MATCH**
(refusals now: 8 plain_iteration_with_mutation, 8 op_count_mismatch,
5 tok_guard, 5 conditional_body, 2 multi_head_iteration,
1 conditional_loop_body), zero unexplained diverges in either tier.
Remaining conditional classes: or_null truthiness (25), break-variants (3).

---

## Truthiness class DONE: +14 realized (T2 160/184, T3 103/131), 0 diverges

The third and last conditional sub-class (census row "truthiness null-guard,
25"). On contact the row split into TWO executable sub-shapes plus named
residue — the census-shrinkage law again, and the honest ladder is:

**25 (census 2026-08-07) → 18 bare-pointer-truthiness surviving at build
time** (7 absorbed by the list_empty/tokf builds and reclassifications) **→
14 in-scope and 14/14 MATCH** + 4 named banked-model refusals + 3 named
out-of-class.

**Sub-shape A — or_null pop (9/9 MATCH, all T2, all nonvoid):** `x =
list_first_entry_or_null(h, T, m); if (x) list_del[_init](&x->m); return x`
— disk_get_zone_wplugs_work, binder_dequeue_work_head_ilocked,
zram_select_idle_req, relay_dequeue_transaction, fdp1_dequeue_field,
__rtw89_ser_dequeue_msg, pci_bus_ops_pop, ssam_event_queue_pop,
sk_psock_link_pop. This is the census's predicted "new extraction ABI":
`realized_pop(head) -> *mut ListHead` (entry or null). The C ref executes
the REAL or_null expansion (`head->next != head ? entry : NULL`) plus the
real guard; the probe pops NN+2 times so the drained tail (empty → null →
no-op) is distinguishable from one-extra-pop, and the VALUE leg (returned
slot vs null sentinel) is compared on every pop alongside the chain
snapshot. Strictness: single binding, single del-class op on `&x->member`
inside the guard, member must match the or_null binding, `return x` present.
Zero while-pop drain loops exist in the population — the shape was NOT
built, the probe's repeated-pop drain covers the drain semantics.

**Sub-shape B — param null-guard (5/9 MATCH):** `if (entry) list_del(...)`
and `if (!p) return; ops` where the guarded pointer is a PARAM whose member
every op targets — qp_list_remove_entry, mcast_list_del, rproc_remove_rvdev,
led_remove_lookup (T2), xhci_debugfs_free_regset (T3, del+kfree under the
guard: the composed free-log rides inside the truthiness branch). The
predicate executes on BOTH sides against a real NULL: the probe adds a
null-param phase (`c_call(-1)`/`r_call(-1)` — entry = NULL), so the guard's
other branch is exercised for real, not modeled.

Negative controls, measured on both shapes:

| sabotage | pci_bus_ops_pop (A) | binder_dequeue (A) | qp_list_remove_entry (B) | xhci_debugfs_free_regset (B) |
|---|---|---|---|---|
| drop_guard | DIVERGE (k=6 drained: pops the HEAD, value leg -1≠-2) | DIVERGE (same) | CRASH (null deref in the null phase — an oops in situ) | CRASH |
| flip_guard | DIVERGE (k=0: null vs popped slot) | DIVERGE | DIVERGE (ops never fire on real entries) | DIVERGE |
| skip_first (off-by-one) | DIVERGE (k=0: pops slot 1, value leg 1≠0) | DIVERGE | — | — |
| del_not_init | — | DIVERGE (POISON −100 in the pop snapshot) | — | — |
| no_free | — | — | — | DIVERGE (freelog 2≠0) |

**The 4 banked-model refusals (named, never gated):**
`pnull_model:no_arg_sentinel_guard` — **acpi_scan_add_handler** (NEW
finding: previously realized in v1, audit-refused, now front-accepted again
— and the correspondence gate exposed that its model never guards the arg's
null sentinel, i.e. the model verified without a null case),
**nfp_port_free** and **nand_ecc_unregister_on_host_hw_engine** (both encode
the null check as a `tokf(...)` sentinel read of the id — a dialect the gate
cannot execute-and-correspond); **qp_list_add_entry** (op_count c=1,adt=2 —
the spurious-del family: model dels before push, and guards on `!= 0` where
the harness sentinel is −1). **Out-of-class, named:** arpc_del (member-FLAG
truthiness + member write), mmc_pwrseq_register (compound predicate),
pool_free (range compare).

Full census re-pass: **T2 160/184 MATCH** (180 front-accepted — pnull/ornull
moved 16 fns past the front gate — 18 op_count_mismatch = 17 pre-existing +
qp_list_add_entry newly exposed, 2 pnull_model; front refusals now only
3 conditional_body + 1 cross_list_move), **T3 103/131 MATCH** (104
front-accepted; refusals: 8 plain_iteration_with_mutation, 8
op_count_mismatch, 5 tok_guard, 3 conditional_body, 2 multi_head_iteration,
1 conditional_loop_body; 1 pnull_model = nfp_port_free), zero unexplained
diverges in either tier. **Containers realized: 263/344.**

Weave note: untouched this slice. All 9 pop fns are nonvoid — weave-
ineligible under the standing nonvoid rule regardless; the 5 pnull fns'
weave eligibility awaits the next denominator re-freeze.

Remaining conditional class: break-variants (4: iort ×2,
pci_dev_res_remove_from_list, kprobe_remove_area_blacklist —
delete-first-and-stop semantics). Banked-model worklist now 29 named fns:
26 op_count_mismatch across tiers (18 T2 incl. the qp_list_add_entry
spurious-del + 8 T3, the net_unlink_todo/o2net/ocfs2 findings among them)
+ 3 pnull_model (acpi_scan_add_handler, nfp_port_free,
nand_ecc_unregister_on_host_hw_engine).

## Banked-model repair (2026-08-09) — the 29-fn worklist dispositioned

Workload-first, per the audit discipline: the bank's defective models had
passed verification because the workload had measured holes. Every hole was
PROVEN (stored model passes the old workload, fails the strengthened one)
before any model was touched; the whole 344-model bank was then re-verified
and repaired to **344/344 behaviorally MATCHing + structurally corresponding**.

**The holes, measured (old → new on the stored models):**

| hole | proof fn | old | new |
|---|---|---|---|
| no NULL row: pnull models shipped DEAD `tokf(id)==-1` guards, reject path never exercised | acpi_scan_add_handler (+nfp_port_free, nand_ecc_unregister) | MATCH | KILLED (panic/DIVERGE on the null row) |
| fresh pool never held id 0: `id != 0` passed as a null check | qp_list_add_entry | MATCH | DIVERGE:adt (id-0 row: push skipped) |
| spurious `del()` before add: envelope-equal, NO workload can kill it (a linked node into bare `list_add` is caller-contract violation → corruption, not a differential) | esp_put_ent | MATCH | MATCH — caught by CORRESPONDENCE instead (op_count c=1,adt=2) |

Workload strengthening (`harness.py`): (1) one NULL row per null-GUARDED
pointer param (node/entry sentinel −1, token 0; unguarded fns get none —
fail-closed), with pool reservations so null rows never starve consuming
draws; (2) fresh pool = {0, 5, 6, 7}; (3) `linked(id)`/`linked_m(M_*,id)`
surface helpers — the faithful dialect for C `!list_empty(&node->member)`;
(4) DIVERGE:adt now prints both id-sequences (counterexample feedback; it is
what landed the last repair).

**Realizer dialect gaps — 10 of the 29 were NOT model defects:**
`adt_ops` could not parse `del_m(M_X, id)` (the multi-membership dialect the
surface itself generates) — the 8 T3 "op_count" fns (dca_free_domain,
__esw_qos_free_node, nxp_c45_secy_free, __team_option_inst_del,
ddebug_table_free, rio_mport_delete_db_filter, rio_mport_delete_pw_filter,
free_cg_rpool_locked) had CORRECT models all along. And `correspond` now
treats a C-side INIT_LIST_HEAD as optional on the model side (backtracking
alignment; the surface documents fresh-node/sub-anchor INIT as a no-op, the
older banked dialect renders it as `del` — both align; emission still emits
the INIT from the C, so the realized gate compares identical concrete ops)
— which cleared rdmacg_register_device (c=3) and response_list_add (c=2).
`_check_empty_consult` replaces the `no_empty_in_model` string check:
head-target guards still demand `empty(`; entry-target guards accept the
`linked`/`contains` dialect; a not_empty guard on a del-class op's OWN member
is canonically redundant (measured in the list_empty class) and needs no
consult.

**Bank-wide re-verify (all 344, strengthened workload + correspondence):**
323 clean on first pass; 21 fails = the 19 remaining worklist fns + **2 NEW
findings only the null rows could catch**: mmc_pwrseq_register (DIVERGE:ret —
model returns the wrong value for a NULL arg) and
nand_ecc_register_on_host_hw_engine (panic on NULL — the register twin,
previously on no list). Re-synthesis (Haiku, behavioral MATCH **and**
`model_check` correspondence in the loop): 21/21 repaired, **$0.078** total.
Final pass: **344/344 clean, zero exceptions, zero refused-by-name residue**.

Pipeline hardening: `overnight.py`'s container gate now runs
`container_realize.model_check` at synth time (a MATCHing but
non-corresponding candidate is refused with feedback), and its prompt carries
the parsimony / NULL-sentinel / linked-dialect rules — the bank cannot
re-accumulate this defect class. Re-verification driver:
`dream/container_adt/reverify.py` (`--resynth` to repair).

**Full census re-pass after repair (solo, 2026-08-09):** **T2 180/184 MATCH,
zero fails** (front refusals now only 3 conditional_body + 1 cross_list_move
— every op_count/pnull refusal converted); **T3 109/131 MATCH, zero fails**
(refusals: 8 plain_iteration_with_mutation, 5 tok_guard, 3 multi_member_ops,
3 conditional_body, 2 multi_head_iteration, 1 conditional_loop_body).
**Containers realized: 289/344** (was 263).

One finding the repair EXPOSED: the three multi-member del fns
(rio_mport_delete_db_filter, rio_mport_delete_pw_filter,
free_cg_rpool_locked — `list_del` through TWO different members + kfree) had
been accidentally shielded by the del_m parse gap; once front-accepted they
CRASHed the realized gate (the single-member arena collapses both dels onto
one probed offset → double-del → LIST_POISON deref). Now refused by name —
`multi_member_ops` — before the differential; the multi-member arena is a
future feature in the same family as multi_head_iteration. Their BANKED
models are correct and behaviorally verified (the ADT harness models
per-member lists natively); only realize/weave waits on the arena.

Worklist disposition, all 29 by name:
- **REALIZED via realizer dialect fixes alone (10):** dca_free_domain,
  __esw_qos_free_node, nxp_c45_secy_free, __team_option_inst_del,
  ddebug_table_free (del_m parse); rdmacg_register_device,
  response_list_add (INIT-optional correspondence) — models untouched;
  rio_mport_delete_db_filter, rio_mport_delete_pw_filter,
  free_cg_rpool_locked (del_m parse → bank-clean, realize-refused
  multi_member_ops as above).
- **REPAIRED by re-synthesis, now gate MATCH (19):** the 13 spurious-del
  (qp_list_add_entry, esp_put_ent, padata_work_free, barn_put_full_sheaf,
  klist add_tail, __bnep_link_session, xsk_map_sock_add,
  __dlm_mle_attach_hb_events, dfl_fpga_cdev_add_port_data,
  mtk_mdp_register_component, pinctrl_add_gpio_range, vduse_enqueue_msg,
  vduse_enqueue_msg_head); net_unlink_todo + o2net_debug_del_nst +
  ocfs2_resv_mark_lru (linked-dialect); acpi_scan_add_handler,
  nfp_port_free, nand_ecc_unregister_on_host_hw_engine (arg-sentinel null
  guards).
- **Plus the 2 NEW null-row findings, repaired:** mmc_pwrseq_register,
  nand_ecc_register_on_host_hw_engine.

## Coverage as a gate precondition (2026-08-09, generalization slice A)

The repair's three workload holes shared one root cause: the gate declared
MATCH for functions whose predicates were never exercised on both sides. That
class is now structural, not audited: the C reference TU carries per-op
execution counters and per-guard taken/not-taken counters (never reset across
the probe run), the probe dumps them after a clean run, and `run_gate`
REFUSES any MATCH whose report shows a single-polarity guard or a dead op —
`coverage:unexercised_branch:<pol>@<op>` / `coverage:dead_op:<op>`. A MATCH
now certifies *behaviorally equal AND every branch/op exercised*.

**The slice's negative control is the measured history itself**: the
pre-repair workloads, reconstructed via `run_gate(..., probe_flags=...)`
(PNULL_MODE=0 = no null row; COND_MODE=0 = no drained phase), are refused by
the coverage check ALONE on the very fns that once false-passed
(test_container_coverage.py). The loop-guard and tok rows are enforced too —
the flip_guard-no-op emission shape can never verify vacuously again.

Census re-pass with coverage armed: see container_census_t{2,3}.json
(persisted per-fn dispositions — the refusal ledger's feed; totals below).
Re-pass result (solo, 2026-08-09): **T2 180/184 MATCH, T3 109/131 MATCH,
zero coverage refusals, zero diverges** — the strengthened workload already
exercises every branch and op, exactly what the repair claimed; the coverage
gate now guarantees it stays that way for every future class.
