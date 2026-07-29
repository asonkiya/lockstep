# Overnight suite — results

A five-job unattended battery (no kernel boots, no API → $0, no hang path) run
against the whole kernel tree. It answers, at scale, the two questions that
matter after the extractor grind: **is the machine sound**, and **what is the
single highest-value next increment**. Both got definitive, data-backed answers.

Run: 2026-07-29, ~1h40m wall (the soundness megatest dominates). Orchestrator
`run_suite.sh`; consolidated `reports/SUMMARY.md`.

## Headline: soundness proven at scale

**0 false passes across 25,341 adversarial candidates.** The project's core
claim — *a wrong candidate is never accepted* — was tested not on the 145-case
suite but on:

- **25,323 randomized recorder mutants** — 300 per closeable register function
  (bit-flipped offsets, dropped / duplicated / reordered accesses, corrupted
  returns), each of which must be rejected on the trace/return;
- **18 hostdiff wrong-candidate probes** — delegation-to-C and constant-return,
  universally wrong-to-accept.

Every one rejected. Not a single mutant of a single function fooled either
oracle. This is the soundness posture the whole project rests on, held at ~175×
the scale of the committed test suite.

## The recorder census (whole tree)

Every `.c` under the kernel touching `readl`/`writel`, ~37k files:

```
register functions scanned : 12,829
closed (extractable today) :     95  (0.74%)
refused                    : 12,397
emit-gap anomalies         :    337   (extracted but candidate won't compile)
false passes               :      0
```

0.74% is the honest current coverage of the recorder's *host-side* extractor.
Low — and the census tells us exactly why, and what fixes it.

## The next-increment backlog — ranked by addressable functions

This is the census's most valuable output: the refusal reasons across all 12,829
register functions, clustered and ranked. No more guessing which increment to
build — the whole tree voted.

| increment | addressable fns | % of register mass |
|---|---|---|
| **control flow** (if/for/while/switch/goto) | **8,207** | **64.0%** |
| non-clean access (computed/opaque offset or value) | 2,824 | 22.0% |
| iomem local / base-alias (unresolvable base) | 305 | 2.4% |
| unresolvable identifier (macro/global offset) | 136 | 1.1% |
| nested-brace scope (guard/scoped_guard) | 59 | 0.5% |
| **emit-gap** (extracted, candidate won't compile) | **337** | 2.6% |

Two conclusions:

1. **Branch-aware extraction is the biggest prize by far** — 64% of the entire
   register mass is gated on modeling `if`/`switch` soundly instead of refusing
   it. That is one increment worth more than every other combined. It is also
   the hardest and most soundness-critical (a conditional MMIO access makes the
   register program state-dependent), so it is a deliberate, tested build, not a
   quick patch.
2. **The emit-gap is the cheap win** — 337 functions that *already extract
   correctly*; only the Rust code-gen is broken (329 RUSTC_FAIL, dominated by
   read-modify-write with a named local — `stat = readl(); stat &= …;
   writel(stat)` — where the emitter never declares `stat`, doesn't map the
   input param to the harness pin, and drops the compute-constants). Fixing the
   *emitter* (not the soundness-critical extractor) could roughly 4× coverage
   with low risk. This is the chosen immediate next step.

## Real progress at $0 (synth grind)

The free pipeline (c2rust → local qwen, no API) verified, overnight, **6 kernel
functions bit-identical to their C** at literally $0.00:
`__kfifo_max_r` (local model), `__sw_hweight8/16/32`, `int_pow`, `int_sqrt`
(c2rust). `__kfifo_max_r` is one the function-scoped-extraction work unlocked.

## Tree-wide purity census

Scalar-leaf harvest: **A 76% / B 16% / C 5% / D 3%**; pure fraction **14.7%**.
The conservative post-hardening whitelist keeps the value-differential (T0/T1)
domain small — which is why the boot-free tier is inherently narrow, confirmed
tree-wide.

## What this establishes

- The machine is **sound at 25k-candidate scale** — enforced live, not just
  asserted.
- The next two moves are **measured, not guessed**: the emit-gap (cheap ~4×) now,
  branch-aware extraction (the 64% prize) as the deliberate next build.
- Cost remains noise; the deterministic pipeline runs the whole tree for $0.

## Files

`run_suite.sh` (orchestrator), `overnight_sweep.py` / `soundness_megatest.py` /
`synth_grind.py` / `analyze.py` / `consolidate.py` (the jobs), `reports/`
(per-job JSON + `SUMMARY.md`, gitignored).
