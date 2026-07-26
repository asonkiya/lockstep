# Lesson 04 — The oracle: sanitizers that hunt races

## For the teaching LLM

- This module is the **load-bearing** one. Everything about whether anyone can
  *trust* Lockstep collapses to one question: "is the oracle sound, and do you
  read it honestly?" Spend real time here; do not rush to the exercises.
- **Hammer the honesty point until they can say it unprompted:** a sanitizer that
  finds a race has found a *real* race (sound-ish); a sanitizer that stays
  **silent has proven nothing** — only that it saw no race *under this workload*.
  Silence is not a proof of correctness. If the learner ever says "KCSAN passed,
  so it's race-free," stop and re-teach — that sentence is the exact mistake the
  whole project is built to avoid.
- **The gate is a delta, not an absolute.** The accept/reject rule is "adds **no
  NEW** finding versus the baseline," never "zero findings." Lead with *why*: real
  kernels have known-benign races on stock code (the learner's own M0 run has two),
  so demanding zero would reject the unmodified kernel. Make them feel that a
  non-empty baseline is *healthy*, even *reassuring* — it proves the detector was
  actually instrumenting.
- **Ground it in their own M0 result.** They just ran this. KCSAN found
  `data_push_tail` (printk ringbuffer, intentionally lockless) and
  `_find_next_and_bit` (a bitmap helper) — both known-benign. Use those names.
  The baseline being `{data_push_tail, _find_next_and_bit}` and not `{}` is the
  concrete reason the gate diffs instead of demanding zero.
- **Keep the four tools in their lanes.** KCSAN = data races (memory). lockdep =
  lock-order / *possible* deadlock (even if it didn't happen this run). KUnit =
  does it still function. syzkaller = the stress generator that makes the others
  fire. Learners blur KCSAN and lockdep constantly; the "read the report" exercise
  exists to separate them by hand.
- Lead every abstract point with a concrete race first (the code, then the report),
  then the vocabulary. Prefer having them *run* the race detector in Exercise (a)
  before you philosophize about soundness — the felt experience of a red report and
  then a silent one does the teaching.

## Objectives

By the end of this lesson the learner can:

1. Explain what KCSAN, lockdep, KUnit, and syzkaller each do, in one sentence each,
   and say which class of bug each catches.
2. State precisely what a silent sanitizer does and does **not** prove.
3. Explain why Lockstep's gate is "no new findings vs. baseline" and not "zero
   findings," using their own M0 baseline as the example.
4. Read a raw KCSAN report and answer: which two accesses raced, was either a
   write, and which lock (if any) was missing.
5. Given a baseline finding set and a post-transplant set, compute the verdict
   (accept / reject) and justify it.

## Conversation flow

### Hook

"You've spent three modules learning that concurrency bugs are invisible on one
core, undefined when they fire, and impossible to catch by comparing outputs. So
here's the obvious question: if you can't compare outputs, **how do you ever know
a rewrite didn't break anything?** You need something that watches the running
kernel and yells when two CPUs collide. That watcher is the whole ballgame —
it's Lockstep's verifier. Today we meet it. And you already ran it: your M0 boot
last night *found two races on stock, unmodified Linux.* By the end of this you'll
know why that's not a bug in your setup — it's the oracle working correctly."

### Define the four tools (concrete first, then the name)

**KCSAN — Kernel Concurrency SANitizer (the primary oracle).**
Start concrete: "Remember the racing increment from Module 2 — two CPUs do
`counter = counter + 1`, one increment is lost. How would a machine *catch* that
while the kernel runs?" Let them think. Then: KCSAN instruments (almost) every
memory access the kernel makes. When it sees two CPUs touch the same address with
no synchronization between them and at least one is a write, it prints a
`BUG: KCSAN: data-race in <function>` report with **two stacks** — one per racing
access. It is watching for *exactly* the Module 2 / Module 3 bugs.

**lockdep — the lock-order validator.**
Concrete first: "CPU A grabs lock 1, then reaches for lock 2. CPU B grabbed lock 2,
now reaches for lock 1. Both wait forever — a deadlock. But what if, on this
particular run, the timing never lined up and it *didn't* hang? Is the code safe?"
No — it's a landmine that didn't get stepped on. lockdep watches every lock
acquire and builds the graph of "lock X was ever taken while holding lock Y." The
moment that graph contains a cycle, it prints a splat — **even though the deadlock
never actually happened this run.** It catches the *possibility*, not just the event.
That's the key difference from KCSAN, which reports an observed collision.

**KUnit / kselftest — the functional tests.**
"Does the code still do the right thing at all?" These are the kernel's ordinary
unit tests — the direct descendant of CGIR's "run the tests and check they pass"
gate. A rewrite can be perfectly race-free and still compute the wrong answer;
KUnit catches *that* half. KCSAN and lockdep say "no new races/deadlocks"; KUnit
says "still correct."

**syzkaller — the stress generator (not yet wired).**
The catch with all three above: they only fire if the workload actually *exercises*
the racy code. syzkaller is a coverage-guided fuzzer that hammers the kernel with
random, weird sequences of system calls to *manufacture* concurrency stress — so
the detectors have something to detect. It's the thing that makes the oracle's
"under stress" real. Later rung; M0 uses a fixed KUnit + crypto-self-test workload
instead.

#### Socratic check 1

> **Ask:** KCSAN and lockdep both watch the running kernel. What's the difference
> in *what they're looking for*?

*Sample answer:* KCSAN looks for a data race — two CPUs touching the same memory,
one writing, no synchronization — and reports it when it *observes* one happen.
lockdep looks at lock *ordering*: it flags a potential deadlock as soon as the
lock-acquire graph could cycle, even if no deadlock actually occurred on this run.
KCSAN reports an observed event; lockdep reports a proven possibility.

#### Socratic check 2

> **Ask:** Why isn't KUnit enough on its own to verify a concurrent rewrite?

*Sample answer:* KUnit checks functional correctness — right output — which is the
CGIR-style "compare results" gate. But a concurrency bug isn't a wrong output;
it's two CPUs racing that produces *undefined* behavior, often only under load.
The tests can pass every time while a race lurks. You need the race/deadlock
detectors *on top of* the functional tests.

### The two key ideas

**Key idea 1 — sound-ish, not complete.**
Say this slowly and get it back in their words: these detectors are **sound-ish
but not complete.**

- **Sound-ish (a finding is real):** if KCSAN reports a data race, there genuinely
  is one. It doesn't cry wolf about the synchronization. (The "-ish" is because a
  reported race can still be *benign* — real, but intended — like your printk one.)
- **Not complete (silence proves nothing):** KCSAN finding *nothing* does **not**
  prove there are no races. It proves only that it didn't observe one *under this
  workload*. A race in a code path the workload never triggered is simply invisible.

So Lockstep's claim is never "provably race-free." It is: *"adds no new
race/deadlock that the detectors can find under stress, where the original was
clean."* Weaker than a proof — and the project states it that way on purpose. If
the learner walks away with one sentence, it's this: **a silent sanitizer is not a
correctness proof.**

**Key idea 2 — the gate is the delta, not zero.**
Now connect to what they just saw. Their M0 KCSAN run on **stock, unmodified
Linux** found:

- `data_push_tail` (×2) — inside the printk lockless ringbuffer, hit by its own
  stress test. This is a **by-design lockless** structure; the race is
  *intentional and known-benign*.
- `_find_next_and_bit` (×1) — a bitmap helper; another known-benign plain-access
  race.
- lockdep splats: **0.**

So the honest baseline reading on a *correct* kernel is **not zero** — it's
`{data_push_tail, _find_next_and_bit}`. If the gate demanded "zero KCSAN findings,"
it would reject the unmodified kernel as broken. Absurd. Therefore the gate is:

> A transplant is **accepted only if it adds no NEW KCSAN or lockdep finding
> versus the baseline** — the reading captured on unmodified code.

That's why M0's entire job was capturing a *trustworthy baseline first*. You can't
diff against a baseline you don't have. And note the bonus: findings in the baseline
actually *reassure* you — a totally silent KCSAN might mean it wasn't instrumenting
at all. Your two benign findings are proof the detector is alive.

#### Socratic check 3

> **Ask:** Your M0 baseline is `{data_push_tail, _find_next_and_bit}`, both benign.
> Suppose after a transplant KCSAN reports `{data_push_tail, _find_next_and_bit}` —
> the exact same two. Pass or fail?

*Sample answer:* Pass. The gate is the delta. No *new* finding appeared beyond the
baseline, so the transplant added no observable race. The benign baseline races
being present again is expected and irrelevant — they were there before.

## Misconceptions to catch

- **"No KCSAN report means the code is correct / race-free."** No. Silence means
  KCSAN didn't *observe* a race under this workload. It's not a proof of absence —
  a race on an unexercised path is invisible. This is the single most important
  correction in the whole course; if they say it, stop and re-teach Key idea 1.
- **"Any KCSAN finding means the code is broken."** No. A finding is a *real* race,
  but real ≠ harmful. The printk `data_push_tail` race is intentional (a
  by-design lockless structure). That's the entire reason the gate diffs against a
  baseline of known-benign findings instead of demanding zero.
- **"KCSAN and lockdep are basically the same detector."** No. KCSAN = data races
  on memory, reported when observed. lockdep = lock-order cycles / possible
  deadlock, reported on possibility even if no hang occurred.
- **"If lockdep is silent, there's no deadlock risk."** Weaker than it sounds — same
  completeness caveat as KCSAN. lockdep only knows about lock orderings the run
  actually exercised. It's very good, but silence still isn't proof.

## Exercises (reps)

### Exercise (a) — Feel what a race detector does

**Goal:** experience the red report → fix → silent cycle firsthand, so "the oracle"
stops being abstract. This is the single most valuable rep in the lesson.

**Language:** Go (its built-in `-race` detector is the friendliest analog to KCSAN;
same underlying idea as ThreadSanitizer). C/ThreadSanitizer fallback below.

**Complete starter code** — save as `race.go`:

```go
package main

import (
	"fmt"
	"sync"
)

func main() {
	counter := 0
	var wg sync.WaitGroup
	for i := 0; i < 1000; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			counter = counter + 1 // <-- two goroutines, same memory, a write, no sync
		}()
	}
	wg.Wait()
	fmt.Println("counter =", counter)
}
```

**Task:**

1. Run it *without* the detector: `go run race.go`. Run it a few times. Notice the
   printed `counter` is often **not 1000** — this is the lost-increment race from
   Module 2, live.
2. Run it *with* the detector: `go run -race race.go`. Read the
   `WARNING: DATA RACE` report — it names two stacks (a read and a write to the same
   address by two goroutines), exactly like a KCSAN report names two CPUs.
3. **Fix it** so the increment is synchronized. Simplest fix — wrap it in a mutex:

   ```go
   var mu sync.Mutex
   // ...inside the goroutine:
   mu.Lock()
   counter = counter + 1
   mu.Unlock()
   ```

4. Run `go run -race race.go` again. Confirm it's **silent** and prints `1000`.

**Fallback if Go isn't installed — C with ThreadSanitizer (KCSAN's cousin):**
Save this as `race.c`:

```c
#include <pthread.h>
#include <stdio.h>

int counter = 0;

void *inc(void *arg) {
    for (int i = 0; i < 100000; i++)
        counter = counter + 1;   /* unsynchronized: data race */
    return NULL;
}

int main(void) {
    pthread_t a, b;
    pthread_create(&a, NULL, inc, NULL);
    pthread_create(&b, NULL, inc, NULL);
    pthread_join(a, NULL);
    pthread_join(b, NULL);
    printf("counter = %d\n", counter);
    return 0;
}
```

Run it (no local toolchain needed — uses the `gcc:12` container):

```bash
docker run --rm -v "$PWD":/w -w /w gcc:12 bash -c \
  'gcc -fsanitize=thread -O1 -g -pthread race.c -o race && ./race'
```

You'll get a `WARNING: ThreadSanitizer: data race` report with two stacks. Then fix
it: add `pthread_mutex_t m = PTHREAD_MUTEX_INITIALIZER;`, wrap the increment in
`pthread_mutex_lock(&m); counter = counter + 1; pthread_mutex_unlock(&m);`,
re-run the same docker command, and confirm the warning is gone.

**Solution:** the mutex-wrapped version reports nothing and prints the full count
(`1000` for Go, `200000` for C). The unsynchronized version both mis-counts *and*
trips the detector.

**What it reinforces:** the felt shape of a race report (two stacks, one address);
that a lock/mutex is what makes the detector go quiet; and — crucially — that going
from red to silent is what "passing the oracle" *feels* like. It also sets up the
honesty point: silence here is trustworthy only because this tiny workload exercises
the one racy line every time. A real kernel path might not be so lucky.

### Exercise (b) — Read a KCSAN report

**Goal:** parse a raw kernel race report by hand and separate KCSAN's job from
lockdep's.

**Language:** none — reading comprehension on a realistic KCSAN report.

**Starter — read this report:**

```
BUG: KCSAN: data-race in list_add_tail / queue_work_len

write to 0xffff8881036a4c18 of 8 bytes by task 271 on cpu 2:
 list_add_tail+0x3c/0x70
 queue_enqueue+0x88/0x140
 producer_thread+0x9c/0x1e0
 kthread+0x1a4/0x1e0

read to 0xffff8881036a4c18 of 8 bytes by task 273 on cpu 0:
 queue_work_len+0x24/0x60
 stats_poll+0x40/0xd0
 monitor_thread+0x7c/0x160
 kthread+0x1a4/0x1e0

CPU: 2 PID: 271   CPU: 0 PID: 273
```

**Task — answer three questions:**

1. Which two accesses raced (name the two functions and the CPUs)?
2. Is either access a write? Which one(s)?
3. Which lock protected this address? (Trick-ish — look at what the report shows and
   doesn't.)

**Solution:**

1. A `write` to `0xffff8881036a4c18` by `list_add_tail` on **CPU 2** (task 271,
   the producer enqueuing) raced against a `read` of the *same* address by
   `queue_work_len` on **CPU 0** (task 273, the stats poller).
2. Yes — the CPU 2 access is a **write** (`list_add_tail` modifies the list). The
   CPU 0 side is a read. That satisfies KCSAN's "at least one writer" condition; a
   read/read pair would not be reported.
3. **None.** The report shows no lock was held on either side around this address —
   that's *why* it's a data race. (Contrast: if a lock *were* held on both sides,
   there'd be no race; if a lock ordering were the problem, you'd get a **lockdep**
   splat, not a KCSAN one. This is a KCSAN report — a missing-synchronization
   problem, not a lock-order problem.) The likely fix: hold the queue's lock (or use
   an atomic) around both the enqueue and the length read.

**What it reinforces:** the two-stack structure of a race report; the
"at-least-one-write" rule; and the KCSAN-vs-lockdep boundary — a *missing*
synchronization shows up in KCSAN, a *wrong lock ordering* shows up in lockdep.

### Exercise (c) — Baseline vs. delta reasoning

**Goal:** apply the gate rule mechanically, including the tempting edge cases.

**Language:** none — reasoning about finding sets.

**Starter / setup:** the baseline (stock code) KCSAN finding set is `{A, B}` — two
known-benign races (think: your real `{data_push_tail, _find_next_and_bit}`). After
a transplant you run the same workload and get a new finding set. For each case
below, give the verdict and the reason.

**Task:**

1. Post-transplant set = `{A, B, C}`. Verdict? Why?
2. Post-transplant set = `{A}` (finding B disappeared, nothing new). Is this a pass?

**Solution:**

1. **REJECT.** The gate is "no *new* finding vs. baseline." `C` is new — a race the
   transplant introduced that wasn't there before. Even though A and B are benign
   and still present, the delta is non-empty, so the transplant fails the gate. `C`
   must be investigated: it's a real race (KCSAN is sound-ish) that the rewrite
   added.
2. **Pass — with a flag, not a shrug.** No *new* finding appeared (the delta of
   "new findings" is empty), so by the strict gate rule it passes. **But** B
   vanishing is suspicious and worth a note: it usually means the workload stopped
   exercising B's code path — i.e., your transplant may have changed *coverage*, not
   just correctness. It is *not* evidence you "fixed" B (KCSAN silence never proves
   absence — Key idea 1). So: passes the gate, but a disappearing baseline finding
   is a signal to check that the test is still hitting the same code, not a
   celebration.

**What it reinforces:** the gate is a one-directional diff — you reject on *added*
findings; a *removed* finding is a coverage smell, not a proof of a fix. And it
re-lands the honesty point: silence (B gone) is never a correctness claim.

## Mastery check

1. KCSAN runs on your transplant and prints nothing. Your teammate says "great,
   it's race-free, ship it." What's wrong with that sentence, precisely? *(Expected:
   silence proves only that no race was *observed under this workload* — not that
   none exists. Sound-ish, not complete. Never a proof of absence.)*
2. Explain, in one sentence each, what KCSAN catches vs. what lockdep catches — and
   why lockdep can flag a deadlock that never actually happened. *(Expected: KCSAN =
   an observed data race on memory, ≥1 writer, no sync; lockdep = a *possible*
   lock-ordering cycle, flagged from the acquire graph even if no hang occurred this
   run.)*
3. Your M0 baseline is `{data_push_tail, _find_next_and_bit}`. Why is the gate "no
   new findings vs. this set" and not "zero findings"? *(Expected: those are real
   but benign races on *stock* correct code; demanding zero would reject the
   unmodified kernel. The gate diffs the delta. Bonus: a non-empty baseline also
   proves the detector was actually instrumenting.)*

## Connects to Lockstep

This module isn't background for Lockstep — it **is** Lockstep's verifier, and you
already stood it up in M0.

- `m0/baseline.sh` is the script that boots a real Linux under exactly these
  detectors. Open it and find the config flags the syllabus points at: `-e KCSAN`
  (the data-race oracle), `-e PROVE_LOCKING` (that's lockdep), and `-e KUNIT` (the
  functional half). Those three flags are the four-tools story made concrete
  (syzkaller is the not-yet-wired fourth).
- Your M0 run produced the **baseline finding set** every future transplant is
  diffed against: `{data_push_tail, _find_next_and_bit}` from KCSAN, `{}` from
  lockdep. That's captured as `m0/baseline-findings.txt`. The gate primitive —
  "does this transplant add anything beyond that set?" — now has its reference
  reading. That is *why* M0 had to run *before* any transplant: you cannot diff
  against a baseline you haven't captured.
- The harness itself is inherited from CGIR: `m0/baseline.sh` reuses CGIR's
  `cgir-kernel-gate` Docker image and kernel-tree volume. **M0 is CGIR's rung-4
  gate plus these sanitizer configs.** So the oracle you learned today is the
  concurrency-aware upgrade of the same containerized kbuild + QEMU machine you
  already know from the parent project.
- Forward pointer: every later rung (M2 onward) reuses this exact gate, and every
  rung includes a *negative control* — a deliberately-broken transplant that the
  gate must **reject** (it must add a new KCSAN/lockdep finding). That habit is
  inherited from CGIR, and it's the operational proof that the oracle actually
  discriminates good from bad — not just that it stays quiet.
