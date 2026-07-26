# Lockstep lessons — the course

Seven conversational lessons that turn [`../SYLLABUS.md`](../SYLLABUS.md) into a
run-it-with-an-LLM course: concepts taught Socratically, with **runnable coding
exercises** to grind in the reps. Each lesson has a "For the teaching LLM" header
(how to pace and run it), a scripted-but-flexible conversation with sample
right/wrong learner answers, exercises with complete code + solutions, common
misconceptions, mastery checks, and a tie-back to where the project actually is.

**How to use:** hand one lesson at a time to an LLM with *"Teach me from this
lesson — run it as a conversation, don't dump it."* Do them in order; each builds
on the last. Do the exercises on your own machine (Python, `rustc`, Docker).

## The order

1. [Lesson 01 — Why you can't just AI-rewrite an OS](lesson-01-why-not-rewrite-an-os.md)
   — the CGIR/Lockstep boundary; pure vs. concurrent code. *(+ a Python race demo.)*
2. [Lesson 02 — Data races: the bug that matters](lesson-02-data-races.md)
   — lost updates → use-after-free; locks vs. atomics. *(Coding-heavy, Python.)*
3. [Lesson 03 — Memory ordering: when did the other CPU see it?](lesson-03-memory-ordering.md)
   — the subtle one; release/acquire barriers. *(Runnable Rust `Ordering` reps.)*
4. [Lesson 04 — The oracle: sanitizers that hunt races](lesson-04-sanitizer-oracle.md)
   — KCSAN / lockdep / KUnit / syzkaller; "silence isn't proof"; baseline-delta.
   *(Hands-on race detector.)*
5. [Lesson 05 — Rust: make the rule a compiler-checked type](lesson-05-rust-encodes-the-rule.md)
   — `SpinLock<T>`, ownership, `Send`/`Sync`. *(Rust reps: races that won't compile.)*
6. [Lesson 06 — The crux: which lock protects which data?](lesson-06-which-lock-protects-what.md)
   — why it's convention, and the static-guess + lockdep-confirm hybrid.
7. [Lesson 07 — The ladder & the big picture (capstone)](lesson-07-the-ladder-and-the-big-picture.md)
   — M0→M5, the negative control, how Lockstep sits on CGIR, the honest end-state.

## You are here

**M0 is done** — the sanitizer oracle is online and baselined (see
[`../../m0/RESULTS.md`](../../m0/RESULTS.md)). Lesson 4 uses your *real* M0
findings. The next real work is **M1** (extract a subsystem's concurrency IR).

## Notes on the exercises (verified on this repo's toolchain)

- **Python races:** modern CPython's GIL makes a bare `x += 1` effectively atomic,
  so the race exercises use `sys.setswitchinterval(1e-9)` + a `write(read()+1)`
  split across function calls to make the lost update *reliably* visible. This is
  a teaching trick to surface a real race the language usually hides — verified to
  drop ~25–40% of updates here.
- **Rust:** the "won't compile" exercises rely on the borrow checker rejecting
  shared mutable state across threads (`E0373`/`E0499`) — verified against
  `rustc`. They're the point: Rust catches at *compile time* the race KCSAN
  catches at *runtime*.
- **Lesson 4's race detector:** Go's `go run -race` is the easiest demo, but if Go
  isn't installed use the provided C + `gcc -fsanitize=thread` (ThreadSanitizer,
  KCSAN's userspace cousin) via Docker — that path works with just Docker.
