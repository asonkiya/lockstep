# COSTDOWN — driving the kernel-rewrite bill from ~$5k to ~pocket change

The RESEARCH.md bill for a booting majority-Rust minimal kernel was **~$400 model +
$2–5k cloud + 2–4 machine-weeks**. This pass re-derives that bill against (a) our own
measured unit costs, (b) the host we actually run on, and (c) current pricing/tooling
facts (sourced 2026-07-27). Verdict up front: **the honest new bill is ~$30–100 in
tokens + $0–5/month in compute + days-to-~2-weeks of background wall-clock on hardware
already owned.** The old bill priced renting compute we own and paying for model calls
we mostly don't need.

Tags: `[MEASURED]` = our runs; `[SOURCED]` = external, URL in §refs; `[MODELED]` =
derived arithmetic.

---

## 0. What the money was actually buying

| old line item | old estimate | what it really was |
|---|---|---|
| model synth | ~$400 | $0.015/fn amortized — but widerun measured **$0.0014/fn** `[MEASURED]` (71 fns / $0.10), 10× lower |
| cloud compute | $2–5k | ~4k instance-hours of **arm64 QEMU workers** — but the dev machine IS a 12-core arm64 (M2 Max, 32 GB). We were pricing a rental of what we own |
| wall-clock | 2–4 weeks | dominated by **boot-gates under TCG inside Docker** — a double emulation penalty that is entirely removable |

Total model spend across the ENTIRE dream so far — every ring, sweep, wide run,
fleet, mirror, transplant: **≈ $0.25** `[MEASURED]`. The cost problem was never
tokens. It was (1) renting compute, (2) paying a boot for every verification.

---

## 1. Lever 1 — stop booting to verify (the ~900× one)

**Measured, this week:** the cluster gate ran a **2,000,400-case differential of real
kernel `gcd.c` (C vs Rust) on the host in 0.28 s** `[MEASURED]`. A boot-cycle
verification of the same class costs ~247 s `[MEASURED]` (Ring 7 worker: weave +
incremental build + TCG boot + probe). Same soundness (bit-exact differential), ~900×
cheaper — because for a *pure* function, the kernel around it contributes nothing to
the oracle.

Restructure verification into four cost tiers, cheapest-first:

| tier | class | oracle | marginal cost | boots |
|---|---|---|---|---|
| T0 | pure scalar (A) + struct-pure w/ mirror (B) | **host-native differential** (real C + shim vs Rust, the cluster-gate pattern) | **~0.3–1 s/fn** | 0 |
| T1 | MMIO/driver | **recorder replay** — record trace once, replay every candidate in userspace | ~1 s/candidate after one recording | 1 per *driver*, not per candidate |
| T2 | integration ratchet | woven kernel builds+boots, batch digest | 1 boot per ~200-fn batch | ~1/200 fns |
| T3 | Tier-C concurrent | KCSAN leg (concgate) | ~9 min, batchable | only the 7.2% locked class |

The purity router (044e120) already routes soundly; T0 needs the **shim library**
(generalize `dream/cluster/kdefs.h` per header family — same amortization economics
as mirrors: write once, unlock a family). Retries become free: a rejected candidate
re-verifies in seconds, so *retry count stops mattering* — which is what makes Lever
3 possible.

**Effect `[MODELED]`:** behavioral verification for ~80–90% of the eligible set drops
from "boot time × N" to **hours total, $0**. Boots remaining for 22k fns: ~110
integration + ~40 KCSAN batches + ~1/driver recordings ≈ **a few hundred boots
total** (vs ~22,000 naive).

## 2. Lever 2 — kill the TCG double penalty (HVF on the host)

We boot QEMU-arm64 **under TCG inside Docker on macOS**: software emulation inside a
VM, for a guest ISA identical to the host's. `[SOURCED]` `-accel hvf` is upstream
QEMU (6.2+); arm64-on-arm64 under Hypervisor.framework is near-native, practitioner
consensus ~an order of magnitude over TCG; minimal-kernel boots land in seconds to
tens of seconds. Docker Desktop's VM has no `/dev/kvm` on M2 `[MEASURED]` (nested
virt needs M3+/macOS 15), so the fix is architectural, not a flag:

> **Build in Docker (kbuild container unchanged) → copy Image out → boot with
> `qemu-system-aarch64 -accel hvf -cpu host -M virt -nographic` directly on macOS.**

Caveats `[SOURCED]`: must pass `-cpu host`; gdbstub breakpoints don't work under hvf
(fall back to TCG when debugging); `-nographic` sidesteps the one open hvf display
issue. **Effect:** the T2/T3 boot legs stop dominating; **incremental kernel build
(~2–8 min) becomes the new bottleneck** → mitigate with ccache in the container and
subsystem-scoped rebuilds. Ring 7 proved workers scale near-linearly (1.99× on 2)
`[MEASURED]`; 32 GB RAM supports ~4 concurrent build+boot workers once Docker's RAM
allocation is raised from the current 7.7 GB.

## 3. Lever 3 — the synth ladder: don't pay a frontier model for first drafts

**The structural insight our own data proves: with sound gates (zero false passes
across every ring, sweep, and wide run `[MEASURED]`), synthesizer quality is a
wall-clock knob, not a correctness knob.** A worse-but-free synthesizer costs extra
retries; Lever 1 made retries cost ~0.3 s. So run an escalation ladder,
cheapest-first, gate-arbitrated:

1. **c2rust (deterministic, $0)** `[SOURCED]`: actively maintained (v0.21.0,
   Oct 2025); transpiled a real kernel module; emits exactly the unsafe-first Rust
   the ratchet wants; skips-with-warning what it can't handle. Limits: **no `asm
   goto`** (pervasive in kernel core since 5.0), inline asm emitted as
   "starting-point only", macros pre-expanded. For pure C leaves — our Tier-A and
   the mirror-unlocked slice of B — it should transpile mechanically. Even at a
   50% hit rate on T0-class functions, that's ~thousands of fns at **$0.00/fn**.
2. **Local open-weight model ($0/token)** `[SOURCED]`: Qwen2.5-Coder-14B /
   Qwen3-Coder-30B-A3B (Apache 2.0) run on a 32 GB Mac via ollama (already
   installed `[MEASURED]`). No published evidence a ≤14B model matches frontier
   unaided on C→Rust (CRUST-Bench: open 32B-class trails) — but unaided isn't our
   setting; the gate catches wrong output and the counterexample-retry loop
   (Ring 5) converges. First-pass rate maybe 50–65% vs Haiku's measured 74%; the
   difference is seconds of free retries.
3. **Haiku, batch + cache (paid, last resort)**: only functions rejected repeatedly
   by 1–2 escalate to the API — historically the hard tail, a minority.

**Effect `[MODELED]`:** paid model calls collapse from "all 22k" to the escalation
tail (~10–30%). Even with zero local setup — skipping straight to Haiku for
everything — Lever 4 alone caps the bill under ~$100.

## 4. Lever 4 — token engineering for whatever stays paid

`[SOURCED]` (live pricing, 2026-07-27): Haiku 4.5 $1/$5 per MTok; **Batch API still
−50% and it STACKS with prompt caching** (cache read = 0.1×). Two gotchas that
matter: **Haiku's minimum cacheable prefix is 4,096 tokens — a 3k prefix silently
doesn't cache** (pad the shared catalog/rules prefix past 4,096); inside batches
cache hits are best-effort (30–98%), so use the 1-hr TTL.

Per-function `[MODELED]`, 4,096-tok cached prefix + 500 fresh in + 800 out, batch +
cache-hit: **≈ $0.0025/fn** (~$0.0037 on cache miss). Full 24k single-pass ≈
**$59–90**; behind the Lever-3 ladder, realistically **$15–40 incl. retries**.
Batch fits the pipeline natively — synth is already fire-and-forget into a
deterministic gate; nothing waits on latency.

## 5. Lever 5 — compute that costs $0–5/month

- **The M2 Max itself**: 12 arm64 cores. Raise Docker RAM 7.7 → ~24 GB, run ~4
  workers. `[MEASURED]` basis: Ring 7.
- **Oracle Cloud Always Free** `[SOURCED]`: 4 OCPU / 24 GB Ampere A1, permanent
  free tier, real Linux with **KVM** → native-speed QEMU workers, always-on
  grinder (keep it busy; idle instances get reclaimed).
- **GitHub Actions arm64 runners** `[SOURCED]`: free for public repos (GA
  Aug 2025), 4 vCPU. lockstep is private — would need a public gate-mirror repo;
  optional.
- **Hetzner CAX** `[SOURCED]`: ~€6/mo for a 2 vCPU/4 GB arm64 KVM box if an
  always-on second worker is wanted without Oracle's capacity roulette.

**Effect:** the $2–5k line item → **$0** (M2 Max + Oracle free tier), or $6/mo for
comfort.

## 6. Agent-free automation — where the LLM isn't even in the loop

Everything in the hot path is **already deterministic script**: harvest, purity
routing, mirror generation, cluster analysis, weaving, probe generation, gates,
manifest, dashboard (`fleet.py`, `weave.py`, `purity.py`, `mirror.py`,
`cluster.py`, `gate.sh`). The LLM appears at exactly one seam — "C text in, Rust
text out" — as a stateless function call, and Lever 3 makes even that seam
optionally model-free. **No agent, no orchestration tokens, no interactive session
required**: the whole ratchet can run as a cron loop (synth-ladder → T0 host-diff →
batch-weave → one HVF boot → ratchet manifest) that burns pennies while the machine
is otherwise idle. Cost of the *orchestrator* itself: $0 by construction.

---

## 7. The re-modeled bill `[MODELED]`

Scope: minimal arm64 virt config, ~20–25k fns, ~89% reachable (census), the same
"booting majority-Rust minimal kernel" target.

| item | old | new | why |
|---|---|---|---|
| model tokens | ~$400 | **$15–60** (ladder) / **≤$90** (all-Haiku worst case) | measured $0.0014/fn; batch+cache $0.0025/fn; c2rust/local tier $0 |
| compute | $2–5k | **$0** (+optional €6/mo) | own M2 Max + HVF; Oracle A1 free KVM worker |
| behavioral verification | (inside cloud item) | **$0, hours** | T0 host-diff 0.3 s/fn ×22k ≈ 2–3 core-hours |
| integration boots | weeks of TCG | **days** | ~150–300 total boots; HVF seconds-class; build (ccache'd) dominates |
| wall-clock | 2–4 machine-weeks | **~3 days–2 weeks background** | 4 local workers + free Oracle grinder; retries no longer boot |
| **total cash** | **~$2.5–5.5k** | **≈ $30–100 one-time** | |

The ~11% Tier-D floor and the verification-strength tiers are unchanged — this pass
moves *cost*, not the epistemics: T0/T1 keep the same bit-exact differentials, T2/T3
keep the same boots; there are just radically fewer paid ones.

## 8. Build order (savings ÷ effort)

1. **Host-diff harness (T0)** — generalize `cluster/kdefs.h` into per-family shims +
   auto-extract fn + deps into a host TU (the lifter machinery exists in CGIR).
   *Unlocks the ~900×; makes retries free; prerequisite for the ladder.*
2. **HVF boot runner** — `brew install qemu`; boot script that pulls Image from the
   volume and boots on-host; keep Docker for kbuild only. *Hours of work, kills the
   double penalty.*
3. **Synth ladder** — c2rust in the container; `ollama pull qwen2.5-coder:14b`;
   escalation wrapper around the existing synth seam. *Token bill → noise.*
4. **Batch+cache Haiku path** — pad prefix ≥4,096 tok, 1-hr TTL, Batches endpoint.
   *Only matters for the escalation tail; trivial to add.*
5. **Oracle A1 free worker** — provision once, run the cron ratchet. *A second
   always-on lane for $0.*

## 6. Lever 6 — template synthesis: $0, NO model, for idiom-recognizable families

The GPIO family result (`dream/family/RESULTS.md`) showed driver register logic
factors into a few IDIOMS, and a new driver in a known idiom costs only its offset
table. So for idiom-recognizable drivers the Rust transplant is a **deterministic
instantiation of the idiom template + offsets — no LLM call at all** (`dream/family/
template_synth.py`). This removes the model from the majority of the driver mass
(e.g. the ~41 bgpio drivers behind `gpio-mmio.c`): `$0.006/fn → $0.000/fn`.

Soundness is unchanged — template synth emits only the candidate; the trace-oracle
gate still checks it against the REAL driver's C reference, so a mis-recognized
idiom / wrong offset table `DIFF_FAIL`s and the ladder falls back to c2rust/model
(proven: `test_template_synth.py` — right offsets `DIFF_PASS`, swapped `DIFF_FAIL`).
So the ladder gains a rung ABOVE c2rust: **template ($0) → c2rust ($0) → local
($0) → Haiku (tail)**. The token bill for the driver mass is now dominated by the
tail-of-the-tail custom-logic drivers, not the shared-library core.

## 7. Lever 7 — orchestration economy (the agent's OWN token bill)

The pipeline's lifetime synth spend is ~$0.25; a long Opus *orchestration* session
dwarfs it. So the largest real saving is how the agent works, not how the pipeline
synthesizes:

- **Cheap model for mechanical work.** Census scans, reach measurements, grep-shaped
  exploration → **Haiku** subagents; reserve Opus for design + hard reasoning. A
  Haiku scan agent is ~1/15th the cost and just as good at tallying a census.
- **Measure once, persist, never recompute.** Save worklists/census/results to
  `dream/*/` (e.g. `reach_accepted.json`) and *check disk before launching a scan*.
- **Sample + honestly extrapolate** over full 100k-fn passes when a bounded sample
  answers (drivers-at-8% → ×12).
- **Terse I/O.** Subagent prompts ask for terse returns; a 90k-token transcript is
  pure cost when a tally was wanted. Keep commit messages / reports lean.
- **Don't subagent what a 5-line grep answers; don't re-read files already in
  context.**

## refs

- Pricing/caching/batch: platform.claude.com/docs pricing.md, prompt-caching.md,
  batch-processing.md (fetched 2026-07-27; stacking + 4,096-tok Haiku minimum
  explicit).
- c2rust: github.com/immunant/c2rust (v0.21.0 2025-10-20);
  immunant.com/blog/2020/06/kernel_modules; c2rust manual (transpile/asm).
- Local models: arxiv 2409.12186 (Qwen2.5-Coder); CRUST-Bench arxiv 2504.15254;
  SACTOR arxiv 2503.12511; Syzygy arxiv 2412.14234.
- HVF: QEMU hvf aarch64 series (patchew 20210915181049); mstone.info/posts
  qemu-aarch64-hvf; sam4k.com virtualised-linux-empire.
- Free ARM: github.blog changelog 2025-01-16 + 2025-08-07 (arm64 runners);
  Oracle Always Free A1 (4 OCPU/24 GB); Hetzner CAX post-2026-06 pricing.
