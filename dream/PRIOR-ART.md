# Prior art — has this been done, and what have others done that we haven't?

A sourced survey (three parallel research passes, 2026-07). Honest conclusion up
front: **the ingredients are a crowded, well-funded field and others are ahead on
rigor/idiom/scale; the one unclaimed thing is the fusion — an in-kernel,
concurrency-aware (KCSAN/sanitizer-gated) acceptance loop for Linux.**

## What is NOT novel (others do this, some better)

- **LLM C→Rust is a gold rush.** DARPA **TRACTOR** (2024–, ~$14M, ~6–7 teams) targets
  *idiomatic, safe* Rust; T&E by MIT Lincoln Lab, acceptance = compile + per-project
  test suite, staged batteries. [darpa.mil](https://www.darpa.mil/research/programs/translating-all-c-to-rust) ·
  [ll.mit.edu](https://www.ll.mit.edu/r-d/projects/translating-all-c-rust-tractor-benchmarks)
- **Differential testing as the acceptance gate is the field's dominant paradigm** —
  i.e. exactly our approach:
  - **Fluorine** — cross-language differential *fuzzing*, no pre-existing tests; 47%
    of real C/Go programs (best model). [arxiv 2405.11514](https://arxiv.org/abs/2405.11514)
  - **Syzygy** — differential testing, ~1M random inputs; Zopfli (~3k LOC/98 fns) to
    safe Rust. [arxiv 2412.14234](https://arxiv.org/abs/2412.14234)
  - **SACTOR** — **FFI-links Rust into the C and compares outputs** (the userspace
    twin of our in-kernel differential); real repos, idiomatic pass.
    [arxiv 2503.12511](https://arxiv.org/abs/2503.12511)
  - **RustAssure** — differential *symbolic* execution (KLEE); **caught 11 bugs
    fuzzing missed** — direct evidence dynamic differential is the leakier oracle.
    [arxiv 2510.07604](https://arxiv.org/abs/2510.07604)
  - **VERT** — property-based testing + **bounded model checking** (Kani) against a
    Wasm-lifted correct-by-construction oracle; 1,394 programs.
    [arxiv 2404.18852](https://arxiv.org/abs/2404.18852)
- **c2rust** — the incumbent: rule-based transpile of *any* C to unsafe Rust; no
  equivalence proof (re-run the C test suite; optional opt-in runtime cross-check).
  [github/immunant](https://github.com/immunant/c2rust)
- **LLMs have already touched kernel C.** **LLMigrate** translated the Linux
  `math`, `sort`, `ramfs` modules (GPT-4o), human edits <15% of final lines — but
  the gate was compile + manual review, no runtime/concurrency check.
  [arxiv 2503.23791](https://arxiv.org/abs/2503.23791)

## What others have done that we haven't (they're ahead)

- **Stronger verification:** VERT (bounded model checking), RustAssure (differential
  symbolic) are formally stronger than our *dynamic* differential — and RustAssure
  empirically caught bugs a fuzzer misses.
- **Idiomatic/safe output at quality:** TRACTOR's bar, SACTOR's idiomatic step. Ours
  is mostly faithful `#[repr(C)]`/unsafe; idiomatic was a sketch (M5).
- **Validated scale + peer review + benchmarks:** VERT 1,394 programs; CRUST-Bench;
  real repos. Ours is a solo demonstration on dozens of functions.
- **Full-C coverage:** c2rust compiles any C; our eligibility is a measured fraction.

## The genuine whitespace (empty across all three surveys)

1. **No in-kernel/boot-time behavioral acceptance gate.** Everyone verifies in
   userspace or against a lifted oracle; nobody compiles the Rust freestanding,
   links it into a real vmlinux, boots, and checks behavior at runtime. LLMigrate
   reaches kernel modules but stops at compile + manual.
2. **No concurrency/data-race equivalence inside the acceptance oracle.** Every
   translation gate above is single-threaded I/O comparison — blind to the bug class
   that dominates the kernel. The machinery exists only in *separate, non-LLM* tools:
   **RustMC** (GenMC race-checking for Rust, [arxiv 2502.06293](https://arxiv.org/abs/2502.06293)),
   **Concrat** (rule-based lock-API translation, [arxiv 2301.10943](https://arxiv.org/abs/2301.10943)).
   Nobody fused race-checking with translation acceptance — the Lockstep thesis.
3. **KCSAN/lockdep as an accept/reject gate for a rewrite** appears nowhere; syzbot
   can't even apply candidate patches. [syzbot docs](https://github.com/google/syzkaller/blob/master/docs/syzbot.md)
4. **Rust-for-Linux is 100% manual** (hand-written new Rust + hand-written safe
   wrappers over C ABIs); the community explicitly does not pursue wholesale
   conversion. From-scratch Rust OSes (Redox, Theseus, Hubris, Asterinas) are all
   clean-slate, not translations. [lwn 1050174](https://lwn.net/Articles/1050174/)

## Why the intersection is empty (honest)

- **Two non-overlapping communities:** translation research is PL/ML people who
  validate on userspace (free `main()`+stdin harness); R4L is kernel experts hand-
  writing Rust who reject wholesale conversion. The fusion needs both + a
  kbuild/QEMU/KCSAN harness neither builds.
- **No cheap oracle for the kernel:** a kernel function has no process/stdin to
  diff; you must boot a kernel to observe one.
- **The bugs are invisible to the standard oracle:** kernel correctness is locking
  discipline / race-freedom, which return-value testing cannot see — you need KCSAN
  specifically, and nobody wired it in as a gate.
- **The payoff is bounded and hard:** our own sweep put the ceiling at ~50–65% with
  an effect-tracing/config-coverage tax and an ~11% C-forever floor — a messy
  partial systems win, less publishable than a clean userspace benchmark.

## Net

We did not invent the loop or the differential gate — those are hot and funded, and
the literature is ahead on formal rigor, idiomatic output, and validated scale. The
defensible, unclaimed contribution is narrow and specific: **an in-kernel,
concurrency-aware acceptance loop for Linux, using the kernel's own race detector as
the equivalence oracle** — empty not because it is brilliant but because it sits in
the seam between two fields and demands infrastructure neither side builds.
