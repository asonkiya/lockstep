# The synth ladder, wired and measured — c2rust → local 14B → Haiku

COSTDOWN §3 proposed the escalation ladder; localbench supplied the routing rule
(escalate after 2 — small models loop on what they don't understand). This wires it
into one loop (`ladder.py`), gate-arbitrated by hostdiff at every step, and runs it
on the 8-function battery (the 7 boot-verified fleet fns + intlog2).

Rungs: **0** c2rust 0.22.1 (deterministic transpile of the shimmed TU, $0) → **1**
qwen2.5-coder:14b via ollama (≤2 attempts, $0) → **2** Haiku 4.5 API (≤2 attempts,
cost-ledgered). Setup cost: brew llvm@21 + cmake, `cargo install c2rust` (LLVM 22
is NOT supported — `ElaboratedType`/`getTypeForDecl` removed; the research's 18–21
window is real). The transpile trick: c2rust needs a standalone-compiling TU, and
**hostdiff's shimmed TU already is one** — the same artifact feeds the oracle and
the transpiler.

## Result

```
gcd              -> c2rust   $0.0000   0.8s   MATCH
int_sqrt         -> c2rust   $0.0000   0.7s   MATCH
int_pow          -> c2rust   $0.0000   0.7s   MATCH
__sw_hweight32   -> c2rust   $0.0000   0.7s   MATCH
__sw_hweight64   -> c2rust   $0.0000   0.7s   MATCH
lcm              -> c2rust   $0.0000   0.9s   MATCH   (cross-TU dep: gcd)
lcm_not_zero     -> c2rust   $0.0000   0.9s   MATCH
intlog2          -> c2rust   $0.0000   0.7s   MATCH   (256-entry table)

LADDER: 8/8 | c2rust 8 | local 0 | haiku 0 | unsolved 0 | spend $0.0000
```

**Rung 0 swept the battery.** Every function bit-identical to the real kernel C
(500k differential cases each), ~6 s of wall-clock for all 8, zero tokens. The
functions that stalled the LLM rungs are exactly the ones the transpiler can't
miss: `gcd`'s `r & -r` and `int_sqrt`'s width mixing are *semantics* (a transpiler
models them; a model must remember them), and `intlog2`'s table is *data* (a
transpiler copies it; a model transcribes it, badly). Meanwhile the whole-module
transplant keeps helpers private and exports only `cgir_<fn>` — no link collisions
with the C, extern imports bind to the dep TU.

Caveats, honestly: n=8, one family (pure lib/math + bit ops) — the C-semantics-
heavy class where transpilers shine; c2rust output is unsafe, unidiomatic Rust,
which is acceptable *by design* for the unsafe-first ratchet (design.md §4.1) — the
LLM rungs turn into the **refinement** engine (idiomatic/safe Rust later), not the
coverage engine; and in-kernel the shim grows per header family (asm-goto-heavy
core TUs stay out of rung 0's reach — they escalate).

## Cost estimation, all three levels (measured routing, 2026-07-27)

Measured unit costs: rung 0 $0 + ~0.8 s/fn; rung 1 $0 + ~10–60 s/fn; rung 2
interactive Haiku ≈ $0.0014–0.0025/fn (fleet-measured / batch+cache list price).
Scope anchors: census 24,194 fns → A 34% / B 47.8% / C 7.2% / D 11% (C-forever).
Eligible ≈ 21.4k for the minimal-kernel target.

| | Level 1: minimal arm64 kernel, majority-Rust (~21k fns) | Level 2: defconfig+modules (~99k fns) | Level 3: whole tree (~500k fns, all arches) |
|---|---|---|---|
| rung 0 share (assumed from measured 8/8 on A; discounted per tier) | A: ~85%, B-post-mirror: ~40%, C: 0% | same rates, more asm-goto/macro TUs → lower | lower still (arch asm, config webs) |
| fns needing any tokens | ~6–8k | ~35–45k | ~250k+ |
| **token bill** (14B free rung eats ~60% of those; Haiku tail at $0.0025–0.005) | **$8–25** | **$45–120** | **$400–900** |
| verification | hostdiff hours + ~150–300 boots → **days** on M2 Max (+free Oracle A1) | weeks on 2–4 workers; config-coverage dominates | the oracle wall, not money — months+, research-grade |
| cash total | **≈ $10–30** | **≈ $50–150 + patience** | not a token problem at all |

The Level-1 headline moved again: COSTDOWN said $15–60; with rung 0 measured at
100%-of-battery the expected bill is **~$10–30 in tokens, $0 compute** — i.e. the
whole dream's synthesis cost is now bounded by *a lunch*. What tokens remain buy
exactly two things: (a) the hard tail c2rust can't preprocess (asm goto, macro
webs), (b) idiom — turning working unsafe Rust into reviewable safe Rust, which is
optional for the ratchet's %-Rust metric and can be done incrementally, later, at
leisure.

Everything spent this session across ladder + localbench + smoke tests:
**$0.000032.** The ladder run itself: $0.0000.

## Files

`ladder.py` — the loop (c2rust rung: shimmed-TU transpile → whole-module keep,
no_mangle-strip, export_name only the target, ffi-type mapping to core ints;
local rung: localbench prompt+retry; Haiku rung: API + per-call cost ledger).
`ladder_results.json` — machine-readable run. Companions: `../hostdiff/` (oracle),
`../localmodel/` (rung-1 calibration), `../COSTDOWN.md` (the bill this validates).
