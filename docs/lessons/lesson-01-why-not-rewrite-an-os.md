# Lesson 01 — Why you can't just AI-rewrite an OS

## For the teaching LLM

- **Start with intuition before jargon.** Open with the analogy (bank teller vs. check processor) to ground "pure" vs "concurrent" in something they've seen. Don't say "pure function" yet.
- **Be Socratic — ask before telling.** Ask "what would you need to check to know a rewrite was safe?" *before* explaining output comparison. If they struggle, offer a concrete function and ask them to imagine testing it.
- **Pace in beats.** Each beat = one intuition + one probing question + listen to their answer (right or wrong) + course-correct if needed. Don't move to the next beat until they can voice the idea back to you.
- **Use their words.** When they answer "it touches shared state," say "so you're saying the meaning isn't in the output — it's in *how* it coordinates with other parts. That's the key insight."
- **Check understanding before advancing.** After the lock example, pause and ask "can you give me a different example of concurrent code?" If they can, move on. If not, re-explain the lock/data connection.

## Objectives

By the end of this lesson, you can:
1. **Draw the boundary:** explain what CGIR already solves (pure functions) and what Lockstep exists for (concurrent regions).
2. **Spot the difference:** given a function, classify it as "rewritable by comparing outputs" or "needs a different strategy" — and say why.
3. **Find the hard part:** explain why a lock's *meaning* spreads across many functions, making single-function rewrites unsafe.

## Conversation flow

### Opening hook

**Teaching LLM:** Imagine you're a bank. You hire an AI to rewrite your check-processing code from C to Rust. How do you know the rewrite didn't lose any money?

*[Wait for answer. Likely responses:]*
- *Right:* "Run it on the same checks, compare the output amounts."
- *Vague:* "Test it a lot." → "Good instinct. But test *what*? Give me a concrete check."
- *Wrong:* "Just make sure it compiles." → "Compiling is step 1, but that doesn't prove correctness."

**Follow-up (if they got it right):** Perfect — compare inputs to outputs. Now: that works for processing checks. Does it work for *everything* the bank's computer does? What about the part that tracks which teller has how much cash in their drawer right now, when multiple tellers are using it simultaneously?

---

### Beat 1: The two kinds of code

**Teaching LLM:** Let me show you the two worlds. First, a pure function.

```c
// Pure: input → output, nothing else
int mul_by_x(int w, int x) {
    return w * x;
}
```

This lives alone. Give it 5 and 3, it returns 15. Rewrite it to Rust, run it on the same inputs a million times, same outputs — you're done. CGIR (the parent project) does exactly this. It found 146 real functions like this in the Linux kernel, rewrote them to Rust, booted them inside a live kernel, and proved the behavior never changed. That part is solved.

Now the second world — here's a function from the real kernel:

```c
// Not pure: meaning is in coordination
void schedule_task(struct rq *rq, struct task *t) {
    spin_lock(&rq->lock);           // ← grab the token
    list_add(&t->link, &rq->tasks); // ← touch shared list
    spin_unlock(&rq->lock);         // ← release token
}
```

**Probing question:** If I rewrite this to Rust and run it on the same inputs, comparing the return value (which is void) — will I know it's safe?

*[Listen. Common answers:]*
- *Right:* "No, because there's no return value to compare. The meaning is the locking."
- *Half-right:* "You'd need to run it with multiple threads to see if it's truly safe." → "Exactly — and even then, you might miss a race that only happens 1 in 100,000 times."
- *Wrong:* "If it compiles in Rust, it's safe." → "Rust is safer than C, but we're adding new Rust code here. Rust still lets you write data races with locks."

**Key point to land:** The return value isn't the meaning. The meaning is: *"even though three other CPUs are also touching this list, the list doesn't get corrupted."* You cannot see that by comparing return values on one thread.

---

### Beat 2: Why the function boundary breaks

**Teaching LLM:** Here's the hard part. That `spin_lock` on `rq->lock` — does it protect `rq->tasks`? You and I see the code and say yes. But the compiler doesn't. The lock is grabbed here, the list is touched here, five other functions also touch it under the same lock, and *nowhere in the C code is this rule written down.* It's convention.

Now you rewrite `schedule_task` to Rust. You decide: this lock protects this data. You wrap them in a `SpinLock<T>`. Beautiful. But:

1. One of those other five functions wasn't rewritten — it's still C.
2. It still assumes `rq->lock` protects the list.
3. The Rust side says "you can only access this list through me."
4. The C side reaches in and touches the list directly.
5. **Race condition. The rewrite is unsafe, even though the Rust code is locally correct.**

**Probing question:** If you had to rewrite `schedule_task` safely, what would you need to know *before* you started?

*[Listen. Target answer:]*
- *Right:* "You'd need to find *all* the code touching this data, not just the one function."
- *Close:* "You'd need to understand the whole locking design." → "Yes, and more specifically, which functions?"
- *Wrong:* "You'd use a static analyzer to find all accesses." → "That helps, but Lockstep is about finding them and *rewriting them as a group*."

**Key point:** You can't rewrite a function in isolation when a lock is involved. The function boundary is wrong. You need to rewrite a **region** — the set of all code governed by one locking rule.

---

### Beat 3: The sorting CGIR already did

**Teaching LLM:** So here's what CGIR's analysis already figured out. It has a classifier:

- **Pure:** takes inputs, returns outputs, touches nothing shared. CGIR rewrites these one at a time.
- **Not pure:** grabs locks, walks shared lists, updates reference counts. CGIR refuses these.

The "refuses pile" is exactly Lockstep's input. It's like a hospital: CGIR handles the routine checkups. Lockstep handles the surgery. Different tools, same patient.

**Probing question:** If CGIR already *knows* which functions are concurrent, why can't it just refuse to rewrite them and call it done?

*[Listen. Target answer:]*
- *Right:* "Because the kernel is 70% concurrent code. You need a second tool to handle it."
- *Close:* "Because concurrent code is still important to migrate to Rust." → "Right, and...?"
- *Wrong:* "Because Lockstep rewrites it the same way CGIR does." → "No — Lockstep uses a completely different strategy, which we'll see in the next modules."

**Key point:** CGIR and Lockstep split the work. CGIR eats the pure core. Lockstep eats the concurrent region around it. The boundary between done and hard stays explicit.

---

### Beat 4: The lock-protects-data mystery

**Teaching LLM:** Now I'm going to show you the one thing that makes Lockstep a *research* project instead of just a script.

In the kernel, there's a real pattern: the `request` struct. Abbreviated:

```c
struct request {
    struct list_head list;        // linked list of all requests
    spinlock_t lock;              // protects... what?
    unsigned int ref_count;       // number of users
    struct bio *bio;              // the I/O operation
    int flags;                    // request state
};
```

Somewhere in the code:

```c
spin_lock(&rq->lock);
rq->ref_count++;
rq->flags |= DISPATCHED;
spin_unlock(&rq->lock);
```

**Question for you:** which fields does `lock` protect? `ref_count` and `flags`? All five? Just `ref_count`?

*[Wait. They likely say "I don't know from the code alone."]*

**Exactly.** The rule is in the developer's head. It's *convention*. In a new codebase, you might protect `ref_count` separately and touch `flags` with a different lock. There's no written contract.

**Probing question:** If I could watch the kernel run — see every lock grab and every memory access — could I *verify* my guess about which fields belong to which lock?

*[They likely say "yes" — this is the key intuition.]*

That's `lockdep`, the kernel's lock validator. It tracks: *"I saw this lock held during access to this memory." Boom. Dynamic proof your guess was right (or wrong). Lockstep uses that.

**Key point:** Inferring lock protection is hybrid — static guess + dynamic confirmation. Not perfect, but honest.

---

### Beat 5: Closing synthesis

**Teaching LLM:** Bring it home. In one sentence: What makes concurrent code hard to rewrite?

*[Listen. Target answer:]*
- *Right:* "The meaning isn't in one function — it's spread across all the code a lock touches, and the rule is never written down."
- *Close:* "It's hard to test." → "That's a symptom. What's the deeper reason?"

That's it. CGIR handles pure functions because the function boundary matches the meaning boundary. Lockstep handles concurrent regions because the concurrency boundary cuts across functions — and finding it requires dynamic tools the pure world never needed.

---

## Misconceptions to catch

### Misconception 1: "Rust's type system automatically solves concurrency"

**Wrong mental model:** If I write Rust code with proper locks, the compiler will catch concurrency bugs.

**Reality:** Rust's type system prevents *memory unsafety* (use-after-free, buffer overflow). Data races are still possible in Rust if you use locks incorrectly. The compiler can't know which lock is *supposed* to protect which data — that's a semantic rule, not a type rule. That's what Lockstep solves: *encoding* that semantic rule as a type.

**How to correct it:** "Rust prevents segfaults and memory corruption. But two threads writing the same variable at once is a data race even in Rust. What Lockstep does is make it impossible to touch the data without holding the lock — the data lives *inside* the lock object, so you can't accidentally forget."

### Misconception 2: "We just need better static analysis to find which lock protects what"

**Wrong mental model:** A clever enough analyzer can read C code and infer all the locking invariants.

**Reality:** C code is too dynamic. A lock might protect different data depending on the context, the code path, or runtime state. You need to see what the kernel *actually does* under load, not just what the code *might* do. That's why Lockstep uses `lockdep` — the tool that watches the real system.

**How to correct it:** "Static analysis is great for finding candidates. But the real answer is in the running kernel. We make a guess (static), then ask the kernel 'is this what you actually observe?' (dynamic). That's why M1's whole job is: extract a guess, run lockdep, compare."

### Misconception 3: "Once we rewrite the first concurrent function, the rest follow the same pattern"

**Wrong mental model:** Concurrency bugs are uniform; solving one teaches us how to solve all of them.

**Reality:** Different regions have different concurrency rules. A critical section protected by a spinlock is different from an RCU read epoch (readers lock-free, writers coordinate). A reference count protected by atomics is different from both. Each has its own Rust-for-Linux abstraction and its own verification strategy. You have to handle them one region at a time.

**How to correct it:** "Concurrency isn't one bug class — it's several. A data race is one thing; a memory ordering violation is subtly different. A deadlock is a third. Each needs its own tool and its own rewrite pattern. Lockstep's advantage is that it applies the *same* process (extract, verify, transplant) to each, but the details change."

---

## Exercises (reps)

### Exercise 1: Classify pure vs. concurrent

**Task:** Given these five function signatures and descriptions, classify each as:
- **P** (pure — rewritable by output comparison, CGIR handles it)
- **C** (concurrent — Lockstep territory)

Say one sentence for each.

1. `int blake2b_compress(uint8_t *state, const uint8_t *data)` — reads input, updates a hash state array in place, returns nothing. Touches no other global data.

2. `void enqueue_request(struct queue *q, struct request *rq)` — grabs `q->lock`, adds `rq` to `q->pending`, wakes a waiter, releases lock.

3. `int strlen(const char *s)` — counts chars in a string until NUL. Reads only; no writes, no locks.

4. `void free_page(void *addr)` — decrements a reference count on a shared page structure; if the count reaches zero, deallocates. Uses an atomic decrement.

5. `struct item* get_cached(int id)` — looks up `id` in a global hash table protected by a read-write lock; does not modify; holds the read lock for the duration.

**Answers:**

1. **P.** It reads input and returns derived data; no shared state touched.
2. **C.** It grabs a lock and modifies shared data; the meaning is "safely coordinate with other CPUs."
3. **P.** It reads memory deterministically; the output is always the same for the same input.
4. **C.** Even though it uses an atomic, the correctness rule ("the page is freed iff the count is zero") involves coordination with other CPUs holding references.
5. **C.** It holds a lock for the lookup. The correctness rule is "don't let the table be modified while I'm reading it," which only matters when multiple CPUs are involved.

**What it reinforces:** The boundary isn't "does it use a lock" — it's "is the meaning about coordinating multiple CPUs." `strlen` is pure even though it reads memory. `free_page` is concurrent even though it doesn't hold an explicit lock — the atomic operation is the coordination.

---

### Exercise 2: Write pure vs. non-pure

**Task:** Write two small Python functions, about 5–10 lines each:
1. A **pure** function that computes something and returns it.
2. A **non-pure** function that reads and writes to a shared data structure (a global dict or a mutable object).

Then, run both in a multithreaded context and observe the difference.

**Starter code:**

```python
import sys
import threading

# Modern CPython's GIL makes a bare `x += 1` effectively atomic, so a naive
# race often won't show. Two things make it reliably visible: force frequent
# thread switches, and split the update into read()/write() calls so a switch
# can land *between* the read and the write. (This is a teaching trick to
# surface a race the language usually hides — the race is real either way.)
sys.setswitchinterval(1e-9)

# Shared data (simulating kernel state)
counter = {"value": 0}  # dict so it's mutable and shared
lock = threading.Lock()

# TODO: Implement pure_multiply
def pure_multiply(a, b):
    # Should take inputs and return output; touch nothing shared.
    pass

# read() and write() split the update so a thread switch can interleave them.
def read():
    return counter["value"]
def write(v):
    counter["value"] = v

# TODO: Implement increment_counter
def increment_counter(n_times):
    # Should do write(read() + 1), n_times.
    # Try it WITHOUT a lock first, then WITH a lock.
    # Observe what happens to the final value.
    pass

# Main
if __name__ == "__main__":
    # Test 1: pure function (will always work)
    results = []
    def worker_pure():
        for _ in range(100000):
            results.append(pure_multiply(2, 3))
    
    threads = [threading.Thread(target=worker_pure) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"Pure results (should all be 6): {set(results)}")
    
    # Test 2: non-pure without lock
    counter["value"] = 0
    threads = [threading.Thread(target=increment_counter, args=(100000,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"Counter without lock (expected 400000, got): {counter['value']}")
    
    # Test 3: non-pure with lock (what Lockstep ensures)
    # TODO: modify increment_counter to use `lock` and re-run.
```

**Your solution (one way):**

```python
def pure_multiply(a, b):
    return a * b

def increment_counter(n_times):
    # WITHOUT lock (first pass) — a switch can land between read() and write(),
    # so two threads read the same value and one increment is lost:
    for _ in range(n_times):
        write(read() + 1)

    # WITH lock (second pass) — the lock makes read+write indivisible, so the
    # count is exact. Swap the loop above for this and re-run:
    # for _ in range(n_times):
    #     with lock:
    #         write(read() + 1)
```

> Verified: without the lock this reliably prints well under 400000 (updates
> lost to the race); with the lock it prints exactly 400000. If you ever see the
> exact number without a lock, raise the thread count — the race is there.

**What to observe:**
- Test 1: All 400,000 results are 6 — pure functions are deterministic.
- Test 2 (no lock): The final counter is less than 400,000 (maybe 100,000–350,000) — lost increments due to race.
- Test 3 (with lock): The final counter is exactly 400,000 — the lock serializes access.

**What it reinforces:** This is the difference CGIR and Lockstep are dealing with. CGIR can prove `pure_multiply` works by running it once. `increment_counter` *cannot* be proven by running it once — you need to see it under concurrent stress and check that no races happen.

---

## Mastery check

You must answer these three questions in your own words to move forward:

1. **In your own words, what makes a function "un-rewritable" by the output-comparison method?** (Hint: think about what "output" means when the function touches shared state.)

2. **Why can't you rewrite one concurrent function and leave the others alone?** (Hint: think about a lock, the data it protects, and the functions that touch that data.)

3. **If CGIR already knows which code is "pure" and which is "not pure," what does Lockstep need to add?** (Hint: hint — what's the difference between "refusing to rewrite it" and "rewriting it safely"?)

**Passing criterion:** You can explain each without reading the lesson. If you hedge ("I think..."), that's fine. If you can't explain it, ask the teaching LLM to re-explain that beat, and try again.

---

## Connects to Lockstep

M0 has just run: we booted a real Linux kernel under KCSAN (the data-race detector) and lockdep (the deadlock detector) and confirmed the baseline is clean. That's the oracle coming online. Everything in this lesson — distinguishing pure from concurrent, understanding that the meaning spreads across functions, knowing that you need tools to detect races — is the *why* behind what M0 proved and why the gate exists. When we transplant concurrent regions to Rust in M1 and beyond, we'll verify them against the same tools that validated the original C code.
