# Lesson 07 — The ladder, and how it all fits (capstone)

## For the teaching LLM

This is the wrap-up: the learner has now walked through six foundational modules (why concurrent code needs a different machine, data races, memory ordering, the sanitizer oracle, Rust-for-Linux abstractions, and the hard problem of lock-to-data inference). This lesson **ties it all together and maps the plan.**

- **Frame the arc:** You've been building up the pieces. Now we're looking at the whole staircase — a five-rung ladder from the baseline (M0, just complete) to the honest end state (M5, maintainer-reviewable patches with evidence).
- **Walk the rungs and the negative control:** At each rung, have the learner answer: "What is new here that we couldn't do before?" and "Why does this rung include a gate that *rejects* the deliberately-broken version?" That habit of thinking in negative controls is inherited from CGIR and it's the backbone of trustworthiness.
- **Zoom out to the system:** Lockstep doesn't rebuild the world — it stands on CGIR's shoulders. Call out what it gets for free (call graph, purity classification, the gate machinery) and what it has to do itself (concurrency IR, abstraction synthesis, the race/lock/fuzz oracle).
- **Be honest about the end state:** This is **not** "the kernel becomes all Rust automatically." It's "we have a machine that can take hard concurrent regions and transplant them into Rust-for-Linux abstractions, gated by the kernel's own sanitizers, emitting patches a human maintainer will review." That is the real shape of "rewrite the kernel" — bounded, transparent, and respecting the role of the person who owns the code.
- **Celebrate understanding:** By the end, the learner can reason about the whole project — where a piece of code lives, which oracle checks it, what it means if KCSAN passes, why the lock→data map matters for the boundary between M1 and M2. They're ready for M1.

---

## Objectives

By the end of this lesson, the learner will:

1. Walk the M0–M5 ladder and explain what each rung adds and why its negative control matters.
2. Articulate the division of labor between CGIR (pure code) and Lockstep (concurrent regions).
3. Name the three layers of the Lockstep oracle (KCSAN, lockdep, KUnit/syzkaller) and what each sees.
4. Understand the honest end state: patches, not a magic button; human review, not automation from first principles.
5. Reason about a hypothetical region and say which checks it must pass before acceptance.

---

## Conversation flow

### Opening: Zoom out

> You've now seen the six foundational pieces: why the problem exists, what a data race actually is, why memory ordering is subtle, which tools catch these bugs, what Rust abstractions solve them, and why extracting the lock→data map is the crux. Let's pull back and see the whole plan. Lockstep is a five-rung ladder from where we are right now (M0 complete) to the honest end state (maintainer-reviewable patches). Walk the rungs with me.

### The M0–M5 ladder

Walk each rung using the material from `docs/design.md` §4 and `m0/RESULTS.md`.

#### M0 — The sanitizer baseline (just ran)

**What happened:**
- We booted a real Linux kernel (7.2-rc4, 4 CPUs) in the CGIR container (the kbuild + QEMU harness that was already proven) under KCSAN + lockdep + KUnit.
- We captured a clean reading. lockdep: 0 splats. KCSAN: two known-benign intentional races in the printk ringbuffer (things that are *supposed* to race by design). KUnit tests: all pass.

**What this rung adds:**
- The **oracle is now online.** Before M0, we had no baseline — we couldn't tell if a transplant was better or worse. Now we have a measurement, reproducible, of what "clean" looks like on stock code.

**The negative control:**
> Here's the thing about oracles: if a gate can't reject the wrong answer, the gate is worse than useless — it's a lie. So M0 also proves the gate *works*. We did this in CGIR's rung 4 too: show that a **deliberately broken** version gets caught. For M0, that would mean: take the baseline-clean kernel, introduce a known-bad pattern (drop a lock, mess with a barrier order), re-run. KCSAN should scream. We haven't run that yet, but we will before moving to M1.

**Learner checkpoint question:**
- The baseline is "2 KCSAN findings, 0 lockdep splats." Does that mean the kernel is "clean"?
- Sample answer: *Yes, in the sense that these are known-benign (intentional races in a lockless structure). The point of M0 is to capture "this is what clean looks like on stock code." Every future transplant is diffed against this — if it has the same 2 findings plus nothing new, it passes. If it has a new finding, it fails.*

#### M1 — Extract the concurrency IR

**What happens:**
- Pick a small subsystem (candidate: a driver or a `lib/` data structure with clear locking, like an IDR user or ring buffer).
- Run **static analysis** on the source to propose: which locks appear to protect which fields? Draw edges in the concurrency IR.
- Run the kernel under lockdep and capture: which lock classes were actually held during access to which memory addresses?
- Compare the two. If they match (or match after resolving aliases), the IR extraction works.

**What this rung adds:**
- We now know how to *read the concurrency invariants out of the kernel.* The static analysis proposes; lockdep confirms. The lock→data map lives in the IR.

**The negative control:**
> Here's where the negative control catches a subtler class of bug: inference. Imagine our static analysis incorrectly guesses that `lock1` protects field `f`, when in reality only `lock2` does. We run lockdep and it will say: "I never saw `lock1` held during access to `f`; I only saw `lock2`." The negative control is: "our guess was wrong; *reject* this IR and ask the model to re-infer." Or it could be: your extraction is incomplete (there's a critical-section path you missed). The gate catches that too.

**Learner checkpoint question:**
- Static analysis proposes a lock→data edge. Lockdep says it never observed that lock held during access to that field. What does that mean?
- Sample answer: *The static guess was wrong. Either the lock doesn't actually protect this field, or our static analysis missed some access path where the lock is held. Either way, the IR extraction fails this edge; reject it and mark it as "uncertain."*

#### M2 — Single-region transplant by hand

**What happens:**
- Take one critical section from the M1 IR. Hand-rewrite it into Rust-for-Linux: lock the fields into a `SpinLock<T>`, lock and guard scope, drop the manual lock/unlock calls.
- Run it through the gate: KCSAN, lockdep, KUnit on the modified subsystem.

**What this rung adds:**
- We now know the **pipeline works end-to-end once on a real region.** IR → Rust → gate pass.

**The negative control:**
> M2's negative control is the *mechanical* check: we transplant the region, then we also transplant it *wrong* — deliberately omit the lock on one data field, or use the wrong barrier. KCSAN should catch the missing lock as a race. lockdep should catch the wrong barrier order (if it's an acquire/release reversal). The transplant that's mechanically correct passes; the broken one fails. This proves the gate is actually catching real mistakes, not just happy-pathing through.

**Learner checkpoint question:**
- You hand-rewrite a critical section into a `SpinLock<T>` guard. KCSAN passes, lockdep passes. What does that mean?
- Sample answer: *It means the region in Rust has the same race/deadlock profile as the original C. That's the equivalence we can claim for concurrent code — not "outputs match," but "no new race, no new deadlock, same locking invariants respected."*

#### M3 — Model-synthesized transplant

**What happens:**
- Instead of hand-writing M2, the **model synthesizes the Rust rewrite** given the region + the IR + the R4L abstraction catalog (the table from `docs/design.md` §3.2: C idiom → Rust target).
- Same gate: KCSAN, lockdep, KUnit.

**What this rung adds:**
- We now know **cheap-model synthesis is in reach.** The same class of models that produce the $0.007 CGIR rewrites can also do region synthesis for concurrent code. (Evidence: if it works on M3, we're in cheap-model territory.)

**The negative control:**
> M3's negative control proves the model doesn't hallucinate: we give the model a region + IR, it proposes a Rust rewrite, we run the gate. The gate either passes or fails. If the model produces a wrong rewrite (say, forgets to lock a field), KCSAN catches it. If it produces a deadlock-prone lock order, lockdep catches it. The negative control is: generate a rewrite, *deliberately corrupt it* (swap barrier orderings, drop a lock), and watch the gate reject it.

**Learner checkpoint question:**
- The model synthesizes a Rust rewrite of a region. It passes KCSAN and lockdep. What can we say about it?
- Sample answer: *We can say it is *empirically equivalent* to the C under the oracle's observation — KCSAN didn't find a new race, lockdep didn't find a new deadlock. That doesn't prove the Rust is correct in the abstract; it proves the Rust and C have the same concurrency behavior under stress.*

#### M4 — Subsystem sweep

**What happens:**
- Take M3 and **run it on every region** of one subsystem, dependency-ordered (so regions that depend on each other get rewritten in the right order).
- All regions pass the M0 gate together.

**What this rung adds:**
- We now know **the machine scales.** M3 on one region is a proof of concept; M4 on 10–50 regions is evidence the technique works at the subsystem level.

**The negative control:**
> M4's negative control has a new flavor: *integration*. Two regions might each pass KCSAN in isolation, but when rewritten together, they interact in a way that creates a new race. The negative control is: rewrite a region, deliberately introduce a *cross-region* bug (a field that should be protected by a shared lock but isn't), and watch KCSAN + syzkaller (the fuzzer that creates stress) catch it under load.

**Learner checkpoint question:**
- You've rewritten region A and region B. Both pass KCSAN individually. When you rewrite both and run syzkaller to stress the subsystem, a new KCSAN finding appears. What happened?
- Sample answer: *The two regions interact — say, they both touch a shared field but there's a new code path where one region doesn't hold the lock the other expects. The gate caught an integration bug.*

#### M5 — Upstreamable output

**What happens:**
- Take the M4 sweep and **emit it as Rust-for-Linux-shaped patches** against a real subsystem in the Linux tree.
- Attach the sanitizer evidence: "these changes added 0 new KCSAN findings, 0 new lockdep splats, syzkaller found no new crashes."

**What this rung adds:**
- We have **human-reviewable patches.** A maintainer opens the PR, sees:
  - The Rust code using R4L abstractions (`SpinLock<T>`, `Rcu<T>`, etc. — things they recognize).
  - Evidence: "stock kernel baseline: 2 known-benign KCSAN races. Modified kernel: same 2 races, no new ones."
  - Functional tests: all pass.

**What this rung is NOT:**
- This is not "the kernel is now all Rust."
- This is not "zero human review needed."
- This is not "the machine replaced the maintainers."

**What it is:**
- This is **the honest end state.** A force multiplier for Rust-for-Linux. The machine takes regions that are too scattered/subtle for a human to rewrite safely, produces a candidate Rust translation, and lets the oracle + maintainer decide. Some regions the machine gets right. Some are too subtle and the machine falls back to "human please hand-write this."

**The negative control:**
> M5's negative control is the **upstreamability gate.** Before a patch is emitted, it must:
> 1. Compile on the target kernel version.
> 2. Pass the sanitizer gate (no new races/deadlocks).
> 3. Pass subsystem KUnit + syzkaller.
> 4. Use only R4L abstractions that are already upstream (no inventing new Rust bindings).
> 
> A patch that violates any of those gets rejected with a reason. The machine does not emit "we think this is right, good luck" — it emits "the oracle says this is safe, the code is maintainable, and here's the evidence."

**Learner checkpoint question:**
- You emit a patch from M5. A maintainer looks at it and says: "This uses a Rust-for-Linux abstraction that doesn't exist in the kernel version we're targeting." What happens?
- Sample answer: *The patch is rejected. M5 was supposed to emit only patches using existing R4L abstractions. Either the oracle was supposed to catch this (it didn't, that's a bug), or our region-synthesis chose an abstraction that wasn't available yet. We mark the region as "out of scope for this sweep" and move on.*

---

### How Lockstep sits on CGIR

> Now zoom back to the big picture. Lockstep doesn't stand alone — it stands on the shoulders of CGIR, the parent project you already know.

**What Lockstep gets from CGIR:**
1. **Call graph + effects/purity classification.** CGIR already analyzed the kernel and sorted code into "pure" (CGIR handles it) and "not pure" (it refuses). That purity label is loaded into Lockstep's pipeline. It tells us:
   - Which callees inside a region are already Rust-ready (CGIR rewrote them).
   - Which callees are themselves concurrent (Lockstep needs to handle them too, or wait for them).
   - Where the pure/concurrent boundary is.

2. **The gate machinery.** The kbuild + QEMU container, the way to patch the kernel, link in Rust objects, boot it. All proven in CGIR's rung 4. Lockstep's M0 baseline is literally CGIR's gate with sanitizer configs added.

3. **The `ComponentSpec` contract.** CGIR emits human-readable specs of what each component does. Lockstep reads those specs as part of the context for synthesizing a rewrite.

**What Lockstep has to do itself:**
1. **Concurrency IR extraction.** CGIR's IR is about data flow and purity; Lockstep's IR is about locks, barriers, and RCU. New analysis.
2. **Abstraction synthesis.** The model choosing which R4L abstraction fits this region. Different task from function-level rewrites.
3. **The dynamic oracle stack.** KCSAN, lockdep, syzkaller under the transplant. CGIR's differential would compare outputs; Lockstep watches for races.

**The frontier moves in-out:**
- CGIR eats the pure computational core, function by function.
- Lockstep eats the concurrent regions around it.
- The boundary between "done" and "hard" is always explicit (CGIR's effects labels + Lockstep's region classification).
- They run together, not sequentially.

**Learner checkpoint question:**
- The kernel has a function `foo()` that calls `helper_pure()` (which CGIR rewrote) and also takes a spinlock protecting a critical section. How does Lockstep see this?
- Sample answer: *Lockstep's purity label (inherited from CGIR) says `helper_pure` is done — CGIR handles it. The critical section is in Lockstep's domain. Lockstep doesn't rewrite the call to `helper_pure`; it treats it as a black box that's already Rust. Lockstep focuses on the lock, the critical section, and the fields guarded by it.*

---

### The honest end state

> Here's what "success" actually looks like for Lockstep — and it's important to be clear, because the vision can drift:

**What Lockstep is:**
- A machine that takes concurrent regions of C and produces candidate Rust-for-Linux rewrites, gated by the kernel's own sanitizers and functional tests.
- A scaling tool for Rust-for-Linux: it amplifies human expertise by handling the mechanical parts and letting the oracle filter out the mistakes.

**What Lockstep is NOT:**
- A "make the kernel all Rust" button. (The kernel is millions of LOC, much of it legacy C that will never be rewritten. Lockstep is for *parts* of it.)
- A replacement for Rust-for-Linux human effort. (R4L writes safe abstractions; Lockstep applies them. R4L is the upstream home; Lockstep is a contributor.)
- A proof engine. (KCSAN is sound-ish, not complete. We claim what the oracle claims: "no new race the sanitizers found," not "provably race-free.")
- Autonomous. (When a region is too subtle, the machine bows out. A human R4L contributor then handles it the traditional way.)

**What success means:**
- Maintainers can open an M5 patch, read Rust code using R4L abstractions (things they trust), see the sanitizer evidence (no new races, all tests pass), and decide: "I'll merge this" or "Too subtle, I'll hand-rewrite it." That decision is *informed* by the oracle, not removed.
- The output is in the same shape as R4L's own code — not a separate dialect, not a "Lockstep patch" format, just upstreamable Rust.

**Learner checkpoint question:**
- A maintainer reviews an M5 patch and says: "The oracle says no new races, but I'm not confident in the abstraction choice. I'm going to rewrite this by hand." Is that a failure?
- Sample answer: *No. That is exactly the right behavior. The machine produced a candidate, the oracle vetted it, the human made a judgment call. Some regions the machine gets right; some need human judgment. Lockstep succeeds by making the hard judgment *informed*, not by removing human judgment.*

---

## Misconceptions to catch

### "The end goal is a fully-automatic whole-kernel rewrite"

**Catch it:** When the learner says something like "so by M5 the kernel is all Rust?" or "the machine handles everything?"

**Clarify:** The honest scope:
- Lockstep applies to *bounded, well-concurrented regions* with clear locking.
- Some subsystems are too subtle (non-nested locks, unusual orderings, patterns without R4L equivalents yet).
- The end state is "N subsystems (or regions within subsystems) transplanted to Rust"; not "the whole kernel."
- A human engineer made the decision to rewrite this subsystem. A human maintainer reviews the result.

**Anchor it:** Show `docs/design.md` §6 ("What is proven vs. speculative") and §5 ("The hard problems, stated honestly"). This is the project's intellectual honesty — naming the ceiling instead of hand-waving past it.

### "KCSAN found 0 races, so the code is definitely race-free"

**Catch it:** When the learner interprets M0's baseline as "the kernel is clean" or thinks a passing gate means "provably safe."

**Clarify:** The accuracy of the oracle:
- KCSAN **finding** a race means there's really a race (sound).
- KCSAN **not finding** a race means the instrumenter didn't observe one under this workload (not complete). There might be races that occur once a month under a different load.
- M0's baseline is "stock code clean under KUnit + KCSAN," which is strong evidence of correctness, but not a proof.
- The gate is: "the transplant has the same KCSAN profile as stock," i.e., "no new bug class the oracle observes."

**Anchor it:** `SYLLABUS.md` Module 4, the "honesty point" section. This is where the project differs from "formal verification" (which claims completeness) and aligns with "dynamic testing" (which claims soundness but not completeness).

### "Lockstep replaces Rust-for-Linux"

**Catch it:** When the learner treats Lockstep as a competitor to R4L or an alternative.

**Clarify:** The relationship:
- R4L is humans designing safe abstractions and writing new drivers in Rust. Rust-for-Linux is upstream.
- Lockstep is a machine that applies those abstractions to *existing C*, scaled up.
- If Lockstep works, it's a force multiplier for R4L (more code gets the Rust benefits faster).
- If a region is too subtle for the machine, the machine steps aside and the human R4L process takes over.
- There is no version of this that "replaces" the human effort — only one that scales the human effort by handling the routine mechanical parts.

**Anchor it:** `design.md` §7 ("Relationship to Rust-for-Linux"). Quote: "If it works, it is a force multiplier for R4L's mission; if a region is too subtle for the machine, it falls back to exactly the human process R4L already runs."

### "The gate must show zero KCSAN findings for a transplant to pass"

**Catch it:** When the learner thinks the criterion is "0 findings" rather than "no *new* findings."

**Clarify:** The gate is differential:
- Stock kernel baseline: 2 known-benign KCSAN races (printk ringbuffer).
- Transplanted region in Rust: 2 same races, 0 new ones.
- Result: **pass**, because we added no *new* race. If the Rust version had 3 races (the 2 stock + 1 new), it would **fail**.
- Why differential? Because the baseline may have known-benign races (things meant to race). We're not trying to erase those; we're trying to not *add* to them.

**Anchor it:** `m0/RESULTS.md` and the checkpoint question from M0 above. Show the actual baseline (2 findings) and explain why "2" is the right criterion for "pass," not "0."

---

## Exercises (reps)

Each exercise has a learner prompt, a sample solution, and what it reinforces. Use these to test understanding and build muscle memory.

### Exercise A: Place the concept (mapping → rungs)

**Learner prompt:**

You now have six terms from the syllabus. Each belongs to a specific rung or concept. Read them, and for each one, say which rung (M0–M5) or earlier module it belongs to, and *why*. No right answer is "somewhere in the ladder" — you need to pinpoint it.

Terms:
1. `data_push_tail` race in the printk ringbuffer
2. `SpinLock<T>` Rust abstraction
3. Release/acquire memory barriers
4. Lock-acquisition order validation
5. `smp_store_release` C idiom
6. R4L patch emitted with sanitizer evidence

**Sample solution:**

1. **`data_push_tail` race** → **M0 / Module 2**. This is a data race (Module 2: the thing that matters). M0 captures it in the baseline as a known-benign, intentional race in a lockless structure. It is the *existence* of this race that proves KCSAN is instrumenting correctly.

2. **`SpinLock<T>`** → **Module 5 / M2–M3**. This is the Rust-for-Linux abstraction that encodes a critical section's rule in the type system. M2 uses it when hand-transplanting a region; M3 uses it when the model synthesizes a transplant.

3. **Release/acquire barriers** → **Module 3 / M2–M3**. Memory ordering rules. When a region uses `smp_store_release`/`smp_load_acquire`, the Rust rewrite must use the equivalent Rust `Ordering` (Release/Acquire). Getting this wrong creates a subtle race that KCSAN catches.

4. **Lock-acquisition order** → **Module 4 / M1–M2**. lockdep watches lock-acquisition order. If CPU A holds lock1 waiting for lock2 while CPU B holds lock2 waiting for lock1, deadlock is possible. M1 extracts the IR; M2 confirms the transplant preserves the order.

5. **`smp_store_release` C idiom** → **Module 3 / M2–M3**. This is the C way of enforcing memory ordering. Module 3 explains what it does; M2–M3 are where we transplant it into Rust `Ordering::Release`.

6. **R4L patch with evidence** → **M5**. This is the output: a Rust patch using R4L abstractions, with the sanitizer delta attached ("stock: 2 races, transplant: 2 races, delta: 0").

**What it reinforces:**
- Concepts belong to specific parts of the ladder. Data races and barrier semantics are timeless (Modules 2–3), but *how they're expressed* changes (C idiom in Module 3, Rust in Module 5). The ladder is not "learn modules 1–8 then build," it's "modules 1–6 are the vocabulary, rungs M0–M5 are how we use it."
- Each rung stands on the previous ones. M2 transplants by hand, so you need to know: what is a data race (M2), what is a barrier (M3), what is a region (M5 from design.md), what is `SpinLock<T>` (M5). The ladder moves forward.

---

### Exercise B: Design a mini-gate (checks in order)

**Learner prompt:**

You've hand-transplanted a critical section from the kernel into a `SpinLock<T>` guard. Now imagine you're the gate. A hypothetical maintainer is asking: "I want to merge this. Will you check it?" 

Write down, in order, every check your gate must run and what "pass" looks like for each. (Hint: think about the M0–M2 rungs. What did each prove? What can go wrong?)

**Sample solution:**

1. **Compile check.** Does the Rust code compile against the target kernel version's Rust bindings?
   - Pass: No errors or warnings. Fails: Compilation error or missing binding.

2. **Correctness check.** Does the transplant actually do what the C did? (This is the negative control.)
   - Pass: Run functional tests (KUnit); all pass. 
   - Fails: KUnit test that exercised the critical section now fails.

3. **Baseline consistency.** What is the sanitizer baseline on stock code for this subsystem?
   - This is the M0 step: we've already booted stock, captured KCSAN + lockdep findings. They're the reference.

4. **KCSAN race check.** Boot the kernel with the Rust transplant, run KCSAN. Do we have new races?
   - Pass: Same KCSAN findings as stock. (Or fewer, but we don't require that.)
   - Fails: New KCSAN finding not in the stock baseline = the transplant is broken.

5. **lockdep deadlock check.** Did the lock-acquisition order change? Are there new deadlock potential?
   - Pass: lockdep splats = stock baseline (probably 0).
   - Fails: New lockdep splat = the Rust locking is wrong.

6. **The negative control check.** Deliberately break the transplant (drop a lock, mess with a barrier). Does the gate catch it?
   - Pass: The broken version is rejected (KCSAN finds a race, lockdep finds a deadlock, or KUnit fails).
   - Fails: The broken version passes (the gate is a lie).

7. **Integration check.** If this critical section shares fields with other regions, do all regions together pass?
   - Pass: Full subsystem + syzkaller (fuzzer stress) finds no new races/crashes.
   - Fails: Two regions that each pass in isolation create a new race when combined.

**What it reinforces:**
- The gate is a *sequence* of checks, not a single pass/fail.
- The negative control is not optional — it's the proof that the gate is real.
- The gate grows as we climb the ladder: M0 is "does stock pass?", M2 is "does transplant pass + does broken version fail?", M4 is "do multiple regions together pass under fuzzer stress?"
- Checking functional correctness (KUnit) is not the same as checking concurrency correctness (KCSAN + lockdep). Both are needed.

---

### Exercise C: Where does this code go? (CGIR vs. Lockstep vs. out of scope)

**Learner prompt:**

Lockstep sits on CGIR. Different code goes different places:
- **CGIR** handles pure, self-contained functions (no shared state, no locks, outputs depend only on inputs).
- **Lockstep** handles regions of concurrent code (locks, barriers, critical sections, RCU, shared state).
- **Out of scope (for now)** — code that needs R4L abstractions that don't exist yet, or patterns too subtle for either tool.

For each code snippet below, route it and explain *why* it belongs there.

**Snippets:**

1. A function `hash_bytes(const char *data, int len)` that computes and returns a SHA256 hash. It touches no global state and takes no locks. Its entire meaning is "hash → output." Where does it go?

2. A critical section protected by `struct queuelock *q->lock`. The section touches fields in a queue structure `q->items`, `q->count`, `q->head`. The section is 20 lines long and not nested inside another critical section. Where does it go?

3. A structure that tracks reference counts with a complex ordering requirement: "increment must happen under lock A, but decrement can happen anywhere as long as the decrement is ordered after the final use by a release barrier." The pattern has no R4L equivalent yet (it's too specialized). Where does it go?

4. A RCU read-side critical section: `rcu_read_lock()`, walk a linked list with `list_for_each_entry_rcu()`, `rcu_read_unlock()`. The list is updated elsewhere with `call_rcu()`. Where does it go?

**Sample solution:**

1. **CGIR.** Pure function, no shared state, deterministic output. CGIR's differential test will rewrite it to Rust, run it on the same inputs, compare outputs, and verify it matches. This is what CGIR does best.

2. **Lockstep.** A critical section (the defining unit for Lockstep), well-nested, protecting specific fields. Lockstep extracts the lock→data map (M1), confirms it matches lockdep (M1 gate), hand-transplants it to `SpinLock<T>` with the fields inside (M2), or lets the model synthesize the transplant (M3). Then the gate checks KCSAN + lockdep.

3. **Out of scope.** R4L doesn't have a safe abstraction for this pattern yet. The machine *could* propose something, but the gate would fail (Lockstep requires using only upstream R4L abstractions). So: mark the region as "too specialized, out of scope for this sweep." A human R4L contributor might add the abstraction later, then this becomes in-scope.

4. **Lockstep.** A RCU read-side critical section. Lockstep's region types (from `design.md` §2) explicitly include "RCU read/update epochs." The model would synthesize a transplant using R4L's `Rcu<T>` abstraction, respecting the grace-period ordering. The gate checks for new races and new deadlocks. This is core Lockstep work.

**What it reinforces:**
- The three categories are *not* "pure vs. concurrent vs. everything else" — they're "pure (CGIR), concurrent (Lockstep), not yet in Rust (out of scope)."
- CGIR and Lockstep partition the kernel by looking at *effects*: CGIR's purity label says "no locks, no shared state"; Lockstep takes everything CGIR refuses.
- "Out of scope" is not failure — it's honesty. If a region can't be transplanted with today's R4L abstractions, it's better to flag it than to force-fit it.
- The route is determined by the *invariant*, not by the code shape. Both snippet 2 and 4 involve locking/coordination, but one is spinlock (Lockstep) and one is RCU (also Lockstep, but a different region type).

---

## Mastery check

Ask the learner these four questions. They should be able to answer in their own words without looking back (though it's fine to refer to a diagram or the design doc). If they miss one, re-teach that piece.

### Question 1: The ladder and the oracle

**Question:**
Describe the M0–M5 ladder in one sentence per rung. Then: why does every rung include a check that rejects the deliberately-broken version?

**Sample answer:**
- **M0:** Boot the kernel under KCSAN + lockdep; capture a clean baseline (0 new races, 0 deadlocks).
- **M1:** Extract the concurrency IR for one subsystem; prove the lock→data map matches lockdep's runtime observations.
- **M2:** Hand-rewrite one critical section to `SpinLock<T>`; pass M0 gate (same sanitizer profile as stock).
- **M3:** Model synthesizes a region rewrite given the IR; pass M0 gate.
- **M4:** Rewrite all regions in a subsystem, dependency-ordered; pass gate under fuzzer stress.
- **M5:** Emit the rewrite as R4L-shaped patches with sanitizer evidence.

Every rung includes a negative control because **a gate that can't reject is worse than no gate**. KCSAN finding a race in a deliberately broken version proves KCSAN is real, not a decorative pass. lockdep catching a deadlock in a wrong lock order proves lockdep is working, not paper-checking. KUnit failing on a broken transplant proves the functional test is actually exercising the code. Without the negative control, we have no evidence that the gate is catching anything — it might just pass everything.

### Question 2: The hard problem

**Question:**
Why is "which lock protects which field" the central hard problem (Module 6), and how do M1 and the overall ladder try to solve it?

**Sample answer:**
In C, the lock-to-data mapping is a convention: it's written down only in comments or developer memory. If a new contributor forgets to take the lock before touching a field, the race is silent until it happens under load months later.

M1 solves this with a **hybrid approach**: static analysis proposes candidate edges (this lock seems to guard these fields based on what's touched between lock/unlock), and lockdep confirms them dynamically (we actually observed this lock held during access to this field). The M1 gate is "the extracted map matches lockdep's observations."

The ladder then *encodes* the solution: M2 and M3 transplant the region into `SpinLock<T>`, which makes it impossible to touch the data without holding the lock — the rule stops being convention and becomes a compiler check. Any code path that tries to access the field without the guard gets a compilation error. KCSAN then confirms that the Rust version respects the same rule as the C.

So the answer to "which lock protects which field?" goes from "I hope someone documented it" in C, to "lockdep observed it" in M1, to "the compiler enforces it" in Rust.

### Question 3: CGIR and Lockstep

**Question:**
What does Lockstep get from CGIR, and what makes them complementary rather than competitive?

**Sample answer:**
Lockstep gets three things:
1. **Purity labels:** CGIR already analyzed the kernel and marked functions as "pure" or "impure." Lockstep reads those labels and knows which functions it can treat as black boxes (already CGIR-safe) vs. which are concurrent regions itself.
2. **The gate machinery:** The kbuild + QEMU container that was proven in CGIR's rung 4. M0 is literally that harness with KCSAN + lockdep configs added.
3. **The `ComponentSpec` contract:** Human-readable specs of what each function does, which the model reads as context.

They're complementary because they partition the kernel: CGIR eats the pure computational core; Lockstep eats the concurrent residue around it. A function might call `pure_helper()` (CGIR) and also take a spinlock (Lockstep). CGIR doesn't try to rewrite inside the lock; Lockstep doesn't try to rewrite pure helpers. The boundary between "done" and "hard" stays explicit.

### Question 4: The honest end state

**Question:**
What is the honest definition of "success" for Lockstep? What is it *not*? And how does the negative control prove the oracle is trustworthy?

**Sample answer:**
**Success** is: a machine that can take bounded concurrent regions and produce candidate Rust-for-Linux rewrites, gated by the kernel's own sanitizers (KCSAN, lockdep, KUnit, syzkaller). A maintainer can review the patch, see the evidence ("0 new races, 0 new deadlocks, tests pass"), and decide to merge or hand-rewrite. Some regions the machine gets right; some the human rewrites by hand.

**Not success** is: whole-kernel automatic rewrite, zero human review, "provably race-free" claims, or a replacement for R4L's human effort.

The negative control proves the oracle is trustworthy because: we deliberately break a transplant (drop a lock, mess with a barrier), run the gate, and the gate rejects it. If the broken version *passed*, the gate would be a lie. If it fails, we have evidence that KCSAN/lockdep/KUnit are catching real mistakes, not just rubber-stamping. That evidence is what lets a maintainer trust the gate: "the oracle caught the wrong thing in testing; I can trust it to catch problems in production."

---

## Where to go next

You now have the whole map. M0 is complete: the baseline is captured, the oracle is online.

**Next step: M1.** Pick a small, well-locked subsystem (a candidate: a driver or a `lib/` data structure with clear locking). Extract its concurrency IR. Run the kernel under lockdep and prove the extracted lock→data map matches what lockdep observed. That's the proof that the static+dynamic hybrid works — and it's the foundation for everything that follows.

**What you can now do:** You can reason about any piece of Lockstep's work and ask: "Where does this fit in the ladder? What oracle checks does it pass? If this region is too subtle, what's the reason?" You understand the whole project — not just the current milestone, but how it connects to the design, to CGIR, to Rust-for-Linux, and to the kernel's own concurrency model.

You have the tools to make decisions about what's in scope, what's hard, and when to step back and let a human do it. That is the shape of responsible research.

**Go read** `docs/design.md` §5 ("The hard problems, stated honestly") if you want to understand what makes M1 nontrivial and what problems live in M2+M3. And if you're curious about the gory details of the sanitizers, **go back to Module 4** — you now know *why* KCSAN and lockdep matter, so the details will stick.

Congratulations. You've zoomed out and you understand the ladder. Let's climb it.
