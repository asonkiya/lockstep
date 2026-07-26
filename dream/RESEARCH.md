# What it would actually take to rewrite the (bootable) kernel into Rust

A research pass, grounded in the real tree we boot and our own measured numbers,
not vibes. Goal (the 2026-07-26 pivot): **rewrite our Linux into Rust and run
it** — a private ratchet, no external review required. This document scopes the
whole job: the denominator, the walls, the cost, the residue, and the
infrastructure gap between the N=4 mechanisms we've proven and the N=100k we'd
need.

Status of sections: `[MEASURED]` = numbers from the real tree/build;
`[MODELED]` = derived from our measured per-unit costs; `[JUDGMENT]` = reasoned,
flagged as such.

---

## 1. The denominator — what "the kernel" actually is, for us [MEASURED]

Not the 37k-file source tree. The thing that boots in our harness.

| scope | TUs | functions | LOC |
|-------|-----|-----------|-----|
| whole source tree (v7.2-rc4) | 36,917 .c | ~690k | 26.3M |
| **arm64 defconfig (what we build)** | **4,453** | **~99,000** | **3.8M** |
| minimal virt config (est.) | ~1,500–2,500 | ~30–50k | ~1–1.5M |

The defconfig is the honest working number: **~99k functions**. It is already
~7× smaller than the tree because a config compiles a slice. A *minimal* virt
config — just enough to boot on QEMU virt with a virtio console/disk — cuts it
again by pulling out the driver mass we don't exercise; that is the config to
actually target for "boots and is mostly Rust," and it's the difference between
a multi-week and a multi-month grind.

Built-set composition is the good news, and it's where the shape is friendliest:
**drivers dominate** (clk 549, acpi 224, pinctrl 190, usb 149, pci 94, tty 73,
mtd 64 TUs...). Drivers are the device-state-plus-a-lock-plus-a-callback-table
pattern — the ptp_mock shape we already transplanted. The hard cores (mm 89,
kernel 79 built TUs) are a small minority of the built set by count.

---

## 2. The four walls (measured where possible)

### 2.1 Eligibility / entanglement  [MEASURED — §Findings-1]
Built-set split: A 2.2% / B 75.3% / C 8.3% / D 14.2%. ~86% reachable via
rewrite-in-place-and-gate; ~14% Tier-D floor (struct-context webs + container_of).
Corrects the naive "drivers easy" story: **net/mm are hardest** (D~20%), fs most
lock-saturated. The wall is *isolation*, not type-resolution (CGIR's 1/382 reproduced).

### 2.2 Oracle coverage  [MEASURED — §Findings-2]
**The binding constraint.** ~17% strongly verifiable (functional oracle + not
boot-critical/barrier-dense), ~73% weakly verifiable (boot-digest + KCSAN only =
"didn't crash," not "correct"), ~11% unverifiable (boot-critical silent-death /
barrier-dense-by-design). Drivers are 92% oracle-free. Reachable ≠ verifiable.

### 2.3 The freestanding-Rust linking wall  [MEASURED — §Findings-3]
Link mechanism scales (2,000 fns/one object/0.28s); the *entanglement* doesn't:
~69% of callees are inline/macro (must reimplement), variadic logging blocks 47%
of files (needs shims), bitfields wall the hot datapath structs, and independent
`#[panic_handler]` objects collide (→ one crate per subsystem + shared runtime).

### 2.4 The abstraction-catalog wall  [JUDGMENT]
Idiomatic transplant needs a safe Rust abstraction to target. R4L ships ~a dozen
(`SpinLock<T>`, `Mutex<T>`, `Arc`, `Rcu`, `KBox`...). The kernel uses hundreds of
concurrency/ownership idioms (seqlocks, lockref, percpu-refcount, RCU flavors,
srcu, the whole `list.h` intrusive family). For the *unsafe-first* dream this
wall is deferred, not blocking: faithful Rust doesn't need the safe abstraction,
only the refinement pass does. It caps how much of the result becomes *idiomatic*
Rust, not how much becomes *Rust*.

---

## 3. Cost and time model  [MODELED from measured units]

Measured unit costs from our runs:
- **Model cost/function**: $0.0020 (in-kernel region, attempt 1), $0.0084 (4-region
  cluster), CGIR ~$0.007/leaf. Amortize retries+clusters at **~$0.015/function**.
- **Gate wall-clock**: incremental Image build ~2–8 min; KCSAN SMP boot reaches
  the probe at ~155–165 s and a stress run adds ~200–320 s → **~8–9 min boot per
  KCSAN leg**, plus build. A non-KCSAN boot-digest gate is far cheaper.

### Model spend is negligible.
99k functions × (say 40% attempted) × $0.015 ≈ **$600**. Even attempting all 99k
at $0.02 is **~$2,000**. The model is not the constraint — this is the
counterintuitive headline. Generation is free; *verification wall-clock* is the
whole cost.

### Gate wall-clock is the entire ballgame.
Naive (one boot per function): 99k × ~12 min = **~800 days**. Untenable — which
is why the ratchet must **batch**: weave B functions into one Image, one boot
validates the whole accumulated set + the new batch.

| batch B | gate cycles (40k eligible) | pure boot-time @ ~12 min | + bisection-on-failure |
|---------|---------------------------|--------------------------|------------------------|
| 1 | 40,000 | ~800 d | — |
| 50 | 800 | ~7 d | ~2× → ~2 wk |
| 200 | 200 | ~1.7 d | ~2–3× → ~4–5 d |
| 500 | 80 | ~16 h | localization dominates |

The tension: **big batches boot fast but a failure doesn't say which function
broke** → the ratchet needs optimistic-big-batch + bisect-on-red (log₂B extra
boots to localize, only when a batch fails). Net realistic estimate for the
eligible set of a *minimal* config: **2–4 weeks of wall-clock on one machine**,
and this parallelizes linearly across N QEMU workers (independent batches on
independent Image builds), so a handful of cores turns weeks into days.

### The three levers that actually matter [JUDGMENT]
1. **Tiered gating** — most functions need only a cheap boot-digest / KUnit gate
   (~3–4 min headless boot); reserve the ~9-min KCSAN leg for the locked/concurrent
   (Tier C) minority. This alone is a ~2–3× speedup because most functions aren't
   concurrent.
2. **Subsystem-scoped boots** — re-running the full battery only for the touched
   subsystem's probe, not a global re-verify every batch.
3. **Parallel workers** — the ratchet is embarrassingly parallel across batches.

---

## 4. The infrastructure gap — proven at N=4, needed at N=100k  [JUDGMENT]

What exists (proven): model synthesis loop, freestanding link into vmlinux, the
C↔Rust seam, KCSAN/lockdep/functional gates, non-vacuous negative controls,
extractor-driven worklists. What the ratchet needs on top:

1. **The manifest** — `{file, symbol, status: c|rust, tier: unsafe|safe, gate_evidence,
   deps}` for every function in the config. The single source of truth; the
   dashboard metric (% of vmlinux text that is Rust, gate-green) reads off it.
2. **The build weaver** — apply the manifest: strip each `status:rust` function
   from its `.c` (the de-static/body-excise surgery we did by hand in M4/M5,
   generalized), compile all winners into the Rust object(s), link, Image. This
   is literally the "one-command pass" from the original dream. Hardest new part:
   doing the C-side excision safely at scale (static vs extern linkage, header
   decls, `EXPORT_SYMBOL`).
3. **The shared runtime crate** — one `#[panic_handler]` / `#[global_allocator]`-
   free core the per-subsystem objects link against, resolving the collision the
   linking agent flags. Plus the growing **extern-and-mirror library**: every
   linkable kernel symbol declared once, every touched struct mirrored once with
   its `BUILD_BUG_ON` layout guard, reused across transplants.
4. **Cumulative, tiered gate orchestrator** — every batch keeps *every prior*
   gate green (boot digest + touched-subsystem probes + KCSAN for Tier C);
   ratchet semantics (green never regresses); bisect-on-red for localization;
   parallel workers.
5. **Dependency ordering from the CGIR index** — leaves first, up the call graph,
   locked clusters together. This is where CGIR (decides *what/when*) and Lockstep
   (decides *whether it stays*) fuse into one pipeline. The SQLite-sweep ordering
   logic, repointed.
6. **The shared runtime crate** (from the linking finding, §Findings-3) — the single
   `#[panic_handler]` + `memcpy`-family + the **kernel-idioms-in-Rust library**
   (reimplementations of the ~69% inline/macro callees: `READ_ONCE`, list/RCU ops,
   refcount, container_of, atomics), written once and trusted since they can't be linked.
   One transplant crate per subsystem links against it; **do not emit many independent
   freestanding objects** (panic-handler collision, verified).
7. **The variadic-log shim generator** — every `pr_*`/`dev_*`/`printk` call site (47% of
   files) needs a fixed-arity `extern "C"` shim because stable Rust can't call C variadics.
   Mechanical but pervasive; generate per call site.
8. **The struct-mirror library with per-config layout guards** — every touched struct
   mirrored once, `BUILD_BUG_ON(sizeof/offsetof)` guarded (load-bearing: `#ifdef` fields
   make each mirror config-specific). Bitfield structs (the hot datapath) are a hand-port
   wall — flag them, don't auto-mirror.
9. **The differential-oracle harness** — THE highest-ROI new piece (from the oracle
   finding, §Findings-2): record a driver's C original's I/O (return values, MMIO/register
   trace) on an input sequence, replay against the transplant, assert identical. This is
   what upgrades the 73% weakly-gated driver mass from "boots" to "correct." Without it,
   most of the dream is faithful-but-unproven.

Items 1–5 are pure engineering with every mechanism de-risked. Items 6–8 are one-time
shared libraries. Item 9 is the one genuinely new capability the research surfaced, and
the plan's success hinges on it more than on anything else.

---

## 5. The honest floor — what stays C/asm forever  [JUDGMENT + agent input]

Even a from-scratch Rust kernel keeps this:
- **arch entry / early boot** — `arch/arm64` head.S, exception vectors, the code
  that runs before the gate can observe anything (the oracle can't see a fault
  that happens before console/scheduler exist). Highest risk, least gateable.
- **Deliberately-lockless / barrier-defined code** — RCU core, the printk
  ringbuffer, seqlocks, lockless lists: their correctness *is* the memory-ordering
  argument, which KCSAN either can't see or flags as intentional (our M0 baseline).
  These need a proof, not a test — out of scope for a dynamic gate, and arguably
  the most-reviewed C on earth already.
- **Inline asm / hardware bring-up** — per-function arch asm, MMIO ordering,
  cache/TLB maintenance.

Realistic ceiling: on a minimal config, **the great majority of driver + lib +
self-contained subsystem functions are reachable; a single-digit-to-low-double-
digit % core stays C/asm** — and that residue is exactly the residue a hand-
written Rust kernel would also keep. The dream metric isn't 100%; it's "boots,
and the ratchet has driven %-Rust to the floor."

---

## 8. Bottom line  [JUDGMENT, revised by the measurements]

- **Feasible and personal-scale — with an honest ceiling.** For a *minimal virt
  config*, a majority of functions can become faithful, booting Rust, once the
  shared idioms crate + variadic shims + differential-oracle harness exist. But
  "booting Rust" splits three ways: **~17% strongly proven**, a large **faithful-
  but-weakly-gated middle**, and **~11–14% C-forever** (Tier-D entanglement ∪
  boot-critical ∪ barrier-dense ∪ bitfield-datapath). 100% is not the target and
  never was; "boots, mostly Rust, ratcheted to the floor" is.
- **Model cost is noise (~$hundreds); the bottleneck is verification wall-clock.**
  Every lever (batch size, tiered gates, minimal config, parallel workers) is
  about amortizing boots. Weeks on one machine, days across a few.
- **The one genuinely new capability the research surfaced is oracle-
  manufacturing** — differential-testing a transplant against its own C original.
  It's what converts the 73% driver mass from "didn't crash" to "correct," and
  the dream's *meaning* (not just its feasibility) hinges on it. Everything else
  is de-risked engineering.
- **Nothing left is research** in the sense of unknown-if-possible. The walls are
  now named and measured: eligibility (unsafe-first + isolation tooling), oracle
  (differential harness + accept a C-forever residue), linking (shared runtime +
  reimpl/shim/mirror libraries). The next brick is the manifest + weaver, then
  Ring 0 — and Ring 0 should be `lib/`-or-`crypto/`-flavored (where the oracle is
  real), with `drivers/ptp` as the first *weakly-gated* driver demo, not the
  proof-of-correctness one.

---

## Findings (measured by parallel investigation)

### Eligibility tiers [MEASURED — Universal Ctags, n=360 stratified sample, Wilson 95% CI]

**98,962 function definitions** in the built set (±1%), confirming §1. Distribution:
drivers 50%, kernel 13%, net 10%, fs 10%, mm 5%, arch 3%, lib 3%, crypto 1%.

| tier | meaning | % | 95% CI | projected fns |
|------|---------|---|--------|---------------|
| **A** leaf-pure | scalar/ptr, no field access, no locks, calls only pure helpers | **2.2%** | 1.1–4.3% | ~2,200 |
| **B** self-contained | touches own args' fields, bounded fan-out, single file, no lock | **75.3%** | 70.6–79.5% | ~74,500 |
| **C** locked-cluster | takes a lock + shared struct state (the ptp_mock class) | **8.3%** | 5.9–11.6% | ~8,200 |
| **D** entangled | container_of / per-cpu / RCU / ops-tables / deep struct webs | **14.2%** | 10.9–18.1% | ~14,000 |

- **The "drivers are easy" story is wrong** — corrected by measurement. Per-subsystem
  D%: **net 20%, mm 20%** (hardest — callback/ops tables + RCU; per-cpu + container_of),
  **fs** most lock-saturated (C 18%, VFS), drivers/kernel/lib B-heavy but drivers is
  *middle* not best (its leaves tail-call domain helpers, so Tier A is only 3%).
- **The B-vs-D line is the real uncertainty**: sweeping the entanglement threshold moves
  B in [60%, 72%] and D in [7%, 19%]. A and C are stable (syntactic markers); B-vs-D is
  a judgment about how deep a struct closure you'll transplant as a unit. **±7pp.**
- **Biggest Tier-D blocker: struct-context entanglement — 57% of D** (wide helper/deref
  webs, no single offending keyword), then `container_of` 17%. This reproduces CGIR's
  hardest result exactly (struct-pointer lift 1/382 even on an amalgamation where type
  resolution was solved → **isolation, not type-resolution, is the wall**).
- **Fundamental-vs-tooling, the load-bearing distinction**: these are walls against
  *lift-to-standalone-TU*. They are merely *hard* for **rewrite-in-place + boot-gate** —
  which is the strategy we already proved at rung 4. So:
  - **~2% turnkey today** (Tier A, lift-and-differential-verify, cents/fn, our shipped class).
  - **~86% reachable ceiling (A+B+C)** under rewrite-in-place-and-gate — B needs
    struct-context resolution (solvable engineering), C needs the concurrency-IR + gate
    Lockstep already built.
  - **~14% hard floor (Tier D)** needs human-supplied layout/ownership invariants no
    current tool recovers.
- **Caveat the agent flags and we adopt**: these are *structural-eligibility* rates, not
  *proven-correct* rates. Cross-reference the oracle finding below — reachable ≠ verifiable.

### Oracle coverage + residue [MEASURED, n=4,439 built C TUs]

**This is the binding constraint — not lifter capability.** A dynamic gate can
only decide what it can observe, and the kernel keeps its executable oracles in
a small, non-driver core.

- **Test surface**: 292 KUnit suites / 2,805 `KUNIT_CASE`s, 132 selftest dirs.
  Only **19.4% of built TUs have any KUnit oracle** (dir-level). Well-tested:
  lib/ 71%, kernel/ 52%. Barely: mm/ 1%, crypto/ 0% KUnit — **but crypto has its
  own oracle** (testmgr known-answer vectors, 158 `*_tv_template[]`), so count it
  covered. **Drivers: 91.7% have no functional oracle at all** — and drivers are
  63% of the built kernel.
- **Verifiability split** (of 4,439 built TUs):
  | tier | meaning | count | share |
  |------|---------|-------|-------|
  | (a) strongly verifiable | functional oracle + not boot-critical/barrier-dense | ~755 | **~17%** |
  | (b) weakly verifiable | boot-digest + KCSAN/lockdep only, no functional oracle | 3,264 | **73.5%** |
  | (c) unverifiable by dynamic gates | boot-critical (silent-death) or barrier-dense-by-design | 478 | **10.8%** |
- **The 73% weak-verifiable middle is the real story, and it's a genuine
  qualification to the dream.** For that mass (overwhelmingly drivers), the only
  gate is "still boots, no new KCSAN/lockdep finding" — *absence of evidence*. It
  catches crashes and gross corruption but **passes any behavioral bug that
  doesn't perturb boot or trip a sanitizer**: a driver that returns wrong data or
  mis-programs a register sails through. The boot-digest gate certifies "didn't
  crash," not "is correct."
- **The ~11% residue is C-forever**, for two distinct reasons: (i) **8.5%
  boot-critical** (arch entry, early initcalls — 564 TUs register an early
  initcall) where a wrong rewrite yields a *silent non-boot indistinguishable
  from a harness failure* — the failure mode is undetectable, not merely
  unverified (exactly the vacuous-harness trap our own negative controls already
  exercise); (ii) **2.5% barrier-dense** (net/ 12%, kernel/ 11%, mm/ 10%, fs/ 7%
  — RCU/seqlock/ringbuffer) where correctness *is* a memory-ordering invariant
  KCSAN is designed to ignore (our M0 printk-ringbuffer finding is the archetype).

**Consequence for the plan**: "reachable" ≠ "verifiable." We can *transplant* most
drivers, but we can only *weakly attest* them. Three responses, in order of
value: (1) **manufacture oracles** — a transplanted driver can be differentially
tested against its own C original (same MMIO trace / same return values on a
recorded input sequence), which upgrades a driver from tier (b) toward (a); this
is the single highest-leverage tooling investment the dream needs. (2) Lean the
early ratchet on tier (a) — lib/crypto/kernel-util — where the gate is real.
(3) Treat tier (b) transplants as "faithful but weakly-gated," tracked distinctly
in the manifest, never conflated with proven.

### Freestanding-Rust linking wall [MEASURED — rustc 1.97 in-container, 188K-symbol System.map, tree-wide greps]

**Verdict: the link mechanism scales trivially; the C-side entanglement around each
function does not.** One crate → 2,000 `#[no_mangle]` fns → one object, one panic
handler, 0.28 s. The proven transplants succeeded because they are the narrow
intersection — pure, non-logging, non-percpu, bitfield-free leaves — that dodges every
wall below.

1. **The extern surface is the real cost, not linkage.** Median out-degree 7 callees/fn,
   but only **~16% are linkable `extern "C"`** (out-of-line symbols); **~69% are
   inline/macro** with no symbol → **must be reimplemented in Rust** (`likely`,
   `container_of`, list/RCU ops, per-cpu, atomics, endian, flag accessors). Naive
   symbol-table checks *undercount* this (macro-generated inlines). ⇒ a shared
   **"kernel-idioms-in-Rust" runtime crate**, built once, is mandatory — 5 of every 6
   callees are this idiom layer.
2. **no_std showstoppers, ranked by prevalence:**
   | wall | prevalence | verdict |
   |------|-----------|---------|
   | **variadic logging** (`pr_*`/`dev_*`/`printk`) | **~200K sites, 47% of files** | **the pervasive wall** — stable Rust FFI can't call C variadics; needs a fixed-arity `extern "C"` shim per call site (mechanical but everywhere, in ordinary error paths) |
   | inline asm | 9,848 sites / 954 files | hard per-fn (hand-port, not mechanizable) |
   | per-cpu accessors | ~7,400 sites / 821 files | hard (needs `.data..percpu` + arch reloc; escape = C shim) |
   | `__ex_table` fixups | ~350, uaccess only | hard but rare (blocks `copy_*_user`) |
   | `__init`/sections, FP, ops-tables, `__builtin_*`, container_of | ubiquitous but mechanical | annoyances / tooling cost |
3. **Struct bitfields are a hard wall — on exactly the structs you can't avoid.** ~88% of
   random structs mirror `#[repr(C)]` cleanly; drops to **~75%** inside a hot subsystem
   closure (skb/sock/netdev ≈ 117 structs); **the 4 hottest datapath structs
   (sk_buff ~40 bitfields, task_struct ~25) hit the wall** — Rust `repr(C)` has no
   bitfields, and `BUILD_BUG_ON(offsetof)` can't even catch a wrong bit *within* a byte.
   Every `#ifdef` field makes a mirror valid for exactly one `.config` → the per-build
   `BUILD_BUG_ON` layout guard is **load-bearing, not optional**.
4. **One object vs many — tested directly**: independent freestanding crates each with
   `#[panic_handler]` **collide at link** (`multiple definition of rust_begin_unwind`),
   and the stable toolchain can't weak-dedup (`#![feature(linkage)]` → E0554). Two working
   fixes: **one crate → one object per subsystem, each with a single panic handler**
   (recommended), or `objcopy --localize-symbol` + `ld -r`. Also: freestanding Rust emits
   `memcpy`/`memset`/`memmove` refs (kernel's arch/arm64/lib provides them — links, but a
   runtime dependency to know about).

**Architecture that falls out**: a **shared runtime crate** (single panic handler + the
kernel-idioms reimpl library + the variadic-log shims + the struct-mirror library with
layout guards), and **one transplant crate per subsystem** linking against it. Not many
independent objects.

---

## 7. Synthesis — the three ceilings compose [JUDGMENT, from the measurements]

The walls are not alternatives; they multiply. A function is *dream-complete* only if it
clears all three:

| axis | measured | the gate it sets |
|------|----------|------------------|
| **structural eligibility** | ~86% reachable (A+B+C), ~14% Tier-D floor | *can we isolate/rewrite it* |
| **oracle coverage** | ~17% strong, ~73% weak, ~11% unverifiable | *can we tell if it's correct* |
| **linking feasibility** | scales, but ~69% inline-reimpl + variadic shim (47% of files) + bitfield floor on hot structs | *can it compile+link freestanding* |

- **The turnkey island** (pure, has-oracle, non-logging, bitfield-free leaf) is the
  intersection — small, single-digit %: exactly `lib/`, `crypto/`, kernel-util where our
  wins already live. The empirics confirm those wins were not luck; that's where the
  oracle *and* the clean-linking *and* the eligibility all coincide.
- **The vast middle (Tier-B drivers, ~73% of the kernel)** is *reachable and linkable
  after tooling* but only *weakly gated*. The dominant risk is not "can't do it" — it's
  "did it, can't prove it's right." **This makes oracle-manufacturing (differential test
  vs the C original on a recorded I/O/MMIO trace) the highest-ROI investment in the whole
  program** — it's what converts the 73% from "boots" to "correct," and it's the piece we
  have *not* yet built.
- **Off the island, walls compound fast**: the hot datapath (skb/task_struct) stacks
  bitfields + `#ifdef` + variadic logging + per-cpu simultaneously. That core is the
  natural C-forever residue on *engineering* grounds even before the oracle/boot-critical
  residue is counted.
- **Revised reachable-AND-meaningfully-gated estimate**: not 86%. Realistically, a
  **minimal virt config** whose driver mass is differentially-testable could reach **well
  over half its functions in faithful Rust, booting, with a real (not just boot-survival)
  gate — once the idioms crate + variadic shim generator + differential-oracle tooling
  exist.** The strong-proof core stays ~17%; the rest is "faithful, differentially-checked,
  weakly-sanitized," tracked as such.
