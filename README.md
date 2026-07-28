# Lockstep

**Verified C→Rust rewriting of the Linux kernel — oracle-first.**

Lockstep is a research project and toolkit for migrating real kernel C to Rust
where every accepted rewrite is **gated by a sound check**, never by trust in
the generator. The generator (a transpiler, a local model, an API model — it
doesn't matter) proposes; a battery of oracles disposes: bit-exact
differentials against the original C, recorded MMIO traces for drivers,
compile-time struct-layout proofs, KCSAN/lockdep race gates inside a booting
kernel, and bounded model checking for the arithmetic core. Across every run in
this repo, the false-pass count is **zero** — and that discipline, not the
translation, is the project.

Companion to [CGIR](https://github.com/asonkiya/llm-semantic-compilers) (the
pure-function rewriter this grew out of) and
[lockstep-course](https://github.com/asonkiya/lockstep-course) (a 16-lesson
course teaching everything here from first principles).

## Headline results (all reproducible from this repo)

- **Real Rust in a booting Linux kernel:** in-tree C functions excised and
  replaced by freestanding Rust objects, linked into vmlinux, boot-verified —
  10/18 functions (55.6%) of the tracked set Rust across 5 source files at the
  ratchet's last woven state, every one differentially equal to its C original
  against the live kernel (`dream/ratchet/`).
- **The sabotage always gets convicted:** every gate ships a negative control.
  KCSAN has named a sabotaged Rust region in its racing stack while the clock
  ran backwards 98×; a skip-the-status-poll driver rewrite that returns the
  *identical value* was rejected on its register trace; a bug planted inside a
  private helper was caught through the exported boundary.
- **The census:** 24,194 real kernel functions classified — ~89% mechanically
  reachable for this approach, ~11% entangled C-forever floor; and the deeper
  split, **reachable ≠ verifiable** (~17% strongly provable / ~73% weakly /
  ~11% by design unverifiable) — the "oracle wall" (`dream/SWEEP.md`,
  `dream/RESEARCH.md`).
- **Verification without booting:** `hostdiff` verifies a rewrite of a pure
  kernel function against the *real kernel TU* on the host — 2M differential
  cases in ~0.3s vs a ~247s boot cycle (~900×), same bit-exact oracle
  (`dream/hostdiff/`).
- **Synthesis is basically free:** a cheapest-first ladder (c2rust → local
  14B model → API model) with the oracle as arbiter. Sound gates make
  synthesizer quality a wall-clock knob, not a correctness knob; the
  deterministic rung swept the fleet battery 8/8 at $0.0000
  (`dream/ladder/`, `dream/COSTDOWN.md`).
- **Formal tier:** `__sw_hweight32`/`64` proven equal to spec over their FULL
  2^32/2^64 domains via Kani/CBMC in 0.09s (`dream/formal/`).
- **One loop that routes the whole worklist:** the multi-tier router classifies
  each function (census + purity + linkability) and sends it to the strongest
  oracle it can *soundly* use — T0 boot-free hostdiff, T1 in-kernel differential,
  T2 mirror, T3 recorder — then executes the two automatable tiers. On the
  widerun's 72 functions it produced **9 sound verdicts (5 boot-free, 4 in one
  shared boot) for $0.004, zero false passes**, with the rest routed and their
  per-family artifact named rather than silently passed. Aimed at a
  `drivers/{gpio,ptp,clk}` worklist (102 functions) the previously-empty deep
  buckets fill: 40 route to the MMIO recorder, 40 to the struct mirror
  (`dream/router/`, `router_result.json`, `DRIVER-RUN.md`). Soundness is
  structural — nothing is placed in a weaker oracle than its class requires, so
  the widerun's over-crediting bug is unrepresentable.
- **Automated per-driver MMIO harness (mechanism demonstration):** a generator
  extracts a real in-tree driver function's register program, seam-adapts it to
  a C reference, and drives record/replay — both host-side and woven into a
  booting kernel — where a deliberately wrong-register mutant is rejected on the
  trace. It closes 3 of the router's 40 T3_TRACE routees on the clean
  constant-offset pattern (the other 37 are an itemized extractor backlog).
  **Caveat:** here the candidate is generator-emitted from the *same* extracted
  register program as the reference, with non-vacuity shown only by the injected
  mutant — this proves the harness *mechanism* (extract → record → replay →
  reject-the-wrong-trace), not an independent model rewrite verified against an
  oracle (`dream/mmiogen/`, `RESULTS.md`, `INKERNEL.md`).

## What's reusable outside this project

Each tool is small, self-contained Python/shell, and generalizes past this
repo's targets:

| tool | what it does | for whom |
|---|---|---|
| `dream/hostdiff/` | boot-free differential oracle: compile the real C TU with a shim, auto-generate a probe from the C signature, diff any Rust candidate — seconds | anyone verifying a C→Rust rewrite of pure functions |
| `dream/recorder/` | record a driver's MMIO access trace once; replay every candidate against the frozen trace with **no device present** | driver migration where the hardware is the only oracle |
| `dream/mmiogen/` | auto-generate a per-driver record/replay harness from real in-tree driver source: extract the register program → seam-adapted C ref → gate (host + in-kernel) | driver work wanting a harness without hand-scaffolding each function |
| `dream/router/` | classify a worklist and route each function to its strongest *sound* oracle (T0-T3); execute the boot-free and one-boot tiers | anyone running a rewrite pipeline who needs soundness by construction |
| `dream/mirror/` | generate `#[repr(C)]` struct mirrors from C headers, proven ABI-correct two independent ways (rustc const-asserts + `BUILD_BUG_ON`) | any FFI boundary that must be byte-exact |
| `dream/cluster/` | weave a function *and its private static helpers* out of a C TU as one Rust object, verified at the exported boundary | any in-place C→Rust migration hitting `-Werror=unused-function` |
| `dream/ladder/` | the c2rust → local model → API model escalation loop, gate-arbitrated | anyone who wants translation without an API bill |
| `dream/widerun/purity.py` | conservative purity router: not-provably-pure = quarantined | keeping a value-differential pipeline sound |

## Repository map

```
baseline/     M0 — boot real Linux under KCSAN+lockdep, capture the baseline
extraction/   M1 — concurrency IR: which lock protects which field (TSan-crosschecked)
transplant/   M2 — first hand transplant: SpinLock<T> region, loom-proven
synthesis/    M3 — first model-synthesized region ($0.0028), gate rejects its own sabotage
kernel-gate/  M4 — in-kernel gates: KCSAN convicts sabotage BY NAME (depth + breadth)
rfc-export/   M5 — checkpatch-clean git-am-able RFC series emitter
docs/         design.md (the original design), architecture + getting started
dream/        the full-kernel ratchet and everything it learned:
  ratchet/      manifest + weaver + rings 0-9 (woven, booting Rust kernel states)
  router/       the one loop: route each fn to its strongest sound oracle (T0-T3), run T0/T1
  diffgate/     in-kernel differential oracle (C _ref vs Rust, one kernel)
  sweep/ widerun/  the 24k census + production-scale runs + the purity router
  recorder/ mirror/ cluster/   the three critical-path libraries
  mmiogen/      per-driver MMIO-record harness generator (extract → seam → record/replay)
  hostdiff/ localmodel/ ladder/  boot-free oracle + $0 synthesis
  concgate/ exhaustive/ formal/  concurrency gate, exhaustion, Kani/CBMC
  RESEARCH.md SWEEP.md PRIOR-ART.md COSTDOWN.md SUMMARY.md   the findings
```

Every subdirectory has a `RESULTS.md` (or `RINGn.md`) with the measured
numbers, and most have a `gate.sh` that reproduces them.

## Quickstart (no kernel build required)

The host-side gates run in seconds with just `cc`, `rustc`, and Python 3:

```bash
# the boot-free differential oracle: 7 real kernel fns, ~16M cases, ~10s
KSRC=/path/to/linux bash dream/hostdiff/gate.sh

# static-cluster weaving: orphan shown, avoided, boundary-verified
KSRC=/path/to/linux bash dream/cluster/gate.sh

# the MMIO trace recorder: record/replay, value-identical bug rejected on trace
bash dream/recorder/gate.sh
```

`KSRC` points at any Linux source checkout (no build needed for these — the
tools read the C source). The in-kernel gates (ratchet, diffgate, concgate,
mirror's kernel leg) additionally need the containerized kbuild+QEMU harness —
see [`docs/GETTING-STARTED.md`](docs/GETTING-STARTED.md).

## The idea in one paragraph

A function boundary is the wrong seam for concurrent code: the invariants span
it (a lock taken here protects a field touched there). So for concurrent
regions Lockstep's unit of rewrite is a **semantic region** — a critical
section, an RCU epoch — transplanted into the Rust-for-Linux abstraction that
*encodes* the invariant in its type system (`SpinLock<T>`, `Rcu<T>`), and
verified not by output comparison (wrong equivalence for concurrency) but by
the kernel's own **dynamic sanitizer battery** — KCSAN, lockdep, KUnit — run
stock-vs-transplant under load, accepting only "no new findings vs. baseline."
For everything else — the pure and struct-reading majority — the C original is
its own oracle, and the differentials/trace-replays above apply. The full
design is [`docs/design.md`](docs/design.md); the honest accounting of what
can never be reached is `dream/SWEEP.md`.

## Honest scope

- The output is **unsafe-first** Rust (repr(C), raw pointers) — coverage first,
  idiom later. Refinement to safe Rust is the optional second pass.
- ~11% of the kernel (container_of webs, per-cpu, RCU-deref, ops tables) is
  flagged **C-forever** by the census — the same residue a from-scratch Rust
  kernel would keep.
- "Verified" is always qualified: this repo distinguishes *bit-exact
  differential* / *trace-equal* / *exhaustively proven* / *model-checked* /
  *boot-attested*, and the purity router quarantines anything a value
  differential would over-credit. A silent sanitizer is never treated as proof.
- This is a private research ratchet, not an upstream submission (though
  `rfc-export/` emits checkpatch-clean series if that day comes).

## Relationship to Rust-for-Linux

R4L is the landing zone, not a competitor: humans design the safe abstractions;
Lockstep applies them at scale to existing C, gated by the kernel's own
sanitizers. Where a region is too subtle for the machine, it falls back to
exactly the human process R4L already runs.

## Reading order

1. `dream/SUMMARY.md` — the arc in one file
2. `dream/RESEARCH.md` + `dream/SWEEP.md` — the denominator and the walls
3. `dream/PRIOR-ART.md` — what everyone else does; the whitespace here
4. `dream/COSTDOWN.md` — why the whole thing costs ~a lunch in tokens
5. Or take the [course](https://github.com/asonkiya/lockstep-course) — 16
   lessons whose exercises run these gates.

## License

MIT
