# Lesson 03 — Memory ordering: when did the other CPU see it?

## For the teaching LLM

- **This is the module where learners think they get it but don't.** The idea "writes can be reordered" sounds simple and they will nod along. Do not let a nod count as understanding. The real, load-bearing insight is that *there is no single global timeline of memory* — each CPU has its own view, and "when did CPU B see CPU A's write" is a genuine, answerable-or-unanswerable question. Go slow. Budget more time here than any other module.
- **Anchor everything to one mental model: message passing.** CPU A writes the *data*, then sets a *flag* ("the data is ready"). CPU B waits for the flag, then reads the data. The whole module is: *can B see the flag set but still read stale data?* Answer: yes, unless you add ordering. Keep returning to this single picture; do not introduce a second example until they own this one.
- **Make them predict before you reveal.** Before you tell them the answer, ask "so if A does `data = 42` then `ready = true`, and B sees `ready == true`, what does B read for `data`?" Almost everyone says "42, obviously." Let them commit to that wrong answer out loud. The surprise is the lesson.
- **Be honest that they cannot easily reproduce this on their laptop.** x86 and Apple Silicon have strong-ish cache coherence that *hides* most reordering; the bug is real in the abstract machine (and on weakly-ordered hardware, and after compiler reordering) but a naive demo will "work" every time. That "it works every time and is still wrong" is itself the point — do not let them conclude the bug isn't real because their demo passed.
- **Separate the two reorderers.** The *compiler* reorders (and assumes no data races, so it can hoist/sink/fold aggressively) and the *CPU* reorders (store buffers, out-of-order execution). Both break the naive intuition. Release/acquire constrains both. Mention both; don't drown them in microarchitecture.
- **End on the Lockstep hook.** The reason this module exists is that translating a C `smp_store_release` into the *wrong* Rust `Ordering` is a silent, semantic bug that no output comparison will ever catch — which is exactly why Lockstep must verify with a dynamic race detector (KCSAN), not by comparing return values. Do not skip §"Connects to Lockstep."

## Objectives

By the end, the learner can:

1. Explain, using the flag/data example, how a reader can observe a flag as set while still reading stale data behind it.
2. State precisely what a **release/acquire pair** guarantees ("if the reader sees the released flag, it sees everything the writer did before the release").
3. Map the C vocabulary (`smp_store_release` / `smp_load_acquire`) to the Rust vocabulary (`Ordering::Release` / `Ordering::Acquire`) and explain that this mapping is a *claim about cross-CPU visibility*, not a syntactic rename.
4. Explain why running the code once (or a thousand times) on one machine cannot catch a wrong memory-ordering — the failure is rare, load-dependent, and hardware-hidden.
5. Reason through the classic **message-passing (MP) litmus test** and say which outcomes a barrier forbids.

## Conversation flow

### Hook (get a wrong prediction on the record)

Start concrete, no jargon:

> "Two CPUs share memory. CPU A runs these two lines, in this order:
> ```
> data  = 42;
> ready = true;
> ```
> CPU B is spinning:
> ```
> while (ready == false) { /* wait */ }
> print(data);
> ```
> A finishes both lines. B's loop finally sees `ready == true` and falls through. **What does B print?**"

Let them answer. The overwhelming majority say **42** — "A set data *first*, so by the time ready is true, data must be 42." Write their answer down. This is the intuition we are about to break.

### The reveal: B can print 0 (or garbage)

> "On a real multi-CPU machine, B can print the *old* `data` — 0, whatever was there before — even though it already saw `ready == true`. The two writes A made can become *visible to B in the opposite order.* B sees `ready` flip before it sees `data` change."

Let that land. Then explain *why*, at two levels:

- **Compiler.** The compiler sees two independent stores (`data` and `ready` don't depend on each other) and is free to emit them in either order, or move them around, because the C/Rust abstract machine assumes *no other thread is watching without synchronization.* From the compiler's point of view, on a single thread, the order is unobservable — so it may reorder for speed.
- **CPU.** Even if the compiler keeps the order, the hardware has a **store buffer**: CPU A's write to `data` may still be sitting in A's private buffer, not yet pushed to shared memory/cache, while the write to `ready` has already propagated. B, reading shared memory, sees the newer flag and the older data. Different CPUs genuinely have different *views* of memory at the same instant. There is no global "now."

Key framing to say out loud: **"Program order on one CPU is not the same as visibility order on another CPU."** That sentence is the whole module.

### Socratic checks (predict-then-reveal)

Run these one at a time. Sample answers included so you can calibrate.

**Check 1.** "Is this a bug in the CPU or the compiler? Whose fault is it?"

- *Right-ish:* "Neither — it's allowed behavior. The abstract machine only promises single-thread-visible order; across threads you get nothing unless you ask. So it's *my* fault for not asking." (Good — they've internalized that reordering is a feature, not a defect.)
- *Wrong:* "It's a hardware bug / broken CPU." → Re-explain: this is documented, intended, and required for performance; every mainstream CPU does it. The contract you relied on was never promised.

**Check 2.** "I run this loop 10 million times on my laptop and it always prints 42. Did I just prove it's safe?"

- *Right:* "No — my hardware may be hiding it, and the compiler may not have reordered *this* build. It working is not it being correct." (Excellent — this is objective 4.)
- *Wrong:* "Yes, 10 million passes is plenty." → This is the dangerous misconception. Push back hard (see Misconceptions). The bug is rare and load/hardware-dependent; absence of a crash is not a proof.

**Check 3.** "How would you *force* B to see 42 whenever it sees `ready == true`?"

- *Right:* "Put a barrier between A's two writes and a matching one on B's read, so the flag can't become visible before the data." → Perfect segue to the fix.
- *Wrong / partial:* "Make `ready` volatile" or "add a lock." → Volatile alone does *not* give cross-CPU ordering in C (common myth). A lock *does* work (it contains release/acquire internally) but is heavier than needed; we want the primitive underneath.

### The fix: release / acquire

Now give the vocabulary, tied straight back to the example.

> "A **release** barrier on the *write* side and an **acquire** barrier on the *read* side, used as a pair, give you exactly one guarantee — but it's the one you want:
>
> **If B's acquire-load sees the value A's release-store wrote, then B is guaranteed to see *everything A did before the release-store*.**
>
> The release 'publishes' all of A's prior writes; the acquire 'subscribes' to them. So if B sees `ready == true` via an acquire-load, `data == 42` is guaranteed. The flag and the data travel together."

The two-sided requirement is essential — say it explicitly: **you need both halves.** A release with no matching acquire, or an acquire with no matching release, buys you nothing. It is a handshake.

The concrete primitives:

| | Write side (publish) | Read side (subscribe) |
|---|---|---|
| **Linux kernel C** | `smp_store_release(&ready, true)` | `smp_load_acquire(&ready)` |
| **Rust** | `ready.store(true, Ordering::Release)` | `ready.load(Ordering::Acquire)` |

Emphasize the mapping is *meaning-preserving by intent*: `smp_store_release` ⟷ `Ordering::Release` is asserting "these two express the same cross-CPU visibility rule." That is a semantic claim someone has to get right — and it's the crux of the Lockstep tie-in at the end.

### Why "run it once" can't catch this (drive it home)

Before the exercises, make objective 4 unforgettable:

- The failure requires the compiler to reorder (build-dependent) **and/or** the two writes to propagate out of order (timing- and contention-dependent).
- On x86 / Apple Silicon, store→store ordering is largely preserved by hardware, so the *hardware* half often won't fire at all — the bug hides.
- It shows up "once a month under load" on a busy server with weakly-ordered CPUs or an aggressive build, and never in your unit test.
- Therefore: **the correctness of a barrier is not a property of any single run.** You cannot sample your way to it. You need a tool that reasons about *what interleavings are possible*, not one that observes *what happened this time*. Hold that thought — it is Module 4, and it is why Lockstep's verifier is KCSAN, not `assert_eq!`.

## Misconceptions to catch

- **"If I wrote it first, it must be seen first."** The single hardest one. There is no global write order that all CPUs agree on. "First in program order on A" says nothing about "first visible on B." Re-anchor on the store-buffer picture: A's earlier write can still be in A's private buffer while its later write is already public.
- **"This can't happen — I've never seen it."** Never observing it ≠ it can't happen. Their hardware and their build are *hiding* it. Analogy: a race in a bridge that only collapses when 10,000 people walk in step — you've crossed it fine a thousand times. Absence of a crash is not evidence of correctness for undefined/unordered behavior; it's just luck plus a forgiving CPU.
- **"`volatile` fixes it."** In C, `volatile` prevents the *compiler* from eliding/reordering that specific access, but gives **no** cross-CPU ordering and no atomicity guarantee. It is not a memory barrier. (This trips up people coming from single-threaded embedded C.)
- **"A lock is overkill, but release/acquire is basically a lock."** Release/acquire is one-directional ordering on one variable, far cheaper than mutual exclusion. A lock provides release/acquire *plus* mutual exclusion. Different tools; don't conflate.
- **"Relaxed is fine as long as the operation is atomic."** Atomicity (the write isn't torn) and ordering (when others see it) are *independent* properties. `Ordering::Relaxed` is atomic but gives zero ordering relative to other variables — which is exactly the bug we're studying. This is the trap the Rust exercise makes visible.
- **"Acquire/release orders *these two* operations."** It orders *everything before the release* with respect to *everything after the acquire* — not just the flagged variable. The flag is the carrier; the payload is all prior writes.

## Exercises (reps)

The learner has `rustc`, Python, node, and docker. Memory reordering is genuinely hard to *reproduce* on a coherent laptop (be honest about this in the exercises), so these are built to cement the **mental model** and the **vocabulary mapping**, not to force a crash.

---

### Exercise (a) — The release/acquire handshake in Rust, then the Relaxed trap

**Goal:** Write the message-passing pattern correctly with `Release`/`Acquire`, understand the guarantee, then see the *same* code with `Relaxed` and articulate why it is unsound in principle even though it will print 42 on your machine.

**Language:** Rust (`rustc`, no crates).

**Starter code** (`mp_release.rs`):

```rust
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread;

fn main() {
    // Shared state: DATA is the payload, READY is the "message is published" flag.
    let data = Arc::new(AtomicUsize::new(0));
    let ready = Arc::new(AtomicBool::new(false));

    // Producer (CPU A): write the data, THEN publish the flag with a release store.
    let producer = {
        let data = Arc::clone(&data);
        let ready = Arc::clone(&ready);
        thread::spawn(move || {
            data.store(42, Ordering::Relaxed);       // (1) the payload
            ready.store(true, Ordering::Release);    // (2) publish: release fence
        })
    };

    // Consumer (CPU B): wait for the flag with an acquire load, THEN read the data.
    let consumer = {
        let data = Arc::clone(&data);
        let ready = Arc::clone(&ready);
        thread::spawn(move || {
            // Spin until we observe the published flag.
            while !ready.load(Ordering::Acquire) {   // (3) subscribe: acquire fence
                std::hint::spin_loop();
            }
            // GUARANTEE: because (3) saw what (2) released, we see (1) too.
            let seen = data.load(Ordering::Relaxed);
            println!("consumer saw data = {seen}");
            assert_eq!(seen, 42, "release/acquire guarantees this");
        })
    };

    producer.join().unwrap();
    consumer.join().unwrap();
    println!("ok");
}
```

**Task:**

1. Run it: `rustc -O mp_release.rs -o mp_release && ./mp_release`. It prints `data = 42` and `ok`.
2. Explain *in one sentence* why the `data.load` (which is itself `Relaxed`) is nonetheless guaranteed to see 42. (Hint: the guarantee comes from the acquire on `ready`, not from the ordering on `data`.)
3. Now make the **Relaxed** variant: copy to `mp_relaxed.rs` and change **only** the two flag operations — `ready.store(true, Ordering::Relaxed)` and `ready.load(Ordering::Relaxed)`. Keep the `assert_eq!`. Run it.
4. It will (almost certainly) still print 42 and pass on your laptop. Write down why that is **not** evidence it's correct, and what invariant you just threw away.

**Solution / expected reasoning:**

- Step 2: "The acquire-load on `ready` at (3) synchronizes-with the release-store at (2); that handshake makes *all* of the producer's writes-before-(2) — including `data = 42` at (1) — visible to the consumer after (3). The ordering on the `data` access itself is irrelevant; the flag carries the payload."
- Step 4: "With both flag ops `Relaxed`, there is **no** synchronizes-with edge. Nothing orders the producer's `data` write before the consumer's `data` read. The consumer is now permitted (by the memory model) to observe `ready == true` while still reading `data == 0`. It printed 42 only because this CPU/build happened not to reorder or delay the write — pure luck. I threw away the publish/subscribe guarantee; the program is unsound even though this run passed." The `assert_eq!` firing is *allowed* under Relaxed; it simply didn't this time.

**What it reinforces:** The guarantee is a *pairing* on the flag, not a property of the data access; `Relaxed` is atomic-but-unordered; and "it passed" is not "it's correct." This is objectives 2 and 4 made tactile, plus the direct C→Rust vocabulary map (objective 3): the `Release`/`Acquire` here are the literal Rust spelling of `smp_store_release`/`smp_load_acquire`.

---

### Exercise (b) — Pencil-and-paper: reorder the operations

**Goal:** Enumerate possible final states of the message-passing pattern, with and without a barrier, to see exactly which outcome the barrier forbids.

**Language:** paper (no machine).

**Setup.** Shared memory starts `data = 0`, `flag = 0`. Two CPUs run these instructions. There is *no* barrier (all plain accesses):

```
CPU A                 CPU B
  A1: data = 1          B1: r_flag = flag     // read flag into local r_flag
  A2: flag = 1          B2: r_data = data     // read data into local r_data
```

**Task:**

1. On a system that allows A's two *stores* to become visible to B in either order, and allows B to observe them independently, list the possible `(r_flag, r_data)` pairs B can end up with. There are four candidate combinations — decide which are achievable.
2. Identify the **one** outcome that is the bug: B saw the flag but not the data.
3. Now add a barrier: A2 becomes `smp_store_release(&flag, 1)` and B1 becomes `r_flag = smp_load_acquire(&flag)`. Which of the four outcomes is now **forbidden**? State the guarantee that forbids it.

**Solution:**

- Step 1 — the four combinations of `(r_flag, r_data)` and whether each is achievable *without* a barrier:
  - `(0, 0)` — **possible.** B ran before A's writes propagated. Saw neither.
  - `(0, 1)` — **possible.** B saw the data write but not yet the flag (writes propagated data-first, or B read flag early then data late). Not a bug — B correctly waits/retries because flag is still 0.
  - `(1, 1)` — **possible.** B saw both. The happy case.
  - `(1, 0)` — **possible without a barrier, and this is the bug.** B saw `flag == 1` but `data == 0`: the flag became visible before the data. B thinks the message is published and reads garbage.
- Step 2 — the bug is `(1, 0)`.
- Step 3 — with `smp_store_release` on A2 and `smp_load_acquire` on B1, the outcome `(1, 0)` becomes **forbidden.** Guarantee: if B's acquire-load reads the value the release-store published (`r_flag == 1`), then everything sequenced before the release on A (i.e. `data = 1`) is guaranteed visible to everything sequenced after the acquire on B (i.e. the read of `data`). So `r_flag == 1` ⟹ `r_data == 1`. The other three outcomes remain legal.

**What it reinforces:** Objective 1 (the stale-read mechanism) and objective 2 (precisely what release/acquire forbids — one specific outcome, not "all reordering"). It also kills the "if I wrote it first it's seen first" misconception by making the illegal-vs-legal outcomes explicit.

---

### Exercise (c) — (stretch) The real MP litmus test

**Goal:** Connect the hand example to how kernel/memory-model people actually specify this — a *litmus test* — and reason about it in the formal frame.

**Language:** reading + reasoning (optionally the `herd7`/`klitmus` tools, not required).

**Setup.** The canonical **MP (message passing)** litmus test, in the Linux-kernel-memory-model style:

```
C MP
{ }

P0(int *data, int *flag) {     // the writer
    WRITE_ONCE(*data, 1);
    smp_store_release(flag, 1);
}

P1(int *data, int *flag) {     // the reader
    int r0 = smp_load_acquire(flag);
    int r1 = READ_ONCE(*data);
}

exists (1:r0=1 /\ 1:r1=0)      // the question: can the reader see flag=1 but data=0?
```

**Task:**

1. Translate the `exists` clause into English. What real-world bug does `1:r0=1 /\ 1:r1=0` correspond to?
2. Reason: with `smp_store_release` / `smp_load_acquire` as shown, is that state reachable? Why?
3. Now mentally weaken both to plain `WRITE_ONCE`/`READ_ONCE` (no release/acquire). Is it reachable now? Why?
4. (Optional, if curious) The Linux kernel ships this exact test. Point yourself at the kernel source tree under `tools/memory-model/litmus-tests/` (e.g. `MP+pooncerelease+poacquireonce.litmus` for the ordered version and `MP+poonceonces.litmus` for the unordered one) and, if you want, run them under `herd7` from `tools/memory-model/`. Compare the tool's verdict to your reasoning.

**Solution:**

- Step 1: `1:r0=1 /\ 1:r1=0` means "the reader P1 loaded `flag == 1` (message published) but loaded `data == 0` (stale)." That is the stale-read / broken-message-passing bug — reader trusts the flag, gets garbage payload.
- Step 2: **Not reachable** with release/acquire. The release on `flag` orders the prior `WRITE_ONCE(*data,1)` before the flag publish; the acquire on `flag` orders the subsequent `READ_ONCE(*data)` after observing the flag. If `r0 == 1`, the release→acquire synchronization forces `r1 == 1`. `herd7` reports the `exists` as **never / forbidden.**
- Step 3: **Reachable** with only `WRITE_ONCE`/`READ_ONCE`. Those give single-copy atomicity (no torn/elided access) but **no ordering** between the two variables, so the flag can be observed before the data. `herd7` reports the `exists` as **sometimes / allowed.** This is the same distinction as Exercise (a)'s Release-vs-Relaxed, now stated in the formal LKMM frame Lockstep's IR is drawn from.

**What it reinforces:** Objective 5, and it plants the LKMM vocabulary (`READ_ONCE`, `smp_store_release`, litmus tests, the `exists` question) that Lockstep's concurrency IR is built on — the same relations (`reads-from`, publish/subscribe) named in `docs/design.md` §3.1.

## Mastery check

Do not advance until they can answer all three in their own words.

1. **The mechanism.** CPU A does `data = 42;` then `ready = true;`. CPU B spins until it sees `ready == true`, then reads `data`. Explain, concretely, how B can read `data == 0` — name the two independent reasons (compiler and CPU) it's allowed.
2. **The guarantee.** State exactly what a `smp_store_release` / `smp_load_acquire` pair (equivalently `Ordering::Release` / `Ordering::Acquire`) guarantees — and what it does *not* guarantee if only one side of the pair is present. Why is `Ordering::Relaxed` on both sides insufficient even though the operations are still atomic?
3. **Why one run can't catch it.** You transplant a C `smp_store_release` into Rust and pick `Ordering::Relaxed` by mistake. Your test suite runs the code a million times on your laptop and passes every time. Explain why that is not evidence the transplant is correct, and what kind of tool *could* catch the mistake.

## Connects to Lockstep

This module is the reason Lockstep's verifier is shaped the way it is — make the two connections explicit before moving on:

- **Why the oracle must be a dynamic race detector (KCSAN), not output comparison.** A wrong memory-ordering doesn't produce a wrong *return value* on a single thread — it produces a *stale read under contention*, rarely, on the right hardware, under load. Compare the outputs of the C and the Rust a million times on one machine and they'll match every time while the bug sits there dormant (exactly Exercise (a) step 4). So Lockstep can't gate on "same output." It gates on a tool that *watches for the unsynchronized access itself* — KCSAN, which instruments every memory access and reports when two CPUs touch the same address without a synchronizing edge. That is the only kind of check that can see the class of bug this whole module is about. (Full treatment in Module 4; this is the "why" behind it.)
- **Why translating C barriers to Rust `Ordering` is a real semantic claim.** When Lockstep rewrites a region, turning `smp_store_release(&rq->state, READY)` into `state.store(READY, Ordering::Release)` is *asserting the two express the same cross-CPU visibility rule.* Pick `Ordering::Relaxed` instead of `Release` and you've silently deleted the publish/subscribe guarantee — a bug that compiles, passes functional tests, matches the reference output, and races once a month in production. This is precisely why a barrier translation cannot be trusted on inspection or on a passing run; it has to face the dynamic race detector under adversarial load. The publish/subscribe pair you learned here is one of the four first-class **regions** Lockstep extracts (`docs/design.md` §2, "publish/subscribe pairs"), and the `smp_store_release`/`smp_load_acquire` edges are the LKMM `reads-from` relations its IR is built on (§3.1).
