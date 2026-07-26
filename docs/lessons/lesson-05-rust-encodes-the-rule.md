# Lesson 05 — Rust: make the coordination rule a compiler-checked type

## For the teaching LLM

- **Let the compiler teach.** The single most valuable thing you can do in this lesson is get the learner to *run code that fails to compile*, then read the error together. Do not explain the borrow checker abstractly first — make them hit the wall, then interpret the message. The compiler's error is the lesson; your job is translation.
- **Assume general programming, not Rust.** They can read code, know what a thread and a lock are (Lessons 2–4), and have written multithreaded code in another language. They have *not* internalized ownership, borrowing, or `Send`/`Sync`. Define every Rust-specific term the first time it appears. Don't assume they know `Arc`, `Box`, `move`, or what a "guard" is.
- **Anchor to the prior lessons.** In Lesson 2 they watched a Python counter lose increments with no complaint from the runtime. The hook of *this* lesson is that Rust refuses to even compile that same shape of bug. Keep drawing that contrast: "the race you saw at runtime in Python — Rust catches it before the program runs."
- **The one big idea:** in C the rule "hold the lock before touching the data" is a *convention nobody enforces*; in Rust the data lives *inside* the lock, so the type system makes it *impossible* to touch it without locking. Everything else (ownership, `Send`/`Sync`) exists to make that guarantee airtight across threads. Land that before any syntax.
- **Be Socratic, but short.** Ask "what do you think this will do?" before every `rustc` run. Predicting "it'll print 400000" and then seeing `error[E0373]` is the moment the lesson lands. Let them be wrong out loud.
- **Don't over-teach the syntax.** Lifetimes, `impl`, traits in depth — all out of scope. The learner needs exactly enough Rust to see the coordination rule become a type. Resist rabbit holes; flag them ("there's a lot more to ownership, but here's the one slice we need").

## Objectives

By the end of this lesson, you can:
1. **Explain the shift:** describe how Rust moves the lock-protects-data rule from *convention* (C) to *compiler-checked type* (`Mutex<T>` / `SpinLock<T>` — the data lives inside the lock).
2. **Read a borrow-checker error:** hit `error[E0373]` / `error[E0382]` yourself, and explain in plain words what the compiler is refusing and why.
3. **Fix a data race at compile time:** take code that won't compile because it shares mutable state across threads, and repair it with `Arc<Mutex<T>>` — understanding what each layer does.
4. **State the ownership guarantee:** explain how move semantics and `Arc` prevent use-after-free (the Module 2 bug) *at the language level*, and why `Send`/`Sync` are the compiler's bookkeeping for "safe to share across threads."
5. **Connect to Lockstep:** explain why Rust catching races at *compile* time (vs. KCSAN catching them at *runtime*) is the reason Rust is the transplant target.

## Conversation flow

### Opening hook

**Teaching LLM:** Back in Lesson 2, we ran a Python counter: four threads, each incrementing a shared number 100,000 times. Expected total: 400,000. What did we actually get?

*[Wait. They should recall:]*
- *Right:* "Less than 400,000 — increments got lost to the race, and Python never complained."
- *Vague:* "It was wrong somehow." → "Right — the final number was too low. And crucially: the program *ran fine*. No crash, no warning. The bug was silent."

**Teaching LLM:** Hold onto that: *silent bug, caught only by watching the output.* In the kernel, KCSAN is the tool that watches for exactly this at runtime. Today I'm going to show you a language where that same bug — sharing a mutable number across threads with no lock — **doesn't run wrong. It doesn't compile at all.** The compiler refuses. That language is Rust, and that refusal is the entire reason it's our target.

Here's the core idea before we touch code. Two ways to say "this lock protects this data":

- **The C way (convention):** There's a lock `q->lock` and a list `q->items`. Everyone *agrees* to grab the lock before touching the list. Nothing enforces it. A new contributor who forgets — no error, no warning, just a race that shows up once a month under load.
- **The Rust way (the data lives inside the lock):** The list is *stored inside* the lock object. There is no way to name the list except by locking first. Locking hands you a temporary pass to the data; when the pass goes out of scope, the lock releases automatically. Forgetting to lock isn't a bug you can write — it's a sentence the compiler won't parse.

**Probing question:** In the C version, where does the rule "hold the lock first" actually *live*?

*[Listen.]*
- *Right:* "In the developer's head / in a comment / in convention — nowhere the compiler can see."
- *Close:* "In the code." → "Which line? Point at the line that *enforces* it." *(There isn't one — that's the point.)*

---

### Beat 1: The data lives *inside* the lock (`Mutex<T>` / `SpinLock<T>`)

**Teaching LLM:** Let me show you the shape. In C, the lock and the data are two separate variables that you, the human, mentally associate:

```c
spinlock_t lock;          // a lock
struct list items;        // some data
// convention: hold `lock` before touching `items`. Nothing enforces this.
```

In Rust, you write *one* thing — a lock **with the data inside it**:

```rust
use std::sync::Mutex;

let protected = Mutex::new(0i32);   // a lock that CONTAINS an i32
```

`Mutex<T>` means "a mutex wrapping a value of type `T`." Here `T` is `i32`. The `0` *is inside the lock*. There is no separate `i32` variable sitting next to it that you could touch directly — the only `0` in existence is the one the mutex owns.

So how do you touch it? You lock:

```rust
let mut guard = protected.lock().unwrap();  // .lock() gives you a "guard"
*guard += 1;                                 // reach the data THROUGH the guard
// guard goes out of scope here → lock releases AUTOMATICALLY
```

Three things to name:
- **`.lock()`** — acquires the lock and returns a **guard**. (The `.unwrap()` is Rust bookkeeping for a rare error case; ignore it for now.)
- **the guard** — a temporary handle. The *only* way to reach the inner value is through it (`*guard` reads/writes the inner `i32`). No guard, no access.
- **guard drop = unlock** — when the guard variable goes out of scope (the end of its block), Rust runs its cleanup, which *releases the lock*. You never call `unlock()`. You *cannot forget* to unlock, because "forgetting" would mean the guard never goes out of scope, which can't happen.

**Probing question:** In C, what's the classic bug where you grab a lock and then hit an early `return` or an error path?

*[Listen.]*
- *Right:* "You forget to unlock on that path — the lock stays held forever, deadlocking anything that waits on it."
- *Vague:* "Something with error handling." → "Exactly — an error path that skips the `spin_unlock`. Now: in the Rust version, can that happen?"

**Key point to land:** In Rust, unlock is tied to the guard going out of scope, and it goes out of scope on *every* exit path — normal return, early return, even a panic. The "forgot to unlock on the error path" bug is structurally impossible. The rule stopped being your responsibility and became the type's.

**Note on `SpinLock<T>`:** In the actual Linux kernel, Rust-for-Linux ships `SpinLock<T>` — same shape, kernel-flavored (it spins instead of sleeping, and integrates with lockdep). We practice with `std::sync::Mutex<T>` because you can run it on your laptop with `rustc`, but the *lesson* — data inside the lock, guard hands you access, drop releases — is identical. When you see `SpinLock<T>` in Lockstep's output, read it as "the same `Mutex<T>` idea, for the kernel."

---

### Beat 2: Ownership — one owner, or a counted set of them (`Box` / `Arc`)

**Teaching LLM:** Now the second pillar, and it's the one that's genuinely new if you've only used garbage-collected languages. In Rust, every value has exactly **one owner**. When the owner goes out of scope, the value is freed — automatically, deterministically, with no garbage collector.

```rust
let b = Box::new(42);   // `b` owns a heap-allocated 42
// ... use b ...
// end of scope: `b` is dropped, the 42 is freed. Done. No GC.
```

`Box<T>` is "a single-owner pointer to a `T` on the heap." One owner. When it drops, the memory is freed. That solves *when* to free — but it raises a question for our world:

**Probing question:** If a value has exactly one owner, and we want *four threads* to share one counter — who owns it?

*[Listen.]*
- *Right / close:* "They'd all need to own it? But you said only one owner." → "Exactly the tension. You can't hand a single-owner `Box` to four threads."
- *Wrong:* "Just copy it to each thread." → "Then they're four separate counters, not one shared one. We need them to see the *same* memory."

**Teaching LLM:** The answer is **`Arc<T>`** — "**A**tomically **R**eference **C**ounted." It's a pointer to a value on the heap, with a *counter* of how many owners currently exist. Each thread gets its own `Arc` handle (a `clone`, which bumps the count); when a handle drops, the count goes down; when the count hits zero, the value is freed. Exactly once. By the last one out.

```rust
use std::sync::Arc;

let shared = Arc::new(0i32);
let a = Arc::clone(&shared);   // count: 2
let b = Arc::clone(&shared);   // count: 3
// each of a, b, shared points at the SAME 0.
// as each drops, count decrements. At 0, the 0 is freed. Exactly once.
```

Now connect it to Module 2. Remember the reference-count use-after-free? A lost `ref_count++` meant the object was freed while someone still pointed at it. **`Arc` is that reference count — but the increment/decrement is atomic and the free-at-zero is done by the language, not by hand-written C.** You can't lose a count. You can't free twice. You can't free-then-use.

**Probing question:** In C, what are the two classic reference-count bugs?

*[Listen.]*
- *Right:* "Free too early (lost an increment → use-after-free), or never free (lost a decrement → leak)."
- *Close:* "Freeing at the wrong time." → "Split it into the two directions — too early vs. never."

**Key point:** `Arc` makes "free when the last user is done, exactly once" a *language guarantee*, not a convention you implement by hand. The hand-written `ref_count++/--` from Module 2 — the thing most likely to have a race — becomes something you don't write at all.

---

### Beat 3: `Send` and `Sync` — the compiler's cross-thread bookkeeping

**Teaching LLM:** One more piece, and I'll keep it high-level because you don't need the internals — you need to know *what the compiler is tracking on your behalf*.

Rust has two markers it attaches to every type:
- **`Send`** — "it is safe to *move* a value of this type to another thread." (Hand ownership across a thread boundary.)
- **`Sync`** — "it is safe for multiple threads to *access* a value of this type *at the same time* (through shared references)."

You almost never write these — the compiler figures them out automatically by looking at what a type is made of. The point is: **when you try to send something across threads that *isn't* safe to send, the compiler stops you.** These markers are how it knows.

Concretely, the pieces we've met combine like a lock-and-key:
- A plain `Rc<T>` (single-thread reference count) is **not `Send`** — its counter isn't atomic, so sharing it across threads would race the count itself. The compiler refuses to move it to a thread.
- `Arc<T>` **is `Send`/`Sync`** *when its contents are* — its counter is atomic, so it's safe.
- `Mutex<T>` is what makes its *contents* safe to share, because access is serialized through the guard.

**Probing question:** Given those rules, why do you think `Arc<Mutex<T>>` is the standard combo for "shared mutable state across threads"?

*[Listen.]*
- *Right:* "`Arc` lets multiple threads *own* it (share the pointer safely), and `Mutex` lets them *mutate* it safely (one at a time). You need both."
- *Close:* "`Arc` for sharing, `Mutex` for the lock." → "Yes — say what each one is protecting you from." *(`Arc`: who frees it / safe to share the handle. `Mutex`: only one writes at a time.)*

**Key point:** You don't reason about `Send`/`Sync` by hand. You reach for `Arc<Mutex<T>>`, and the compiler *verifies* — via `Send`/`Sync` — that what you built is actually safe to run on multiple threads. If it isn't, it won't compile. That's the machine doing the concurrency reasoning that, in C, lived only in the developer's head.

---

### Beat 4: Closing synthesis

**Teaching LLM:** Put the three pillars together in one sentence each:

1. **`Mutex<T>` / `SpinLock<T>`:** the data lives inside the lock, so you can't touch it without locking, and the lock releases itself.
2. **`Box` / `Arc`:** ownership decides *when* memory is freed — one owner, or a counted set — so use-after-free and double-free can't be written.
3. **`Send` / `Sync`:** the compiler tracks what's safe to move to / share across threads, and refuses the unsafe combinations.

**The synthesis:** In C, all three of these were *conventions in the developer's head*, checked (if at all) by a runtime tool like KCSAN after the fact. In Rust, they're *types*, checked by the compiler *before the program runs*. The exercises next will let you feel that difference physically — you'll write the Lesson-2 race, and the compiler will simply refuse.

---

## Misconceptions to catch

### Misconception 1: "Rust just has a garbage collector"

**Wrong mental model:** Rust frees memory for you, so it must be scanning the heap and collecting garbage like Java/Go/Python.

**Reality:** There is **no garbage collector**. Rust uses **ownership**: every value has exactly one owner, and the memory is freed *deterministically* the instant the owner goes out of scope — decided at *compile time*, not by a runtime scanner. `Arc` adds a reference count for *shared* ownership, but that's an explicit, atomic counter you opt into, not a background collector. There's no pause, no scan, no runtime tracing.

**How to correct it:** "Ask *when* does Java free an object — answer: 'whenever the GC gets around to it, at runtime.' Ask *when* does Rust free a `Box` — answer: 'exactly at the closing brace of its scope, decided by the compiler.' No runtime, no scan. Ownership is a *compile-time* discipline. The reason this matters for us: the kernel can't afford a garbage collector pausing it, which is exactly why R4L can use Rust at all."

### Misconception 2: "The lock and the data are separate, like in C"

**Wrong mental model:** `Mutex` is just C's `pthread_mutex` — a lock you declare next to your data and remember to grab, the same convention with different syntax.

**Reality:** In Rust the data lives **inside** the `Mutex<T>`. There is no separate data variable. The *only* way to name the inner value is to call `.lock()` and go through the guard. It is not "a lock you should remember to use before touching the data" — it is "the data is unreachable except by locking." The convention isn't just documented; it's made *unwriteable-around*.

**How to correct it:** "In C you have `spinlock_t lock;` and `int items;` as two variables — you *can* touch `items` without the lock; nothing stops you. In Rust you have one variable, `Mutex<i32>`, and the `i32` has no name of its own. Try to write code that reads the inner value without `.lock()` — you can't even express it. That's the whole difference: the rule went from 'please remember' to 'the sentence doesn't type-check.'"

---

## Exercises (reps)

> **Setup.** You have `rustc` locally. For each exercise: save the code to a file, then `rustc file.rs && ./file` (on Windows, `.\file.exe`). Or paste it into the Rust Playground at <https://play.rust-lang.org> and hit Run. The exercises that are *supposed to fail* will fail at `rustc` time — you won't get a binary at all. **Reading that failure is the exercise.** Predict the outcome out loud before you run each one.

---

### Exercise (a) — The Lesson-2 race, refused at compile time

**Goal:** Reproduce the exact shape of the Python race from Lesson 2 — four threads incrementing one shared counter — in Rust, and watch the compiler *reject it before it ever runs*. Then fix it with `Arc<Mutex<T>>` and see it produce the correct total. The insight: **Rust caught at COMPILE time the race that KCSAN catches at RUNTIME.**

**Before (won't compile).** Save as `race.rs`:

```rust
use std::thread;

fn main() {
    let mut counter = 0;                 // a plain, shared-mutable integer
    let mut handles = vec![];

    for _ in 0..4 {
        let handle = thread::spawn(|| {  // try to share `counter` across threads
            for _ in 0..100_000 {
                counter += 1;            // <-- mutate shared state, no lock
            }
        });
        handles.push(handle);
    }

    for h in handles {
        h.join().unwrap();
    }
    println!("counter = {}", counter);   // we WANT 400000
}
```

**Task:** Predict the output (a strong guess from Lesson 2 is "some number less than 400000, due to the race"). Then run `rustc race.rs`. What actually happens?

**Expected compiler output** (abridged — yours will look like this):

```
error[E0373]: closure may outlive the current function, but it borrows `counter`,
              which is owned by the current function
  --> race.rs:8:37
   |
 8 |         let handle = thread::spawn(|| {
   |                                    ^^ may outlive borrowed value `counter`
...
   = note: function requires argument type to outlive `'static`

error[E0499]: cannot borrow `counter` as mutable more than once at a time
  ...
```

There is **no binary**. The program never runs. Rust looked at "four threads all mutably touching `counter` with no synchronization" and refused — this is precisely a data race, and the borrow checker's rule "you may not have two mutable borrows of the same data at once" *is* the no-data-race rule. **In Python this same shape ran and silently lost increments. In Rust it doesn't compile.**

**After (compiles, and is correct).** Save as `race_fixed.rs`:

```rust
use std::sync::{Arc, Mutex};
use std::thread;

fn main() {
    let counter = Arc::new(Mutex::new(0));   // data lives INSIDE the lock,
                                             // shared ownership via Arc
    let mut handles = vec![];

    for _ in 0..4 {
        let counter = Arc::clone(&counter);  // each thread gets its own handle
        let handle = thread::spawn(move || { // `move` = give the handle to the thread
            for _ in 0..100_000 {
                let mut guard = counter.lock().unwrap(); // lock → guard
                *guard += 1;                             // mutate THROUGH the guard
                // guard drops at end of iteration → lock releases
            }
        });
        handles.push(handle);
    }

    for h in handles {
        h.join().unwrap();
    }
    println!("counter = {}", *counter.lock().unwrap()); // lock once more to read
}
```

**Task:** Run `rustc race_fixed.rs && ./race_fixed`.

**Expected result:**

```
counter = 400000
```

Every time. Not "usually 400000." The `Mutex` serializes the increments; no increment can be lost.

**Solution / what changed, line by line:**
- `Arc::new(Mutex::new(0))` — the `0` now lives *inside* a `Mutex` (so access is serialized), *inside* an `Arc` (so multiple threads can safely own it).
- `Arc::clone(&counter)` inside the loop — gives each thread its own owning handle to the *same* counter; the reference count goes up.
- `move ||` — hands that handle's ownership into the thread (required because the thread may outlive `main`'s local scope; this is exactly what `error[E0373]` was complaining about).
- `counter.lock().unwrap()` then `*guard += 1` — the *only* way to reach the integer is through the guard, and the guard releases the lock when it drops.

**What it reinforces:** The **same bug shape** — shared mutable state across threads with no lock — is a *silent runtime race in Python* and a *compile error in Rust*. Rust moved the check from "hope KCSAN sees it at runtime" to "the program won't build." That shift, from runtime detection to compile-time prevention, is the reason Rust is the transplant target.

---

### Exercise (b) — A `Mutex<i32>` counter across N threads (the correct total)

**Goal:** Cement the `Arc<Mutex<T>>` pattern by parameterizing the thread count, and directly contrast with Lesson 2's Python race — which lost increments — by getting the exact total every time.

**Complete code.** Save as `counter.rs`:

```rust
use std::sync::{Arc, Mutex};
use std::thread;

fn main() {
    const N_THREADS: usize = 8;
    const PER_THREAD: usize = 50_000;

    let counter = Arc::new(Mutex::new(0i64));
    let mut handles = vec![];

    for _ in 0..N_THREADS {
        let counter = Arc::clone(&counter);
        handles.push(thread::spawn(move || {
            for _ in 0..PER_THREAD {
                *counter.lock().unwrap() += 1;
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    let total = *counter.lock().unwrap();
    let expected = (N_THREADS * PER_THREAD) as i64;
    println!("total    = {}", total);
    println!("expected = {}", expected);
    println!("match    = {}", total == expected);
}
```

**Task:** Predict the total. Run `rustc counter.rs && ./counter`. Then — the important part — run it **five more times**. Does the total ever change?

**Expected result:**

```
total    = 400000
expected = 400000
match    = true
```

Identical on every run. Compare this to Lesson 2, where re-running the Python version gave a *different* wrong number each time (350k, 280k, 410k... whatever the race happened to lose that run).

**Solution / discussion:** There's nothing to fix — this one is meant to work. The learning is in the *contrast*: `*counter.lock().unwrap() += 1` looks like one line, but it's "lock, get guard, increment through guard, drop guard (unlock)" — atomic with respect to other threads because the lock serializes it. Nondeterminism (which thread goes first) is fine; *lost updates* are gone.

**What it reinforces:** A lock isn't about making threads deterministic in *order* — it's about making each read-modify-write *indivisible*. The Python version's `counter += 1` was three separate steps other threads could interleave into; the Rust `Mutex` makes the whole thing one serialized unit. Same fix concept as Lesson 2's `with lock:`, but here the data *cannot be reached* except under the lock.

---

### Exercise (c) — Ownership: use-after-move is the language preventing use-after-free

**Goal:** Feel move semantics directly. Move a value into a thread, then try to use it back in `main` — and watch the compiler reject the use. Understand that this rejection is the *same mechanism* that prevents use-after-free.

**Before (won't compile).** Save as `move.rs`:

```rust
use std::thread;

fn main() {
    let data = vec![1, 2, 3, 4, 5];        // `data` owns this Vec

    let handle = thread::spawn(move || {   // `move` gives ownership of `data` to the thread
        let sum: i32 = data.iter().sum();
        println!("thread computed sum = {}", sum);
    });

    // Back in main, try to use `data` again:
    println!("main still sees data = {:?}", data);  // <-- ERROR: data was moved

    handle.join().unwrap();
}
```

**Task:** Predict what happens. Run `rustc move.rs`.

**Expected compiler output** (abridged):

```
error[E0382]: borrow of moved value: `data`
  --> move.rs:13:47
   |
 4 |     let data = vec![1, 2, 3, 4, 5];
   |         ---- move occurs because `data` has type `Vec<i32>`,
   |              which does not implement the `Copy` trait
 6 |     let handle = thread::spawn(move || {
   |                                ------- value moved into closure here
...
13 |     println!("main still sees data = {:?}", data);
   |                                             ^^^^ value borrowed here after move
```

Again: **no binary.** The compiler is saying: you *gave away* ownership of `data` to the thread (via `move`), so `main` no longer owns it and may not use it. The `Vec`'s heap buffer now belongs to the thread; if `main` could still read it, and the thread freed it, that would be a **use-after-free**. The compiler forbids the *use* to make the *free* safe.

**After (compiles).** One correct fix — if `main` needs its own copy, `clone` before moving. Save as `move_fixed.rs`:

```rust
use std::thread;

fn main() {
    let data = vec![1, 2, 3, 4, 5];
    let data_for_thread = data.clone();     // give the thread its OWN copy

    let handle = thread::spawn(move || {    // move the copy into the thread
        let sum: i32 = data_for_thread.iter().sum();
        println!("thread computed sum = {}", sum);
    });

    println!("main still sees data = {:?}", data);  // `data` is still main's — fine now

    handle.join().unwrap();
}
```

**Task:** Run `rustc move_fixed.rs && ./move_fixed`.

**Expected result** (the two lines may interleave — that's normal):

```
main still sees data = [1, 2, 3, 4, 5]
thread computed sum = 15
```

**Solution / discussion:** The fix wasn't "trick the compiler" — it was to *decide who owns what*. The thread got its own `data_for_thread`; `main` kept `data`. Two owners, two independent frees, no conflict. (If instead you wanted them to *share* one `Vec`, you'd reach for `Arc<Vec<i32>>` — shared ownership — and if they both mutated it, `Arc<Mutex<Vec<i32>>>`, exactly as in exercise (a).)

**What it reinforces:** "Use-after-move" is a *compile-time* error, and it is the language enforcing the same invariant that prevents **use-after-free** — the exact Module 2 bug that lost reference-count increments cause. In C, using a pointer after its memory is freed is undefined behavior you find at 3am. In Rust, using a value after you've given away ownership is `error[E0382]` you find in the first second of compiling.

---

### Exercise (d) — (stretch) Many readers, one writer with `RwLock` + scoped threads

**Goal:** Meet two more R4L-adjacent tools: `RwLock<T>` (a lock that allows *many simultaneous readers* OR *one writer* — the shape RCU optimizes further) and *scoped threads* (`thread::scope`, which lets threads *borrow* stack data safely without `move`/`Arc`, because the scope guarantees they finish first).

**Complete code.** Save as `rwlock.rs`:

```rust
use std::sync::RwLock;
use std::thread;

fn main() {
    let config = RwLock::new(vec![10, 20, 30]);   // shared config, mostly read

    thread::scope(|s| {
        // Three reader threads — they can all hold the read lock AT THE SAME TIME.
        for id in 0..3 {
            s.spawn(|| {
                let r = config.read().unwrap();     // shared read guard
                println!("reader {id} sees {:?}", *r);
            });
        }

        // One writer thread — needs EXCLUSIVE access; waits for readers to finish.
        s.spawn(|| {
            let mut w = config.write().unwrap();    // exclusive write guard
            w.push(40);
            println!("writer appended 40");
        });
    }); // scope ends: all spawned threads are guaranteed joined here

    println!("final config = {:?}", *config.read().unwrap());
}
```

**Task:** Predict two things: (1) will this compile even though the threads *borrow* `config` without `Arc` or `move`? (2) what's the final config? Run `rustc rwlock.rs && ./rwlock`. Run it a few times and watch the interleaving of the print lines.

**Expected result** (ordering of the reader/writer lines varies run to run):

```
reader 0 sees [10, 20, 30]
reader 1 sees [10, 20, 30]
reader 2 sees [10, 20, 30]
writer appended 40
final config = [10, 20, 30, 40]
```

The reader lines may appear in any order and may show `[10, 20, 30]` or `[10, 20, 30, 40]` depending on whether they ran before or after the writer — but a reader *never* sees a half-updated vector, because `read()` and `write()` are mutually exclusive.

**Solution / discussion:** Two new ideas. (1) `RwLock` gives a `read()` guard (many at once) or a `write()` guard (exclusive) — it encodes "many readers OR one writer" as a type. This is the manual cousin of RCU, which lets readers proceed with *zero* locking; you'll meet `Rcu<T>` later. (2) `thread::scope` compiled *without* `Arc`/`move` because it *guarantees every spawned thread finishes before the scope block ends* — so the borrowed `config` is provably still alive for the whole thread lifetime. The compiler accepts a plain borrow because the scope makes it sound. (Contrast exercise (a), where `thread::spawn` could outlive `main`, forcing `Arc` + `move`.)

**What it reinforces:** Rust has a *vocabulary of lock types*, each encoding a different concurrency rule as a type: `Mutex` (one at a time), `RwLock` (many readers or one writer), and — in R4L — `SpinLock`, `Rcu`, and more. Lockstep's whole job is picking the *right one* for each C region. And `thread::scope` shows that the borrow checker's strictness relaxes exactly when it can *prove* safety another way — the rule is always "the compiler must be convinced," never "the compiler is arbitrary."

---

## Mastery check

Answer these three in your own words before moving on:

1. **The Lesson-2 race — four threads incrementing a shared counter with no lock — ran silently wrong in Python but does something different in Rust. What, and why?** (Hint: what does the borrow checker refuse, and how is "no two mutable borrows at once" the same as "no data race"?)

2. **Explain how Rust frees memory without a garbage collector, and how `Arc` extends that to shared ownership.** (Hint: single owner + scope for `Box`; atomic count + free-at-zero for `Arc`. Tie `Arc` back to the Module 2 reference-count bug.)

3. **Why is `SpinLock<T>` (data inside the lock) a *compiler-checked rule* rather than a *convention*, and why does that make Rust the right transplant target for Lockstep?** (Hint: in C you *can* touch the data without the lock; in Rust you can't even *name* it without locking. Connect "caught at compile time" vs. "caught at runtime by KCSAN.")

**Passing criterion:** You can explain each without rereading the lesson. Hedging ("I think...") is fine. If you can reproduce the `error[E0373]` / `error[E0382]` from memory and say what each means, you've got it.

---

## Connects to Lockstep

`SpinLock<T>` is not a teaching toy — it's a **real Rust-for-Linux abstraction**, shipped upstream, used in actual kernel Rust code today. It's the kernel-flavored version of the `Mutex<T>` you just exercised: data lives inside the lock, `.lock()` hands you a guard, the guard's drop releases the lock, and it integrates with lockdep (Module 4). Everything you did with `Arc<Mutex<T>>` on your laptop is the same shape Lockstep emits, targeting `SpinLock<T>` in the kernel.

Here's the through-line for the whole project:

- **Lockstep transplants C critical sections INTO these types.** A C region — "grab `q->lock`, touch `q->items`, release" — becomes a Rust `SpinLock<Items>` where `items` lives *inside* the lock. The convention that nobody enforced in C becomes a rule the compiler checks. That transplant is the core operation of M2 and M3.
- **The hard part (Module 6) is deciding what goes inside the `<T>`** — i.e. *which fields* the lock actually protects. That's the lock→data inference problem, confirmed against lockdep. Exercise (a)'s `Mutex<i32>` had an obvious `T`; a real kernel struct does not.
- **Rust catching races at compile time is *why* the target is Rust.** In Module 4 you saw KCSAN catch races at *runtime*, under stress, probabilistically — "we didn't observe a race this run" is the best it can say. In this lesson you saw Rust reject the same race shape at *compile* time, deterministically — "this cannot be built." Lockstep uses *both*: it transplants into Rust so the coordination rule becomes compiler-checked going forward, and it verifies the transplant with the runtime sanitizers so we know the *behavior* didn't change. Compile-time prevention for the future, runtime evidence for the transplant itself. That pairing is the whole safety argument, and this lesson is the compile-time half.
