# Interprocedural footprint closure — the real effect-trace reach (honest)

`python3 dream/efftrace/interproc.py` resolves each `bounded_state` function's
footprint transitively over its call graph (corpus of 38,400 core function bodies):

```
bounded_state (router):        14,233
  BOUNDED (effect-trace):       1,230   (8.6%)   <- interprocedural, was 729 syntactic
  unbounded (genuine):          7,664   (53.8%)  <- transitively touches graph/alloc
  unresolved (external callee):  5,339   (37.5%) <- callee body not in corpus
```

## The correction I owe (this is the important part)

I quoted this class's effect-trace reach as **35.5%** (kind-based), then guessed the
interprocedural closure would recover it "up to ~13k." **Both were too optimistic.**
The closure recovered **729 → 1,230** (+501, +69% over syntactic) — real, but not
13k. Following the calls shows **7,664 (54%) are GENUINELY unbounded**: a
bounded-*looking* function very often calls into pointer-graph/alloc machinery a
step or two down. My "these are just small pure helpers" intuition was wrong for
the majority. Another 5,339 are unresolved (external callee not in the corpus).

## The honest core rollup, revised

| bucket | share of 40k core | status |
|---|---|---|
| pure_leaf + struct_reader + mmio | ~31% | production oracle NOW |
| concurrent | ~14% | prototyped (loom/KCSAN, needs scaling) |
| **effect-trace (bounded_state, interprocedural)** | **~3%** (1,230) | oracle proven; this is the REAL reach, not 35.5% |
| unbounded tail (unbounded_state 15% + the ~13k unbounded/unresolved bounded_state ~32% + arch_asm 4%) | **~52%** | genuine hard tail + floor |

So the effect-trace oracle moves the core from ~45% to **~48%** addressable — a
**+3pp**, not the +36pp the kind-based number implied. **~52% of the core is a
genuine hard tail** (transitively-unbounded state + arch floor), needing the
in-kernel differential-under-workload, not the effect-trace oracle.

## What the wiring was still worth

- It produced the **sound** number: 7,664 functions are now correctly routed to
  the hard fallback instead of *falsely* claimed for the effect-trace oracle. That
  is the honest-broker win even though the total dropped.
- +501 real recoveries (69% over syntactic) — interprocedural analysis genuinely
  helps; it just isn't a silver bullet.

## Remaining recovery levers (bounded, not dramatic)

- The **5,339 unresolved** need corpus completeness (inline helpers in headers) +
  a known-pure/bounded kernel-API annotation table (memset/memcpy/ktime helpers).
  Some fraction is recoverable; expect it to lift 1,230 into the low thousands,
  not tens of thousands.
- CGIR's own `call_graph` (function-pointer / macro-aware edges) would sharpen the
  callee set vs the syntactic CALL extraction used here.

## Bottom line

The entangled core is **~48% auto-rewritable** with existing + prototyped + the
effect-trace oracle — substantial, nearly half — but **not ~81%**. The honest wall
is the transitively-unbounded state (~52%), which is the in-kernel-differential /
research frontier. The effect-trace oracle is a real ~1–2k-function tool, not the
core-majority unlock I overstated.

## Summit 3.1 — the opaque-callee discharge measurement (2026-08-11)

Run per `PREREG-INTERPROC31.md` (bars frozen at 1e8426a; measured by
`interproc31.py`; baseline reproduced bit-identical: 1,230 / 5,339 / 7,664).

**M1 (attribution):** 5,223 of the 5,339 unresolved fns attribute to 4,969
distinct missing callee names (116 = depth-cap only). Zipf confirmed: the
top-100 names touch **57.3%** of blocked fns. The head is two classes:
header inlines (atomic_read, test_bit, rcu_*) and **macros** (WARN_ON*,
IS_ENABLED, ARRAY_SIZE, pr_*) — macros are invisible to ANY non-preprocessing
parser, ours or CGIR's.

**Validator (the control):** poison entry `kmalloc`-annotated-pure was CAUGHT
(pointer-graph/alloc). 43 draft entries dropped fail-closed on footprint
disagreement — including test_bit/atomic reads whose header bodies route into
debug-instrumentation branches; the strict self-consistency contract keeps
them out even though their real semantics are pure. Shipped: 39 pure +
21 diag-tier (diag = bounded side effects outside the modeled state — printk,
WARN-once flags, RCU brackets — a documented assurance-tier claim, reported
as its own ladder step, never silently merged).

**M3 (the ladder):**

```
bounded 1,230 -> +header corpus (19,394 bodies)  1,496  (+266)
             -> +pure annotations                 1,605  (+109)
             -> +diag-tier annotations            2,037  (+432)
```

Header completeness also moved 867 fns unresolved->**unbounded** — corpus
completeness mostly reveals real unboundedness (census-shrinkage law).
Secondary: 21 baseline-UNBOUNDED rerouted to bounded (expectation was ~none;
cause: annotations prune instrumented callee paths) — recorded.

**(c) the CGIR marginal:** of the top-100 post-(a)(b) missing names: **66
macros** (equally opaque to CGIR's tree-sitter ingest — no preprocessing),
21 parser-gap/scope, 13 no-definition. Force-annotating the entire gap bucket
pure — a deliberately generous ceiling, since its members include atomics and
rwsem ops that would really resolve unbounded — yields **+89**. CGIR indexes
3/5 of a sampled gap subset (reachability confirmed), but the ceiling is the
story: **(c) <= +89, ~10-24% of the total — far below the >= half share the
prereg requires for wiring.**

**Verdict vs the frozen bars (newly-bounded, baseline 1,230):**

| contract | (a)+(b) | +(c) ceiling | grade |
|---|---|---|---|
| strict (pure table only) | +375 | <= +464 | **PARTIAL** |
| with diag tier | +807 | <= +896 | nominal SUCCESS, by 7 — only under the relaxed tier |

Per the pre-registered PARTIAL rule: **ship the annotation table alone; CGIR
wiring DECLINED (LEVER-THIN)** — its marginal is bounded at +89 and the macro
wall blinds it exactly as it blinds us. The prereg's closing question is
answered: **CGIR stays a separate product, honestly retired from the dream's
critical path**; the contract-as-oracle lesson closes (LESSONS.md).

Bounded ≠ realized: these ~375–807 fns are ROUTING recoveries — they still
need harvest + zero-trust verification before they bank. funnel.json is
deliberately untouched by this measurement.
