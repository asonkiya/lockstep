# Lockstep — learning syllabus & teaching handoff

> **For the teaching agent.** You are tutoring the project owner, who is a strong
> engineer but has **no background in kernel concurrency, memory models, or
> Rust-for-Linux**. Your job is to take them from zero to "can reason about what
> Lockstep does and make decisions about it." Rules:
> - Teach **one module at a time**, in order. Do not dump the whole thing.
> - Lead with **intuition and a concrete example** before any jargon. Define every
>   term the first time it appears.
> - End each module with the **checkpoint questions**; do not advance until they
>   can answer them in their own words. If they miss one, re-explain that piece a
>   different way.
> - Ground everything in **this repo's real artifacts** (paths are given). When
>   possible, have them open the file and read it with you.
> - Be honest about what is proven vs. speculative — this project prizes that.
> - Keep each module to ~10–15 minutes of reading + discussion. This is a course,
>   not a lecture.
>
> The owner built the parent project (CGIR) and the Lockstep design; they know the
> *rewrite/verification* machinery cold. The gap is purely the **concurrency
> domain**. Spend your time there.

---

## The 60-second version (read this first, together)

An AI can already rewrite a **pure function** (input → output, no shared state)
from C to Rust and *prove* it didn't change behavior — that's the parent project,
CGIR, and it works (146/146 functions verified, real kernel crypto routines
running in a booting kernel). But **most of an operating system is not pure
functions.** Most of it is code whose entire meaning is *how it coordinates
multiple CPUs touching the same memory at the same time* — locks, ordering,
ownership. You cannot rewrite that a function at a time, and you cannot check it
by comparing outputs, because the bug isn't a wrong output — it's two CPUs racing.

**Lockstep is the machine for that part.** It rewrites *regions* of concurrent C
into Rust that encodes the coordination rules in its type system, and it checks
correctness with the kernel's own **race and deadlock detectors** instead of
output comparison. This syllabus teaches you the concurrency domain well enough
to understand and steer that.

Where we are right now: **M0 just ran** — we booted a real Linux kernel under the
race/deadlock detectors and confirmed a clean baseline. That's the oracle coming
online. Everything past M0 is ahead of us.

---

## Module 1 — Why you can't just AI-rewrite an OS

**Goal:** understand the exact boundary between what CGIR already solved and what
Lockstep exists for.

**Intuition.** Two kinds of code:
1. A function like `mul_by_x(w)` — takes a number, returns a number, touches
   nothing else. Rewrite it, run both versions on the same inputs, compare
   outputs. If they always match, you're done. **CGIR does this.** (Real example:
   `benchmarks/kernel_gate/` in the CGIR repo rewrote exactly this to Rust and
   verified it inside a booting kernel.)
2. A function that grabs a lock, walks a list other CPUs are also modifying,
   updates a shared counter, and releases the lock. Its "output" isn't a return
   value — it's *"the shared state stayed consistent even though 3 other CPUs were
   hitting it."* You can't check that by comparing return values on one thread.

**Why the function boundary fails for #2:** the correctness rule (this lock
protects this data) is spread across *many* functions — the lock is taken in one
place, the data is touched in five others. Rewrite one function in isolation and
you've thrown away the rule.

**The takeaway:** CGIR's own analysis already sorts code into "pure" (it handles)
and "not pure" (it refuses). Lockstep's input is exactly that refused pile.

**Read together:** `docs/design.md` §1 ("Why a second project") — especially the
`foo(struct request *rq)` example. Have them point at *which line* is the "meaning"
(the `spin_lock` / `smp_store_release`, not the arithmetic).

**Checkpoint:**
- In your own words, what makes a function un-rewritable by the "compare outputs"
  method?
- Why doesn't rewriting one function at a time work when a lock is involved?

---

## Module 2 — The one bug that matters: the data race

**Goal:** understand what a data race actually is, because it's the thing every
tool and technique here is built around.

**Intuition.** A "data race" = two CPUs access the same memory location at the same
time, at least one is writing, and there's no coordination forcing an order. The
result is *undefined* — not "you get one of the two values," but genuinely broken
(the compiler and CPU are allowed to assume races never happen, so they optimize
in ways that shatter when one does).

**Concrete example** (walk through this by hand):
```
  Shared: counter = 0
  CPU A:  counter = counter + 1     CPU B:  counter = counter + 1
```
Both read 0, both compute 1, both write 1. Two increments, result is 1, not 2. Now
imagine `counter` is "how many references point at this object, so I know when to
free it." A lost increment → freed too early → **use-after-free** → security bug.
This is not hypothetical; it's the most common serious kernel bug class.

**The fix vocabulary (define each):**
- **Lock / mutex / spinlock** — a token; only the CPU holding it may touch the
  protected data. Forces the accesses into an order.
- **Atomic** — an operation the hardware guarantees happens all-at-once (an atomic
  increment can't be split into read/compute/write, so it can't be lost).

**The takeaway:** almost all of Lockstep's difficulty is "which lock is supposed
to protect which piece of data" — and in C that is *convention*, never written
down. Hold that thought; it's the central hard problem (Module 6).

**Checkpoint:**
- Draw the racing-increment example and explain why the answer is 1.
- What's the difference between a lock and an atomic?
- Why is a lost reference-count increment dangerous?

---

## Module 3 — Memory ordering (the subtle one; go slow)

**Goal:** understand that "when did CPU B *see* what CPU A wrote" is itself a rule
that can be right or wrong — and that this is the hardest thing to preserve in a
rewrite.

**Intuition.** You'd think if CPU A writes `data = 42` then `ready = true`, CPU B
that sees `ready == true` will also see `data == 42`. **On a multi-CPU machine,
not guaranteed.** CPUs and compilers reorder writes for speed. CPU B can see
`ready == true` while still seeing the *old* `data`. This is a real bug and it's
invisible on a single core.

**The fix vocabulary:**
- **Memory barrier / fence** — an instruction that says "don't let writes cross
  this line." A **release** barrier on the write side + an **acquire** barrier on
  the read side together guarantee: if B sees the flag, B sees everything A did
  before setting it. In the kernel these are `smp_store_release` /
  `smp_load_acquire`.

**Why this is the scary part of a rewrite:** Rust has its own version of these
(`Ordering::Release` / `Ordering::Acquire`). Translating a C `smp_store_release`
to the right Rust `Ordering` is a *semantic* claim about visibility across CPUs —
and getting it subtly wrong produces a bug that appears once a month under load
and never in a test. This is why Lockstep's verification can't be "run it once and
compare" — it needs a tool that actively hunts for these (Module 4).

**Read together:** `docs/design.md` §3.1, the `reads-from` / `publish/subscribe`
bullets — connect them to this module's flag example.

**Checkpoint:**
- Explain how CPU B can see `ready == true` but `data` still old.
- What does a release/acquire pair guarantee?
- Why can't you catch a wrong memory-ordering by running the code once?

---

## Module 4 — How you catch these bugs: the sanitizers (the oracle)

**Goal:** understand the tools that *are* Lockstep's verifier, because the whole
project's trustworthiness rests on them. **This is the module most tied to what's
running right now (M0).**

**The problem restated:** you can't prove concurrency correctness by comparing
outputs. So the "oracle" (the thing that says pass/fail) is a set of **dynamic
detectors** — tools that run the real kernel under stress and *watch* for the bad
patterns.

**The four (define each, plainly):**
- **KCSAN** (Kernel Concurrency SANitizer) — the data-race detector. Instruments
  every memory access; when it catches two unsynchronized CPUs hitting the same
  address, it prints a `BUG: KCSAN` report. This is the primary oracle — it sees
  exactly the Module 2/3 bugs.
- **lockdep** — the lock-order validator. Watches every lock acquire/release; if
  the code could deadlock (CPU A holds lock 1 waiting for lock 2 while CPU B holds
  2 waiting for 1), it prints a splat *even if the deadlock didn't happen this
  run*. It catches the *possibility*.
- **KUnit / kselftest** — the kernel's own functional tests (does the code still
  do the right thing at all). This is the direct descendant of CGIR's "run the
  tests" gate.
- **syzkaller** — a fuzzer that hammers the kernel with random system calls to
  *create* the concurrency stress that makes the above fire. (Later rung; not yet
  wired.)

**The honesty point (important to this project):** these are **sound-ish, not
complete.** KCSAN finding a race means there's really a race. KCSAN finding
*nothing* does **not** prove there are no races — only that it didn't observe one
under this workload. So Lockstep's claim is never "provably race-free"; it's "adds
no new race/deadlock the detectors can find under stress, where the original was
clean." Weaker than a proof, and stated as such.

**The gate design:** a transplant is accepted only if it adds **no new** KCSAN or
lockdep finding versus the **baseline** (the reading on unmodified code). Not "zero
reports" — the *delta*. That's why M0's whole job is capturing a trustworthy
baseline first.

**Read together:** `m0/baseline.sh` (the actual script running now) and
`m0/README.md`. Point out the exact config flags: `-e KCSAN`, `-e PROVE_LOCKING`
(that's lockdep), `-e KUNIT`. Then look at `m0/baseline-findings.txt` — the
extracted findings file.

**Checkpoint:**
- What does KCSAN do, and what does it *not* prove if it's silent?
- What's the difference between what KCSAN catches and what lockdep catches?
- Why is the gate "no *new* findings vs. baseline" instead of "zero findings"?
- (Point at the running M0 result) — what does "KCSAN reports: 0, lockdep splats:
  0" on stock code mean, and why did we need it *before* testing any transplant?

---

## Module 5 — The target: Rust-for-Linux and "encode the rule in the type"

**Goal:** understand what we're rewriting *into*, and why Rust specifically.

**Intuition.** The C way: "there's a lock called `q->lock`, and by convention
everyone agrees to hold it before touching `q->items`. Nothing enforces this; a
new contributor who forgets causes a race." The Rust way: make it *impossible to
touch the data without holding the lock* — the data lives *inside* the lock object,
and the only way to get at it is to lock, which hands you a temporary pass that
auto-returns when you're done. The rule stops being convention and becomes a thing
the compiler checks.

**The vocabulary (these are pre-built by the Rust-for-Linux project; we don't
invent them):**
- `SpinLock<T>` — a spinlock with the data `T` *inside* it. `.lock()` returns a
  guard; you can only reach `T` through the guard; the guard releasing = the lock
  releasing. Forgetting to unlock becomes impossible.
- `Rcu<T>` — the Rust wrapper for RCU (a kernel pattern for "many readers, rare
  writers" without locking readers at all — worth a sentence, not a deep dive yet).
- `KBox<T>` / `Arc<T>` — ownership: exactly one owner frees it (`KBox`), or a
  reference count frees it when the last user drops it (`Arc`). This is the
  language-level answer to the use-after-free from Module 2.

**Rust-for-Linux (R4L)** = the real, upstream effort to let kernel code be written
in Rust, which ships exactly these safe wrappers. **Lockstep does not compete with
it — it feeds it.** R4L is humans hand-designing abstractions; Lockstep is a
machine that *applies* those abstractions to existing C at scale. Where a region
is too subtle for the machine, it falls back to the human R4L process.

**The map (define "region"):** Lockstep's unit of work is a **semantic region** —
a span of code governed by one coherent concurrency rule (one critical section,
one RCU read epoch, one allocation-to-free ownership span), *not* a function. It
transplants a region into the R4L abstraction that encodes that region's rule.

**Read together:** `docs/design.md` §3.2 (the C-idiom → R4L-target table) and §2
(the four region kinds).

**Checkpoint:**
- How does `SpinLock<T>` turn a convention into a compiler-checked rule?
- Why is the unit of rewrite a "region" and not a "function"?
- Is Lockstep competing with Rust-for-Linux? Explain the relationship.

---

## Module 6 — The central hard problem: which lock protects which data

**Goal:** understand the one thing that makes this a research project, not a port.

**Intuition.** To transplant a critical section into `SpinLock<T>`, you must know
*which fields* `T` should contain — i.e. exactly which data that lock protects. In
C, **this is never written down.** It's convention, scattered across the codebase.
Inferring it is the crux.

**The realistic answer (not hand-waved):** hybrid.
- *Static* analysis proposes candidate edges ("this lock seems to guard these
  fields, based on what's touched between lock/unlock").
- *Dynamic* confirmation: **lockdep** already tracks, at runtime, which lock class
  is held during which memory access. So we run the kernel and check our static
  guesses against what lockdep actually observed.
- That's why the design says M1's success criterion is literally *"the extracted
  lock→data map matches lockdep's runtime observations"* — not "provably complete."

**Other honest limits (from `docs/design.md` §5):**
- Regions aren't always cleanly nested (lock taken in one function, released in
  another; error paths). Some regions won't be transplantable and must be
  *detected and skipped with a reason* — exactly like CGIR skips what it can't
  lift.
- Some C patterns have no R4L equivalent yet → out of scope until one lands
  upstream. Flagged, not forced.

**Read together:** `docs/design.md` §5 ("The hard problems, stated honestly"). This
is the most important section for the owner to internalize — it's the honest edge
of the whole project.

**Checkpoint:**
- Why can't you just read the C to know which lock protects which data?
- How does lockdep help *confirm* a guess even though it can't *make* the guess?
- Name one kind of region Lockstep will deliberately refuse, and why refusing
  (with a reason) is the right move.

---

## Module 7 — The milestone ladder & "you are here"

**Goal:** see the whole plan and locate the present moment in it.

Walk the M0–M5 ladder in `docs/design.md` §4. The one-line version:
- **M0 — sanitizer baseline.** *(JUST RAN.)* Boot a kernel under KCSAN + lockdep,
  confirm a clean reading on stock code. The oracle comes online. ✅ done when the
  baseline is clean and reproducible.
- **M1 — extract the concurrency IR** for one small subsystem; prove the lock→data
  map matches lockdep.
- **M2 — transplant one critical section by hand**, pass the M0 gate (and prove a
  deliberately-broken version gets REJECTED — the negative control, a habit
  inherited from CGIR).
- **M3 — the model does the transplant** from the IR + R4L catalog; same gate.
- **M4 — sweep a whole subsystem**, dependency-ordered.
- **M5 — emit upstreamable R4L-shaped patches** with the sanitizer evidence
  attached. The honest end state: *maintainer-reviewable patches*, not a magic
  button.

**Where we are:** end of M0. The next real work is M1 — picking a small,
well-locked subsystem and extracting its concurrency IR.

**Checkpoint:**
- What does each rung add that the previous didn't?
- Why does every rung include a "the broken version must be rejected" check?
- What's the honest *end state* of the whole project (it's not "the kernel is now
  all Rust")?

---

## Module 8 — How Lockstep sits on top of CGIR (tie it together)

**Goal:** connect the new project to the one the owner already knows.

CGIR (the parent) provides, and Lockstep consumes:
- the **call graph + purity/effects classification** — to find region boundaries
  and know which callees are already pure (CGIR-rewritable) vs. themselves
  concurrent;
- the **gate machinery** — the containerized kbuild + QEMU harness. `m0/baseline.sh`
  literally reuses CGIR's `cgir-kernel-gate` Docker image and kernel-tree volume.
  **M0 is CGIR's rung-4 gate plus sanitizer configs.**

So the frontier moves inward from both sides: CGIR eats the pure core, Lockstep
eats the concurrent region around it, and the boundary between "done" and "hard" is
always explicit.

**Checkpoint:**
- Name two things Lockstep gets from CGIR instead of rebuilding.
- In one sentence: what's the division of labor between the two projects?

---

## Appendix — glossary (quick reference for the owner)

- **data race** — two CPUs touch the same memory, ≥1 writing, no coordination;
  undefined behavior.
- **lock / spinlock / mutex** — token that serializes access to protected data.
- **atomic** — hardware-indivisible operation (can't be interrupted/split).
- **memory barrier / fence; release/acquire** — controls *when* one CPU's writes
  become visible to another.
- **RCU** — read-copy-update; lets readers proceed lock-free while writers make
  copies; readers/writers coordinated by "grace periods."
- **critical section** — code between a lock acquire and release.
- **KCSAN** — dynamic data-race detector (the primary oracle).
- **lockdep** — dynamic lock-ordering / deadlock validator.
- **KUnit / kselftest** — the kernel's functional test frameworks.
- **syzkaller** — coverage-guided kernel syscall fuzzer (the stress generator).
- **LKMM** — Linux Kernel Memory Model; the formal spec of all the above ordering
  rules. Lockstep's IR vocabulary is drawn from it.
- **Rust-for-Linux (R4L)** — upstream effort + safe abstractions (`SpinLock<T>`,
  `Rcu<T>`, `KBox<T>`) for writing kernel code in Rust; Lockstep's output target.
- **region** — Lockstep's unit of rewrite: a span governed by one concurrency rule.
- **CGIR** — the parent project; rewrites & verifies *pure* code, feeds Lockstep.

---

*Written 2026-07-25 as a teaching handoff. When this syllabus and `docs/design.md`
disagree, `design.md` is canonical for the plan and this file should be corrected.*
