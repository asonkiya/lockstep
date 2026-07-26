# Lesson 06 — The crux: which lock protects which data?

## For the teaching LLM

This lesson is the inflection point: it exposes why lock-to-data mapping is a research problem, not a solved one. Key angles:

- **It's convention, never written.** In C, there's a spinlock and a struct with 12 fields, and by social contract the lock "protects" 3 of them. But the compiler doesn't know which 3. Walk through an example where a human would say "obviously field X is protected" and show how a static analyzer sees *ambiguity*.
- **Static guesses + dynamic confirmation.** The hybrid is realistic: static analysis proposes "this lock probably guards these fields, because they're touched between lock/unlock." Then run the kernel under lockdep (which already tracks, at runtime, which lock class is held during which memory access) and *verify* the guess matches what lockdep observed.
- **Lockdep is a time traveler.** It doesn't make the guess; it watches execution and records "this lock class was held when this field was accessed." Feed those observations back to the static guess, and you've bootstrapped a verifier without solving the inference problem perfectly.
- **Some regions are un-transplantable.** Lock taken in function A, released in function B; conditional lock paths; error gotos that skip the unlock. These aren't bugs in the C — they're *intentional patterns* that have no safe Rust-for-Linux analogue yet. Refusing them with a reason is the right move.
- **"Matches lockdep" is the success metric, not "provably complete."** This is honest: if the extracted map matches what lockdep observed during a representative load, you've captured the real locking discipline of the kernel, even if there are exotic paths you missed.

The core insight: **this is a dual-oracle problem disguised as a static-analysis problem.** Static analysis can't do it alone (ambiguity in the code), and lockdep can't do it alone (it only observes what actually runs). Together they're powerful.

---

## Objectives

After this lesson, a learner should:
- Understand why "just read the C" doesn't work: there are always multiple plausible lock→data mappings.
- Visualize a lock-protecting-data relationship and recognize when it's ambiguous.
- Understand the hybrid strategy: static → propose candidates; dynamic → verify against lockdep.
- Know what "matches lockdep" means as a success criterion and why it's weaker than (but more honest than) "provably correct."
- Recognize and name three patterns that make a region un-transplantable and know how to flag them.

---

## Conversation flow

### Hook: The ambiguity (5 min)

Start here:

> "Imagine you're transplanting this C code to Rust:
> ```c
> struct request_queue {
>   spinlock_t lock;
>   int num_requests;
>   struct list_head pending;
>   int refcount;
>   char name[32];
> };
> 
> void enqueue_locked(struct request_queue *q, struct request *r) {
>   spin_lock(&q->lock);
>   list_add(&r->node, &q->pending);
>   q->num_requests++;
>   spin_unlock(&q->lock);
> }
> ```
> 
> To make a `SpinLock<T>` in Rust, you need to know: which fields go in T? In other words, which fields does `q->lock` actually protect?"

Let them guess. They'll likely say "all of them" or "the ones touched in the critical section." Both are reasonable first guesses and both are wrong or incomplete.

**Socratic challenge:**

- "What if `q->refcount` is incremented in a *different* function, also under `q->lock`, but we haven't seen that code yet?"
- "What if `q->name` is written once at allocation and never touched again? Does the lock protect it?"
- "What if there's a reader that doesn't take the lock but only reads `q->name`? Is that a race?"

Point: **there is ambiguity in the static C.** Without seeing the entire codebase's access patterns, you can't be sure which fields truly need protection.

### The static part (10 min)

Now walk through what a static analyzer would do:

> "Okay, a static tool can do this:
> 1. Find all critical sections on this lock (`spin_lock(&q->lock)` … `spin_unlock`).
> 2. For each one, record every field access inside it.
> 3. Build a candidate set: fields touched inside the critical section *are* likely protected."

Show the analysis on the code:
- Inside `enqueue_locked`'s critical section: `q->pending` is accessed (`list_add`), `q->num_requests` is written.
- Candidate map: `lock` → `{pending, num_requests}`.
- Missing: any accesses to these fields *outside* a critical section on this lock (which would flag a race) or in other functions.

**The honest limit:** static analysis can only see what's explicitly in the source. If the field is accessed through an alias, or in an out-of-line function, or in a macro, the guess gets less confident.

### The dynamic part: lockdep (10 min)

Now introduce the closer:

> "Here's the gift: the Linux kernel already runs under lockdep, a dynamic lock-order validator. Lockdep is *constantly watching*. When your code runs:
> 1. Every `spin_lock(&q->lock)` is recorded with the lock *address* and *class*.
> 2. Every memory access is observed.
> 3. Lockdep notes: 'This memory location at `&q->pending` was accessed while lock class X was held.'
> 
> After a representative load (the kernel booting, running tests, fuzzing), lockdep has a log of which lock classes were held during which field accesses. **That log is the ground truth.**"

Key insight:

> "Lockdep doesn't *infer* which lock should protect what. It *observes* which lock *actually was held* during accesses. So you take your static guess — 'lock protects {pending, num_requests}' — and you check it against lockdep's observations: 'Did lockdep see both those fields accessed only under this lock (or atomically)?'"

### The realistic hybrid (5 min)

Tie it together:

> "So M1's job is:
> 1. Run static analysis on the C, extract candidate lock→field edges.
> 2. Boot the kernel under KCSAN + lockdep, run a representative load.
> 3. Extract lockdep's observations.
> 4. Compare: does the static guess match lockdep?
> 
> If they match, you've found the real locking discipline. If they don't, either:
> - The static analysis was too conservative (locked more than needed), or
> - The static analysis was too permissive (missed a protected field).
> 
> Either way, lockdep is the tiebreaker."

**Why this works:**
- Static analysis can't see the full picture, but it can make a *plausible* guess.
- Lockdep can't make the guess, but it can verify it against reality.
- Together: "Our guess matches what the kernel actually does."

### Honestly un-transplantable regions (8 min)

Now complicate it:

> "Some C patterns have no clean Rust-for-Linux analogue. Lockstep should *detect and skip* these regions, exactly as CGIR skips functions it can't lift."

Walk through three patterns:

**Pattern 1: Non-nested locks (lock and unlock in different functions)**

```c
void start_critical_section(struct request_queue *q) {
  spin_lock(&q->lock);
  // ... some work ...
}

void end_critical_section(struct request_queue *q) {
  // ... more work ...
  spin_unlock(&q->lock);  // lock was acquired in a different function!
}
```

Why un-transplantable: A `SpinLock<T>` guard is scoped to where you call `.lock()`. You can't `lock()` in one function and `unlock()` in another — Rust's borrow checker forbids it. **Solution:** detect this pattern (lock acquired in one function, released in another), flag it, *skip it with a reason* in the report.

**Pattern 2: Conditional lock, error unwinding**

```c
void possibly_locked_work(struct request_queue *q, int cond) {
  if (cond) {
    spin_lock(&q->lock);
  }
  do_work(q);  // might modify q->fields
  if (cond) {
    spin_unlock(&q->lock);
  }
}

void error_case(struct request_queue *q) {
  spin_lock(&q->lock);
  if (error_check()) {
    goto unlock_and_bail;
  }
  // normal path
  spin_unlock(&q->lock);
  return;
unlock_and_bail:
  spin_unlock(&q->lock);
  return -EIO;
}
```

**Why hard:** Rust's guard model works *only* for normal scope unwinding. A manually-jumped goto that duplicates the unlock is error-prone (what if the unlock is accidentally skipped?) and gets worse with nested locks. **Solution:** detect multiple paths to the same unlock, flag as "requires manual review," skip it with a reason.

**Pattern 3: C idiom with no R4L equivalent yet**

```c
// Hypothetical: a custom lock type that isn't spinlock/mutex/RCU
struct custom_lock {
  atomic_t holder;
  int fairness_token;
};

// Lockstep has SpinLock, Rcu, Arc. But no abstraction for this pattern.
// That's not a bug — it means "this region is out of scope until R4L provides
// the abstraction."
```

**Frame it honestly:** "Some patterns are *technically* transplantable in principle but don't have a Rust-for-Linux abstraction yet. When you hit those, you don't force them — you flag 'out of scope' and wait for upstream."

---

## Misconceptions to catch

### Misconception 1: "Just read the code, you'll see the lock"

**Correct:** You can *guess*, but there's ambiguity. Example: if you see `spin_lock(&q->lock)` and `q->field` inside the critical section, you know field might be protected. But what about reads *outside* the critical section? What about accesses through a pointer alias? What about fields that are accessed in 10 other functions under the same lock, but we haven't read those functions yet?

**Remedy:** Show the ambiguity in code. Use a medium-sized struct with many fields and a single critical section, then ask: "which fields are definitely protected?" Most answers will be too broad.

### Misconception 2: "The tool should handle every region"

**Correct:** No. Some regions are intentionally un-transplantable, and that's fine. CGIR already teaches this lesson (it skips functions it can't lift). Lockstep does the same: the output is a *breakdown*, not "everything transplanted."

**Remedy:** Re-read `docs/design.md` §5 together. Point out the phrase "flagged, not forced." This is a feature, not a bug.

### Misconception 3: "Lockdep proves the mapping is correct"

**Nuance:** Lockdep observes the runtime behavior under a given workload. If lockdep saw `lock` held during accesses to `{field1, field2}` and never saw accesses outside the lock, that's strong evidence those fields are protected. But it's not a *proof* — it's a high-confidence observation. If there's an error path that skips a workload the tests don't exercise, lockdep won't catch it. **Claim:** "matches lockdep under the test load," not "provably correct."

**Remedy:** Re-read Module 4 (the sanitizers) together, especially the honesty point: "sound-ish, not complete."

---

## Exercises (reps)

### Exercise A: Be the static analyzer (reading comprehension)

**Goal:** Produce a candidate lock→field map and spot suspicious accesses.

**Snippet (C-like pseudocode):**

```c
struct packet_cache {
  spinlock_t lock;
  int valid;         // Is there a cached packet?
  char *data;        // Cached packet data
  int size;          // Size of data
  int hit_count;     // Number of cache hits (diagnostic)
  int read_only;     // Set once at init, never changes
};

int cache_lookup(struct packet_cache *c, char **out) {
  // NOT under the lock
  if (!c->read_only) {
    return -EINVAL;
  }

  spin_lock(&c->lock);
  if (c->valid) {
    *out = c->data;
    c->hit_count++;
    spin_unlock(&c->lock);
    return c->size;
  }
  spin_unlock(&c->lock);
  return -ENOENT;
}

void cache_insert(struct packet_cache *c, char *new_data, int sz) {
  spin_lock(&c->lock);
  c->valid = 1;
  c->data = new_data;
  c->size = sz;
  spin_unlock(&c->lock);
  // Diagnostic print OUTSIDE the lock
  pr_info("Cached packet, size=%d\n", c->size);
}
```

**Task:** 
1. Build the candidate lock→field map. Which fields are touched *inside* a critical section on `c->lock`?
2. Flag each access as SAFE (inside lock), SUSPICIOUS (accessed outside lock), or SPECIAL (needs explanation).
3. Which field(s) are *clearly* not protected by the lock?

**Solution:**

| Field | Inside lock | Outside lock | Map |
|-------|-------------|--------------|-----|
| `valid` | yes (cache_lookup, cache_insert) | no | PROTECTED |
| `data` | yes (cache_lookup, cache_insert) | no | PROTECTED |
| `size` | yes (cache_lookup, cache_insert) | **YES** (pr_info in cache_insert) | **SUSPICIOUS** — accessed outside the lock after unlock; potential race if another CPU modifies `c->size` between `spin_unlock` and `pr_info`. |
| `hit_count` | yes (cache_lookup) | no | PROTECTED (diagnostic only, atomicity less critical, but still guarded) |
| `read_only` | **NO** — only accessed outside (cache_lookup) | yes (cache_lookup) | NOT PROTECTED — set once at init, never modified in critical sections. Safe by immutability, not by lock. |

**Candidate map:** `lock` → `{valid, data, size, hit_count}` (conservative).

**Suspicious access:** The `c->size` access in `pr_info` after `spin_unlock` is outside the lock. This could be a race if another CPU concurrently runs `cache_insert` and modifies `c->size`. **Next step:** check lockdep — did it observe `c->size` accesses only under the lock during testing?

**What this reinforces:** 
- Spotting the gap between what's guarded and what's accessed.
- Recognizing that a field touched both inside and outside a critical section is a red flag (either a race or the field isn't actually protected).
- Understanding that the static guess is provisional — lockdep will confirm or contradict it.

---

### Exercise B: Spot the un-transplantable region (classification)

**Goal:** Learn to recognize and name patterns that have no clean Rust-for-Linux shape.

**Three snippets:**

**Snippet B1: Clean critical section (TRANSPLANTABLE)**
```c
void update_queue_state(struct request_queue *q, int new_state) {
  spin_lock(&q->lock);
  q->state = new_state;
  q->last_update_time = ktime_get();
  spin_unlock(&q->lock);
}
```
**Classification:** TRANSPLANTABLE. Single lock, acquired and released within the same function scope. Clean for `SpinLock<T>` transplant.

**Snippet B2: Split lock (SKIP WITH REASON)**
```c
void acquire_and_prepare(struct request_queue *q) {
  spin_lock(&q->lock);
  q->prepared = 1;
}

void finalize_and_release(struct request_queue *q) {
  q->finalized = 1;
  spin_unlock(&q->lock);
}

// Called in order in the same caller
void do_work(struct request_queue *q) {
  acquire_and_prepare(q);
  expensive_work(q);
  finalize_and_release(q);
}
```
**Classification:** SKIP WITH REASON. Lock acquired in one function, released in another. No Rust-for-Linux guard can span across function boundaries. **Reason to skip:** "Non-nested lock region; lock acquired and released in separate functions. Transplantable only with refactoring to nest them in a single scope."

**Snippet B3: Conditional lock with error unwinding (SKIP WITH REASON)**
```c
int conditional_protected_work(struct request_queue *q, int unsafe_mode) {
  if (!unsafe_mode) {
    spin_lock(&q->lock);
  }
  
  if (do_work(q) < 0) {
    goto error_out;  // Skip unlock if we didn't acquire
  }
  
  if (!unsafe_mode) {
    spin_unlock(&q->lock);
  }
  return 0;

error_out:
  if (!unsafe_mode) {
    spin_unlock(&q->lock);  // Duplicated unlock path
  }
  return -EIO;
}
```
**Classification:** SKIP WITH REASON. Conditional lock (held only if `!unsafe_mode`) with multiple unlock paths. Rust's guard model works only for deterministic scoping. **Reason to skip:** "Conditional lock acquisition with multiple exit paths; no guarantee all unlocks are reached. Requires manual restructuring to guarantee lock/unlock pairing."

**Task:** For each snippet, decide: TRANSPLANTABLE or SKIP WITH REASON. If skipping, write the one-sentence reason.

**Solution:** See classifications above.

**What this reinforces:**
- Recognizing structural patterns that Rust's type system can't express.
- Understanding that "skip" is a deliberate choice, not a limitation to overcomplicate.
- The parallel to CGIR: tools have honest boundaries.

---

### Exercise C: Lock and data are a unit (tiny Python simulation — optional)

**Goal:** Feel the lock→data binding in code.

**Task:** Run this Python simulation; then modify it to fix the missing lock acquisition.

```python
import threading
import time

class BadCacheWithoutLock:
    """A shared cache accessed by multiple threads — DELIBERATELY BROKEN."""
    def __init__(self):
        self.data = {}
        self.access_count = 0
    
    def get(self, key):
        # MISSING: self.lock.acquire()
        if key in self.data:
            self.access_count += 1
            return self.data[key]
        return None
        # MISSING: self.lock.release()
    
    def put(self, key, value):
        # MISSING: self.lock.acquire()
        self.data[key] = value
        # MISSING: self.lock.release()

class GoodCacheWithLock:
    """The same cache, but with the lock protecting data."""
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {}
        self.access_count = 0
    
    def get(self, key):
        with self.lock:
            if key in self.data:
                self.access_count += 1
                return self.data[key]
        return None
    
    def put(self, key, value):
        with self.lock:
            self.data[key] = value

def race_test():
    """Hammer both caches with concurrent access. Bad one will show corrupted state."""
    print("Testing BadCache (no lock):")
    bad = BadCacheWithoutLock()
    
    def bad_hammer():
        for i in range(1000):
            bad.put(f"key{i}", i)
            bad.get(f"key{i % 100}")
    
    threads = [threading.Thread(target=bad_hammer) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    print(f"  Final data size: {len(bad.data)} (expected ~1000)")
    print(f"  Access count: {bad.access_count} (may be inconsistent)")
    print()
    
    print("Testing GoodCache (with lock):")
    good = GoodCacheWithLock()
    
    def good_hammer():
        for i in range(1000):
            good.put(f"key{i}", i)
            good.get(f"key{i % 100}")
    
    threads = [threading.Thread(target=good_hammer) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    print(f"  Final data size: {len(good.data)} (expected ~1000)")
    print(f"  Access count: {good.access_count} (consistent)")

if __name__ == "__main__":
    race_test()
```

**How to run:** Save to a file, `python3 file.py`. Observe that BadCache's state is corrupted (data dict size wrong, inconsistent counts) while GoodCache is clean.

**Extension:** Modify BadCache to add `self.lock = threading.Lock()` in `__init__`, then guard each method's `self.data` and `self.access_count` accesses. Observe that it now matches GoodCache's behavior.

**What this reinforces:**
- **The field and the lock are inseparable.** You can't think about protecting `self.data` without thinking about `self.lock`.
- **When you do the Rust rewrite** (`SpinLock<T>`), this binding becomes *syntactic* — the data literally lives inside the lock object. You've turned convention into compiler-checked law.
- The payoff of the static→dynamic hybrid is that you're *identifying* which fields and lock go together, so you can encode that relationship in Rust.

---

## Mastery check

Ask the learner to answer these in their own words. If they falter, re-explain that section using a different angle.

### Q1: Why can't you just read the C and know which lock protects which data?

**Acceptable answers include:**
- "Because the same lock might protect different fields in different functions, and you have to see all accesses to be sure."
- "Ambiguity: a field might be accessed inside a critical section in one place and outside in another, and static code alone can't tell you if that's a race or intentional."
- "In C, it's a convention, not a declaration. The compiler doesn't track it; you're inferring it from usage patterns."

**Red flag:** If they say "the code is clear, just read it" — gently push: "What if that field is also accessed in a function you haven't seen yet, under a different lock? How would you know?"

### Q2: Lockdep watches execution and records which lock was held during which memory access. How does that *verify* a static guess, even though it can't *make* the guess?

**Acceptable answers:**
- "Lockdep is the ground truth about what actually happened. If your static guess says the lock protects fields {A, B} and lockdep observed those fields being accessed only under that lock (or atomically), your guess was right."
- "Static analysis proposes; lockdep confirms. If they match, you know your inference is correct for the workload the tests cover."

**Red flag:** If they conflate lockdep's *observation* with lockdep's *inference* — explain: "Lockdep doesn't infer. It records. You do the inferring; lockdep is the oracle."

### Q3: Name one kind of region Lockstep will deliberately refuse (skip with a reason), and explain why refusing is the right move.

**Acceptable answers (pick one):**
- "Lock acquired in one function, released in another. Rust's guard model requires the lock to be acquired and released in the same scope; you can't have a guard outlive the function."
- "Conditional lock with multiple exit paths. You can't guarantee the unlock is reached if the lock was acquired conditionally and there are error gotos."
- "A C pattern with no Rust-for-Linux abstraction yet. Forcing it would mean hand-writing unsafe code, defeating the point."

**Reinforcement:** "Refusing isn't a limitation — it's honesty. CGIR refuses functions it can't lift; Lockstep refuses regions it can't safely transplant. Both projects emit a *breakdown*, not 'everything done.'"

---

## Connects to Lockstep

**This is M1's exact job.** 

Module 6 sets up the problem; M1 *solves* it for one small subsystem:
1. Static analysis extracts a candidate lock→field map from the C.
2. The kernel boots and runs under KCSAN + lockdep.
3. Lockdep's observations are extracted.
4. **M1's success criterion: the static map matches lockdep's observations.** Not "provably correct," not "100% precise," but "aligns with what the kernel actually does."

Once you have the confirmed map, you know which fields belong in `SpinLock<T>`. That's the input to M2 (hand-transplant one region) and M3 (model-synthesized transplant). Everything after M1 builds on the foundation that M1 established: "We know which lock protects which data, because we verified it dynamically."

The crux is **not** solving lock inference in the general case (an open problem in static analysis). The crux is **hybrid verification**: static→propose, dynamic→confirm. That's why this is a research project, not a solved problem ported. And it's why M1 is where the real work begins.

---

*Lesson written 2026-07-25. For the teaching LLM and a strong engineer with no kernel-concurrency background.*
