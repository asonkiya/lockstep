# Lesson 02 — Data races: the bug that matters

> Domain lesson for Lockstep. Audience: a strong software engineer with **no**
> kernel-concurrency background. This is the load-bearing module — every tool and
> technique in Lockstep is built around catching *this one bug*. It is
> **coding-heavy on purpose**: the learner should feel a race lose their data with
> their own two threads before we ever say the words "memory model."

---

## For the teaching LLM

- **Run it as a live conversation, not a lecture.** Ask a question, wait for the
  learner's answer, react to *their* words. Never dump the whole flow at once.
  Aim for ~10–15 minutes of talk, then push them into the exercises — the reps are
  where it sticks.
- **Make them PREDICT before they run.** This is the single most important move in
  this lesson. Before *any* code runs, ask: *"Two threads each add 1 a million
  times. What's the final number?"* Let them commit to an answer out loud. The
  gap between their prediction ("2,000,000, obviously") and reality is the entire
  lesson. Do not spoil it; let the terminal spoil it.
- **Walk the lost-increment by hand, on paper, slowly.** Two CPUs, one variable.
  Draw the read/read/write/write interleaving as a literal timeline before you
  let them run anything. If they can't reproduce that four-line drawing
  themselves, they are not ready to move on.
- **Be Socratic, and steer with their wrong answers — don't correct-and-move-on.**
  When they guess wrong, ask a follow-up that makes *them* find the hole ("okay,
  if both threads read at the same instant, what value did each one read?").
  Sample right/wrong answers and how to respond are scripted below.
- **Land the stakes, then relieve them.** The abstract counter is a warm-up; the
  point is that the counter is often a *reference count*, and a lost increment
  there is a use-after-free — a real, shippable security bug. Then immediately give
  them the fix vocabulary (lock, atomic) so they leave empowered, not scared.
- **Ground it honestly.** Under Python's GIL, a naive `counter += 1` can actually
  stay atomic, so the exercises deliberately split the read-modify-write across
  function calls to force a *genuine* lost update. Tell the learner this is a real
  race, not a simulation, and that the same shape in C on real hardware is
  Lockstep's whole reason to exist.

---

## Objectives

By the end, the learner can:

1. Define a **data race** precisely: two threads/CPUs access the same location,
   at least one writes, and nothing orders them — and say why the result is
   *undefined*, not merely "one of the two values."
2. Draw the lost-increment interleaving by hand and explain why two `+1`s produce
   `1`, not `2`.
3. **Reproduce a real lost-update race in code**, then fix it with a lock, and
   observe the count go from wrong-and-varying to correct-and-stable.
4. Explain why a lost **reference-count** increment/decrement leads to
   **use-after-free**, and why that is the most common serious kernel bug class.
5. State the difference between a **lock** (serializes access to a region) and an
   **atomic** (makes one operation hardware-indivisible).
6. Connect all of this to **KCSAN**, the detector Lockstep uses as its oracle,
   which M0 just brought online.

---

## Conversation flow

*Scripted but flexible. Follow the learner; the beats matter more than the exact
words.*

### 1. Hook — the bet

> "Quick bet before we touch any theory. I've got a variable `counter = 0`. I
> start two threads. Each thread does `counter += 1` one million times, then
> stops. When both finish — what's the final value of `counter`?"

Let them answer. Almost everyone says **2,000,000**. Good. Write their number
down where they can see it. Say:

> "Hold that number. We're going to run this for real in a few minutes, and I
> want you to feel the moment it isn't that."

Don't explain yet. Curiosity first.

### 2. The by-hand walk — two CPUs, one variable

Now go to paper (or a text block). Say:

> "`counter += 1` looks like one step. It isn't. The CPU can't add to memory in a
> single indivisible motion here — it does three things: **READ** the current
> value into a register, **ADD** one, **WRITE** it back. Three steps. Now put two
> CPUs side by side and let them interleave in the worst way:"

```
   time │  CPU A                     CPU B
   ─────┼──────────────────────────────────────────────
    t0  │  READ counter  → 0
    t1  │                            READ counter  → 0
    t2  │  ADD 1         → 1
    t3  │                            ADD 1         → 1
    t4  │  WRITE 1                   
    t5  │                            WRITE 1
   ─────┴──────────────────────────────────────────────
        counter is now 1.  Two increments happened. One vanished.
```

> "Both CPUs read **0** — because neither had written yet. Both computed **1**.
> Both wrote **1**. Two `+1` operations, and the counter went from 0 to 1. One
> increment was **lost** — not delayed, not queued: gone, as if it never
> happened. That's a *lost update*, and it's the simplest data race there is."

**Socratic check A** — *"In that timeline, why did CPU B read 0 and not 1?"*

- ✅ Right answer: *"Because A hadn't written yet — B read before t4."*
  → "Exactly. The read happened during the window when A's new value existed only
  in A's register, not in memory. B couldn't see something that wasn't there yet."
- ❌ Wrong answer: *"B read 0 because the counter started at 0."*
  → "Careful — by the time B reads, A has already *computed* 1. So why does B
  still see 0? Look at t2 vs t1: what has A actually written to *memory* by the
  time B reads?" (Lead them to: A's `1` is still in a register, not memory.)
- ❌ Wrong answer: *"Won't the second write just win, so you get the right total
  eventually?"*
  → "The second write does win — it writes `1`. But `1` is *already wrong*. B
  computed its `1` from a stale `0`. There's no step anywhere that adds B's
  increment on top of A's. Which increment survived, and which one was thrown
  away?"

**Socratic check B** — *"Is there an interleaving that gives the right answer,
2?"*

- ✅ *"Yes — if A does all three steps before B starts, you get 2."*
  → "Right. So the answer isn't *always* wrong — it's *sometimes* wrong,
  depending on timing you don't control. That's what makes races so nasty: the
  test passes, the demo works, and it corrupts once a week in production. The
  scheduler picks the interleaving, and it's allowed to pick the bad one."

### 3. Run it — collect on the bet

Send them to **Exercise (a)**. Have them predict again *before* running (they'll
still often say 2,000,000). Then run. When the number comes back wrong and
*different every run*, stop and sit in it:

> "There's your 2,000,000 — except it isn't. And run it again: different wrong
> number. Nothing about your code changed. The only variable is *how the two
> threads happened to interleave that time*. You just watched data disappear."

Then have them add the `threading.Lock`. Run again: exactly 2,000,000, every
time.

> "The lock did one thing: it forced the three steps — READ, ADD, WRITE — to
> happen with no other thread allowed in the middle. It turned three steps back
> into one *un-interruptible* unit. That unit is called a **critical section**,
> and the lock is what protects it."

### 4. The precise definition

Now, and only now, give the definition — they've earned it:

> "A **data race** is: two threads (on a real machine, two CPUs) access the same
> memory location, **at least one of them is writing**, and there's **nothing
> forcing an order** between them. Three ingredients: same location, a writer, no
> coordination.
>
> And here's the part that makes it worse than 'you get one of the two values':
> in C, a data race is **undefined behavior**. The compiler and CPU are *allowed
> to assume races never happen*, so they reorder and cache aggressively. When a
> race does happen, the result isn't 'one value or the other' — it can be
> genuinely broken: torn values, impossible states, the optimizer having deleted
> a check it 'proved' was redundant. Undefined means undefined."

**Socratic check C** — *"Two threads both only *read* the same variable, never
write. Race?"*

- ✅ *"No — no writer, so no race."* → "Correct. Reads alone are fine. It takes a
  writer to create the hazard."
- ❌ *"Yes, they're touching the same thing."* → "Touching the same thing is
  necessary but not sufficient. What's the second ingredient? If nobody's
  changing the value, can either reader see something inconsistent?"

### 5. The real danger — reference counts and use-after-free

Now raise the stakes. This is the payload of the lesson.

> "That counter felt academic. Let me make it deadly. Suppose `counter` isn't a
> score — it's a **reference count**: 'how many parts of the program are still
> using this object.' The rule is: every time someone starts using the object,
> `refcount += 1`; every time someone's done, `refcount -= 1`; and **when it hits
> zero, free the memory** — nobody's using it, so give it back.
>
> Now lose one increment, exactly like we just did. Someone grabbed a reference,
> but the `+1` vanished. The count is one too low. It hits zero while that someone
> is *still using the object*. The memory gets freed. Then that someone reads
> through their pointer into freed memory — which may already have been handed out
> and overwritten. That's a **use-after-free**."

Analogy — use one, vividly:

> "It's a coat check. The tag count says how many coats are on the rack. Two
> people hand in coats at the same instant; the attendant reads '5 coats,'
> increments to 6 in his head — twice — but writes 6 once. Now the board says 6,
> there are 7 coats. Later the board hits 0 while a coat is still hanging, so the
> attendant burns the rack. Someone's coat — with their keys in it — is gone. In
> a kernel, 'their keys' is your data, and 'someone else grabs the freed slot' is
> an attacker's data landing where your object used to be."

> "This is not a hypothetical. Lost/mismatched reference counts leading to
> use-after-free are **the most common serious bug class in the Linux kernel.**
> It's the bug Lockstep is ultimately built to prevent from being *introduced*
> during a rewrite."

Send them to **Exercise (d)** if they want to *see* the use-after-free happen.

### 6. The fix vocabulary

Close the loop — leave them with tools, not fear:

> "Two ways to make this safe, and you'll meet both constantly:
>
> - **Lock (a.k.a. mutex, spinlock):** a token. Only the thread holding it may
>   touch the protected data. It *serializes* access — forces one-at-a-time — so
>   the READ/ADD/WRITE can't be split by another thread. That's what you used in
>   the exercise. Broad: protects a whole region (a *critical section*).
> - **Atomic:** an operation the *hardware* guarantees happens all-at-once. An
>   atomic increment is a single indivisible READ-ADD-WRITE — it physically cannot
>   be split, so it cannot be lost. Narrow: protects one operation, no token
>   needed, and it's faster.
>
> Rule of thumb: an atomic when you need one indivisible operation (like a
> refcount bump); a lock when you need several operations to happen together as a
> unit (like 'subtract from account A *and* add to account B' — Exercise b)."

**Final Socratic check** — *"You need to do three things to shared state as one
unit. Lock or atomic?"* → Lock. *"You just need to bump a single counter as fast
as possible?"* → Atomic.

### 7. Hand-off

> "Everything past here — memory ordering, the sanitizers, Rust's `SpinLock<T>` —
> is machinery for *this* bug: detecting it, preventing it, or making it
> impossible by construction. You now know the enemy. Next module, we meet the
> tool that hunts it: **KCSAN**."

---

## Misconceptions to catch

- **"The GIL means Python can't have races."** False, and the exercises prove it.
  The GIL makes *some individual bytecodes* atomic, but a read-modify-write that
  spans multiple bytecodes (or, as in our exercises, multiple function calls) can
  still be interrupted mid-way. Lost updates happen for real. (It also
  fundamentally misunderstands the domain: the *kernel* has no GIL — it's the most
  concurrent program on the machine.)
- **"You just get one of the two values."** No. In C that's *undefined behavior* —
  torn reads, values the source never could have produced, deleted checks. Even
  in our Python exercises you get a *third* thing: a total that's neither input,
  it's just *lost* work.
- **"The lock makes it slower, so it's a perf tax I might skip."** The lock makes
  it *correct*. An incorrect fast answer is worthless. (There are lock-free
  techniques for perf — atomics, RCU — but "remove the lock and hope" isn't one.)
- **"It worked in my test, so it's fine."** Races are timing-dependent. A race can
  pass ten thousand test runs and corrupt in production. "Didn't observe it" ≠
  "isn't there." (This is *exactly* why Lockstep's oracle can't be "run it once
  and compare outputs" — hold that thought for Module 4.)
- **"Two increments must land somewhere; the total can't just drop."** It can and
  does. There is no step that reconciles the two threads' work. One write simply
  overwrites the other, and the overwritten thread's contribution is gone.
- **"Read-only sharing needs a lock too."** No. Multiple readers with zero writers
  don't race. You need a writer to create the hazard. (Add one writer and now
  *everyone* needs coordination — that's the readers-writer problem, later.)

---

## Exercises (reps)

The learner has **Python (threading), rustc, node, docker.** All exercises below
are pure Python 3 and self-contained — copy, run, observe.

> **Why the code looks slightly contrived (read this to the learner):** on
> CPython, a bare `counter += 1` on a global can, on some builds, be handled by
> the interpreter without releasing the GIL mid-operation, and then you'd see *no*
> lost updates — which would teach the wrong lesson. To guarantee a **genuine**
> lost-update race, these exercises (1) set the thread-switch interval very low so
> the interpreter hands off constantly, and (2) split the read-modify-write across
> function calls so a thread switch *can* land in the gap. This is a real race, not
> a mock — the same shape in C on real hardware is undefined behavior and is
> Lockstep's whole reason to exist.

---

### (a) The vanishing increments

**Goal.** Reproduce a real lost-update race, then fix it with a lock.
**Language.** Python.

**Starter / racy version** (`race_counter.py`):

```python
import sys, threading

# Force the interpreter to switch threads constantly so the race is visible.
sys.setswitchinterval(1e-9)

class Counter:
    def __init__(self):
        self.value = 0

counter = Counter()
N = 100_000

# Split read-modify-write across function calls: a thread switch can now
# land *between* the read and the write -> a real lost update.
def read():
    return counter.value
def write(v):
    counter.value = v

def work():
    for _ in range(N):
        write(read() + 1)   # READ, then (later) WRITE — not indivisible

t1 = threading.Thread(target=work)
t2 = threading.Thread(target=work)
t1.start(); t2.start()
t1.join(); t2.join()

expected = 2 * N
print(f"final = {counter.value:,}   expected = {expected:,}   "
      f"lost = {expected - counter.value:,}")
```

**Task.**
1. *Predict the final value before running.* Write it down.
2. Run it: `python3 race_counter.py`. Run it **three times**.
3. Observe: the total is well under 200,000, **and different each run**.
4. Now fix it. Add a lock so the whole `write(read() + 1)` is one unit.

**Expected observation.** Something like `final = 144,163 expected = 200,000
lost = 55,837`, and a *different* wrong number every run. The fixed version prints
exactly `200,000`, every time.

**Full solution** (`fixed_counter.py`):

```python
import sys, threading

sys.setswitchinterval(1e-9)

class Counter:
    def __init__(self):
        self.value = 0

counter = Counter()
lock = threading.Lock()          # the token
N = 100_000

def read():
    return counter.value
def write(v):
    counter.value = v

def work():
    for _ in range(N):
        with lock:               # only one thread inside at a time
            write(read() + 1)    # READ+WRITE now indivisible w.r.t. other threads

t1 = threading.Thread(target=work)
t2 = threading.Thread(target=work)
t1.start(); t2.start()
t1.join(); t2.join()

expected = 2 * N
print(f"final = {counter.value:,}   expected = {expected:,}   "
      f"lost = {expected - counter.value:,}")   # -> lost = 0, always
```

**Reinforces.** The lost-update interleaving is *real*, is *timing-dependent*
(different every run), and a lock — by forcing one-at-a-time — makes READ/…/WRITE
a single critical section that can't be split.

---

### (b) The bank that invents money

**Goal.** See a race *create and destroy* value (not just lose it), and fix it
with one lock covering a *multi-step* update.
**Language.** Python.

**Starter / racy version** (`race_bank.py`):

```python
import sys, threading

sys.setswitchinterval(1e-9)

accounts = {"A": 1_000_000, "B": 1_000_000}
TOTAL = sum(accounts.values())     # money in the system; must never change

def get(k):
    return accounts[k]
def put(k, v):
    accounts[k] = v

def transfer(src, dst, amount):
    put(src, get(src) - amount)    # read-modify-write on src
    put(dst, get(dst) + amount)    # read-modify-write on dst

def hammer(src, dst):
    for _ in range(100_000):
        transfer(src, dst, 1)

# Two threads shuffling $1 back and forth. Net effect should be zero.
t1 = threading.Thread(target=hammer, args=("A", "B"))
t2 = threading.Thread(target=hammer, args=("B", "A"))
t1.start(); t2.start()
t1.join(); t2.join()

now = sum(accounts.values())
print(f"start total = {TOTAL:,}   end total = {now:,}   "
      f"money invented/destroyed = {now - TOTAL:,}")
```

**Task.**
1. Predict: two threads move $1 back and forth 100,000 times each. What's the
   *total* money at the end?
2. Run it. Observe the total is **not** 2,000,000 — money appeared or vanished.
3. Fix it so a whole `transfer` is atomic w.r.t. other transfers.

**Expected observation.** `end total` drifts off 2,000,000 by hundreds or
thousands, differing each run — the invariant "total money is conserved" is
violated. The fixed version keeps the total at exactly 2,000,000.

**Full solution** (`fixed_bank.py`) — wrap the *entire* transfer, both legs, in
one lock:

```python
import sys, threading

sys.setswitchinterval(1e-9)

accounts = {"A": 1_000_000, "B": 1_000_000}
TOTAL = sum(accounts.values())
lock = threading.Lock()

def get(k):
    return accounts[k]
def put(k, v):
    accounts[k] = v

def transfer(src, dst, amount):
    with lock:                     # BOTH legs happen as one unit
        put(src, get(src) - amount)
        put(dst, get(dst) + amount)

def hammer(src, dst):
    for _ in range(100_000):
        transfer(src, dst, 1)

t1 = threading.Thread(target=hammer, args=("A", "B"))
t2 = threading.Thread(target=hammer, args=("B", "A"))
t1.start(); t2.start()
t1.join(); t2.join()

now = sum(accounts.values())
print(f"start total = {TOTAL:,}   end total = {now:,}   "
      f"money invented/destroyed = {now - TOTAL:,}")   # -> 0, always
```

**Reinforces.** When correctness spans *several* operations (debit **and**
credit), the lock must cover *all* of them — this is why the unit of protection is
a *region*, not a single line. (Foreshadows Lockstep's "semantic region.")

---

### (c) Spot the race (reading rep)

**Goal.** Read code and locate the racy line without running it.
**Language.** Python (reading only).

**Snippet.** A tiny web-hit tracker, one shared dict, several worker threads:

```python
import threading

hits = {}                       # url -> count, shared across all workers

def record(url):
    if url in hits:             # (1) read: is it present?
        hits[url] = hits[url] + 1   # (2) read count, add 1, write back
    else:
        hits[url] = 1           # (3) first hit

def worker(urls):
    for u in urls:
        record(u)

# many threads, overlapping URL streams, all calling record() concurrently
threads = [threading.Thread(target=worker, args=(stream,)) for stream in streams]
for t in threads: t.start()
for t in threads: t.join()
```

**Task.** Without running it:
1. Name the racy line(s) and say *why*.
2. Say what goes wrong (be specific: what value is lost or corrupted?).
3. Propose a fix.

**Expected observation / answer.**
- **Line (2)** `hits[url] = hits[url] + 1` is the classic lost-update read-modify-
  write: two threads read the same count, both add 1, both write — one increment
  lost. Counts end up **too low**.
- **Line (1)+(3)** is a *second, subtler* race: two threads can both evaluate
  `url in hits` as `False` for a brand-new URL, both take the `else`, and both
  write `hits[url] = 1` — the first real increment is lost, or the two initial
  writes stomp each other. (This "check-then-act" gap is a race even without an
  arithmetic update.)
- **Fix.** Guard the whole `record` body with a `threading.Lock` so the
  check-and-update is one critical section:

```python
lock = threading.Lock()

def record(url):
    with lock:
        hits[url] = hits.get(url, 0) + 1   # check + update, one unit
```

(Bonus-correct answer: a `collections.defaultdict(int)` still doesn't save you —
`d[url] += 1` is *still* read-modify-write and still races. The lock, or an atomic
counter type, is the real fix.)

**Reinforces.** Trains the eye to spot the *shape* — shared mutable state +
read-modify-write or check-then-act + no lock — which is exactly the shape
Lockstep's static analysis has to find in C at scale.

---

### (d) *(stretch)* Why a lost increment frees too early

**Goal.** See a **use-after-free** happen from a single lost reference-count
update — the real danger from the conversation, made concrete and *deterministic*.
**Language.** Python.

We force the exact bad interleaving with events, so it fires **every run** and the
mechanism is unmistakable. An object starts with `refcount = 1` (a "producer"
holds it). A "consumer" acquires a new reference (`refcount += 1`, as
read-modify-write) at the *same time* the producer releases its reference
(`refcount -= 1`). We schedule the consumer's stale write to clobber the
producer's decrement — the lost update — so the count wrongly reaches 0 and the
object is freed while the consumer still believes it holds a live reference.

**Starter / demonstration** (`use_after_free.py`):

```python
import threading

# Deterministic re-enactment of a lost-increment use-after-free.
# Events pin the exact interleaving a CPU is *allowed* to pick on its own.

obj = {"rc": 1, "freed": False}   # refcount starts at 1 (producer holds it)

consumer_has_read = threading.Event()
producer_done = threading.Event()

def consumer():
    n = obj["rc"]                 # (1) READ rc  -> sees 1
    consumer_has_read.set()       #     "I've read the old value"
    producer_done.wait()          #     ...producer now does its entire -1 + free...
    obj["rc"] = n + 1             # (2) WRITE 1+1 = 2, clobbering producer's 0
    # The consumer now believes it safely holds a reference and uses the object:
    if obj["freed"]:
        print("USE-AFTER-FREE: consumer is using an object "
              "the producer already freed!")
    print(f"final refcount = {obj['rc']}   freed = {obj['freed']}")

def producer():
    consumer_has_read.wait()      # let the consumer read the old value first
    obj["rc"] = obj["rc"] - 1     # (a) 1 -> 0
    if obj["rc"] == 0:
        obj["freed"] = True       # (b) free(): nobody's using it... supposedly
    producer_done.set()

tc = threading.Thread(target=consumer)
tp = threading.Thread(target=producer)
tc.start(); tp.start()
tc.join(); tp.join()
```

**Task.**
1. Trace by hand: consumer reads `rc = 1`; producer decrements `1 -> 0` and frees;
   consumer writes back `1 + 1 = 2`. What is `freed`? Is the object actually in
   use?
2. Run it. Confirm it prints the use-after-free line **every time**.
3. Explain in one sentence why the *lock* fix (below) prevents it.

**Expected observation.**
```
USE-AFTER-FREE: consumer is using an object the producer already freed!
final refcount = 2   freed = True
```
The refcount ends at a nonsensical **2** while `freed` is **True** — the object
was freed *and* is still "held." That contradiction is the use-after-free: in a
real program the freed memory would already be reallocated, and the consumer's
next read would return an attacker's bytes.

**Full solution** (make acquire/release atomic with a lock — the mechanism, minus
the artificial scheduling):

```python
import threading

obj = {"rc": 1, "freed": False}
lock = threading.Lock()

def consumer():
    with lock:                    # acquire is now indivisible w.r.t. release
        if obj["freed"]:
            print("would be UAF"); return
        obj["rc"] += 1            # safe: can't be clobbered by a release
    # ... use the object ...
    with lock:
        obj["rc"] -= 1
        if obj["rc"] == 0:
            obj["freed"] = True

def producer():
    with lock:                    # release can't interleave with an acquire
        obj["rc"] -= 1
        if obj["rc"] == 0:
            obj["freed"] = True

tc = threading.Thread(target=consumer)
tp = threading.Thread(target=producer)
tc.start(); tp.start()
tc.join(); tp.join()
print(f"final refcount = {obj['rc']}   freed = {obj['freed']}")   # no UAF
```

(In real kernel/Rust code you'd use an **atomic** refcount, e.g. `Arc<T>`, rather
than a lock — an atomic increment *physically can't* be split, so the acquire can
never be lost. Same fix, cheaper.)

**Reinforces.** A single lost refcount update is not a cosmetic off-by-one — it
frees live memory, which is a use-after-free, which is a security bug. This is the
exact hazard Lockstep exists to avoid re-introducing, and why `Arc<T>` /
atomics matter (Module 5).

---

## Mastery check

Ask these in the learner's own words; don't advance until all three are solid.

1. **Draw the racing-increment timeline** (two CPUs, `counter = 0`, each does
   `counter += 1`) and explain why the final value can be **1**, not 2. Where
   exactly did the lost increment go?
2. **Lock vs. atomic:** what does each one guarantee, and when would you reach for
   one over the other? (Looking for: lock serializes a *region* of several
   operations; atomic makes *one* operation hardware-indivisible; multi-step
   invariant → lock, single bump → atomic.)
3. **Why is a lost reference-count update dangerous** in a way a lost score-counter
   isn't? Walk the chain from "one `+1`/`-1` vanished" to "use-after-free," and
   say why that's a security bug, not just a wrong number.

*(Bonus, if they're flying:* why doesn't Python's GIL save you here, and why is
"it passed my tests" not evidence a race is absent?)*

---

## Connects to Lockstep

This bug — the data race, and the lost-refcount use-after-free it enables — is the
**entire reason Lockstep exists.** Everything downstream is machinery for it:

- **KCSAN (Kernel Concurrency SANitizer) is the tool that catches exactly this.**
  It instruments every memory access in a running kernel; when two unsynchronized
  CPUs hit the same address and at least one writes, it prints a `BUG: KCSAN`
  report. It is Lockstep's **primary oracle** — the thing that says pass/fail —
  precisely because you *cannot* catch a race by comparing outputs (you saw why:
  the answer is different every run and sometimes accidentally *right*). You need a
  detector watching the accesses themselves. The exercises you just ran are, in
  miniature, exactly what KCSAN watches for.
- **This is what M0 just brought online.** M0 booted a real Linux kernel under
  KCSAN (plus lockdep, the deadlock detector) and confirmed a **clean baseline** on
  stock code — the oracle is now live and trustworthy. Every future Lockstep
  transplant will be judged by KCSAN: *did the rewrite add any new race that
  wasn't there before?* No new `BUG: KCSAN` versus baseline → the transplant
  didn't introduce the bug you spent this lesson learning to fear.
- **Honesty carry-over:** just like "it passed my tests" doesn't prove a race is
  absent, **KCSAN staying silent doesn't prove race-freedom** — only that it
  didn't *observe* one under this workload. That's Module 4's whole subtlety, and
  it's why Lockstep's claim is "adds no *new* race the detector can find," never
  "provably race-free." You already have the intuition for why the weaker claim is
  the honest one.

> Next: **Module 3 — memory ordering**, the subtler cousin of the data race:
> even *with* coordination, *when* one CPU sees another's write is its own rule
> that can be right or wrong. Then **Module 4**, where we meet KCSAN and friends
> properly.
