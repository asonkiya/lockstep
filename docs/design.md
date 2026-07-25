# Lockstep — verified region-level transplant of concurrent C into Rust

> Companion project to [CGIR / llm-semantic-compilers](https://github.com/asonkiya/llm-semantic-compilers).
> CGIR rewrites the pure-computational core of a codebase, function by function,
> verified. Lockstep is the machine for the part CGIR deliberately cannot reach:
> the concurrent, stateful, memory-disciplined code that is *most of an operating
> system by volume* — where a function's meaning is not its arithmetic but its
> locking, its ownership, and its memory ordering.

Status: **design**. This document is written the morning after CGIR's rung 4
landed — a Rust rewrite compiled to a freestanding object, linked into `vmlinux`,
and verified inside a booting Linux kernel by the kernel's own execution (with a
negative control that caught a vacuous harness before it could lie). That result
is the foundation Lockstep builds on and the reason to believe the hard version
is reachable.

---

## 1. Why a second project

CGIR's thesis, now well-evidenced: for a **pure, self-contained function**, the
model + verification + cost are all solved. On the SQLite amalgamation, 146/146
solves were behaviorally proven (differential + whole-program gate), 0 false
passes, ~$0.007/function. On the Linux kernel's crypto tree, header-aware lifting
made 57 functions standalone-compilable and one Rust rewrite now boots inside the
real kernel.

But every one of those wins shares a property: **the function's meaning survives
crossing the FFI boundary as scalars and bytes.** That is exactly what fails for
the rest of the kernel. Consider what a typical non-leaf kernel function *is*:

```c
static int foo(struct request *rq)
{
    spin_lock_irqsave(&rq->q->lock, flags);   // meaning: a critical section
    list_for_each_entry_rcu(e, &rq->list, n)  // meaning: an RCU read epoch
        if (e->tag == rq->tag) { ... }
    p = kmalloc(sz, GFP_ATOMIC);              // meaning: allocation discipline
    smp_store_release(&rq->state, READY);     // meaning: a release fence
    spin_unlock_irqrestore(&rq->q->lock, flags);
}
```

There is no scalar ABI here. The correctness of this code is a statement about
*what other CPUs can observe and when*, about *which lock protects which field*,
about *who frees `p` and after which barrier*. A function-boundary rewriter that
marshals arguments across `extern "C"` cannot carry those invariants — and a
differential that fuzzes inputs and compares outputs cannot even *see* them,
because the divergence a wrong barrier causes is a data race under contention,
not a wrong return value on a single thread.

CGIR's own purity/effects filter already excludes this code. That exclusion is
correct — and it is precisely Lockstep's input domain. The two projects partition
the kernel: CGIR takes everything provably pure; Lockstep takes the concurrent
residue, and the seam between them is the contract layer CGIR already produces.

**Lockstep is not "CGIR but bigger." It is a different machine** — different unit
of work (region, not function), different IR (concurrency-aware, not scalar-ABI),
different oracle (dynamic race/lock/fuzz stack, not byte-identical output),
different target (Rust-for-Linux safe abstractions, not `#[no_mangle] extern "C"`).

---

## 2. Thesis: the unit of rewrite is a *region*, not a function

A function boundary is the wrong seam for concurrent code because the invariants
span it: the lock is acquired in one function and the field it protects is touched
in three others. Lockstep's unit is a **semantic region** — a maximal span of code
governed by one coherent set of concurrency invariants. Concretely, the first-class
regions are:

- **Critical sections** — code between a `lock`/`unlock` pair on one lock, and the
  full set of fields that pairing protects (discovered, not declared).
- **RCU read/update epochs** — `rcu_read_lock()` … `rcu_read_unlock()` spans and
  their `synchronize_rcu()`/`call_rcu()` update counterparts.
- **Ownership regions** — an allocation and every path to its matching free, with
  the barrier discipline in between (the natural home of Rust ownership).
- **Publish/subscribe pairs** — a `smp_store_release` and the `smp_load_acquire`
  that observes it; the memory-ordering edges that make a value safely visible.

The rewrite transplants a *region* into a Rust abstraction that encodes the same
invariant in its type system — a `Mutex<T>` guard, an RCU-protected pointer, an
owned `Box`/`KBox` — rather than transliterating the C statement-for-statement.

---

## 3. Architecture

Four components. Two are new IR/synthesis work; one is an oracle-integration
effort; one is the seam that lets Lockstep stand on CGIR rather than rebuild it.

### 3.1 A concurrency-aware IR (the vocabulary)

CGIR fixed its `NodeKind`/`EdgeKind` from a data-model spec and refused ad-hoc
additions. Lockstep needs the analogous discipline, and its spec is not invented —
it is the **Linux Kernel Memory Model (LKMM)**. The IR's edges are LKMM relations:

- `holds(lock, region)` — a critical section and the lock that guards it.
- `protects(lock, field)` — inferred lock-to-data mapping (the heart of it; see §5).
- `reads-from` / `from-reads` / `coherence` — the LKMM ordering relations between
  memory accesses, lifted from `READ_ONCE`/`WRITE_ONCE`/`smp_*` annotations.
- `rcu-gp(reader, updater)` — grace-period edges between read epochs and updates.
- `owns(region, alloc)` — allocation-to-free ownership, with the free's barrier
  precondition.

Nodes are memory accesses, synchronization operations, and the regions of §2.
The IR is *extracted*, best-effort, from annotated C — the same philosophy as
CGIR's lifter: approximate structurally, let a downstream check be the judge. Here
the annotations are real and load-bearing (`READ_ONCE`, `rcu_dereference`,
`__rcu`, `__percpu`, `lockdep_assert_held`), which is a gift: the kernel already
tells us most of what we need, if we parse it.

### 3.2 Abstraction synthesis (the model's job)

This is where a capable model earns its place. Given a region and its extracted
invariants, synthesize the Rust-for-Linux abstraction that *encodes* the invariant,
not code that mimics the C:

| C idiom | Rust-for-Linux target |
|---|---|
| `spin_lock`/`unlock` around fields | `SpinLock<Fields>` + guard scope |
| `container_of` + `list_head` | `impl ListItem` intrusive list |
| `kmalloc`/`kfree` pairing | `KBox<T>` / `KVec<T>` ownership |
| `rcu_dereference` / `synchronize_rcu` | `Rcu<T>` protected pointer |
| refcount + `kref` | `Arc<T>` / `ARef<T>` |
| `__percpu` | `PerCpu<T>` |

The model does not invent these abstractions — Rust-for-Linux already ships them.
The task is *pattern → abstraction selection + a mechanical transplant*, which is
squarely in reach of the models that already do CGIR's function rewrites, given
the right context (the R4L abstraction catalog + the region's extracted invariant).

### 3.3 The dynamic oracle stack (the verifier)

Byte-identical output is the wrong equivalence for concurrent code — two correct
schedulers produce different interleavings. The gate becomes a **battery of
dynamic sanitizers run against stock-vs-transplant under adversarial load**, and a
transplant is accepted only if it is clean where stock is clean and no worse
anywhere:

- **KCSAN** — data-race detector. A wrong barrier/lock in the transplant shows up
  as a race KCSAN reports that stock did not. This is the *primary* oracle: it sees
  exactly the class of bug function-differential can't.
- **lockdep** — lock-ordering/deadlock validator. Catches an inverted or missing
  lock acquisition order in the transplant.
- **KUnit / kselftest** — the subsystem's own functional tests (the direct
  descendant of CGIR's whole-program gate, already proven on rung 4).
- **syzkaller** — coverage-guided fuzzing of the subsystem's syscall surface, as
  the adversarial load that makes the sanitizers fire. Differential coverage:
  stock and transplant must reach the same states without new sanitizer reports.

The gate runs in the same containerized kbuild + QEMU harness CGIR's rung 4 already
built (`benchmarks/kernel_gate/` there is the seed of it). The equivalence claim is
weaker than byte-identity by necessity and *stronger* in the dimension that matters:
"no new race, no new deadlock, same functional behavior, under fuzzing."

### 3.4 The seam with CGIR

Lockstep does not re-derive program structure. It consumes CGIR's outputs:

- **Call graph + effects/purity classification** — to find region boundaries and to
  know which callees are already pure (CGIR-rewritable) vs themselves concurrent.
- **The `ComponentSpec` contract** — the agent-facing description CGIR already emits.
- **The gate machinery** — the kbuild/QEMU container, `_patch_source`, the
  prebuilt-object link path. Rung 4 is literally Lockstep's M0 harness.

CGIR marks what it proved; Lockstep only fights the genuinely-concurrent residue.
Run together, the frontier moves in-out: CGIR eats the pure core, Lockstep eats the
region around it, and the boundary between "done" and "hard" is always explicit.

---

## 4. Milestone ladder

Each rung is gated by a *dynamic* proof, mirroring how CGIR gated each of its rungs
by a differential/whole-program result rather than a claim.

- **M0 — harness (mostly done in CGIR).** Boot a kernel under KCSAN + lockdep in
  the container; capture a clean baseline race/lock report under a KUnit load. This
  is rung 4's gate plus sanitizer configs. *Proof: stock is clean; the report is
  reproducible.*
- **M1 — IR extraction.** Extract the concurrency IR (§3.1) for one small subsystem
  (candidate: a self-contained driver or `lib/` data structure with clear locking —
  e.g. an idr/xarray user, or a simple ring buffer). *Proof: the extracted
  `protects` map matches `lockdep`'s runtime lock-class observations.*
- **M2 — single-region transplant, hand-checked.** Transplant one critical section
  to a `SpinLock<T>` guard by hand, through the pipeline's mechanical steps, and
  pass the M0 gate. *Proof: KCSAN/lockdep clean, KUnit green, negative control (a
  deliberately dropped lock) is REJECTED by KCSAN.*
- **M3 — model-synthesized transplant.** The model selects the abstraction and
  produces the region rewrite from the IR + R4L catalog; same gate. *Proof: a
  cheap-model transplant of a real region passes the full battery; a wrong one is
  caught.*
- **M4 — subsystem sweep.** Drive M3 across every region of one subsystem,
  dependency-ordered, the way CGIR swept SQLite. *Proof: N regions transplanted, a
  subsystem-level kselftest + syzkaller run clean vs stock.*
- **M5 — upstreamable output.** Emit transplants as Rust-for-Linux-shaped patches
  against a real subsystem, with the sanitizer evidence attached. *Proof: a patch
  that a human R4L maintainer would review — the honest end state.*

The negative control is a first-class citizen at every rung, because CGIR's rung 4
taught the lesson the hard way: **a gate that cannot reject is worse than no gate,
and only the wrong candidate proves the right one meant something.**

---

## 5. The hard problems, stated honestly

Not hand-waved. These are the parts that make this a multi-quarter research
project, not a weekend port:

1. **Lock-to-data inference (`protects`).** Which lock guards which field is
   *convention*, not declaration, in C. `lockdep` observes it dynamically (lock
   classes vs accesses); static inference is partial. The realistic answer is
   hybrid: static candidate edges, confirmed/refuted by a `lockdep` run — which is
   why M1's proof is "matches lockdep," not "provably complete."
2. **Region boundaries are not always well-nested.** `spin_lock` in one function,
   `unlock` in another; conditional acquisition; `goto`-unlocked error paths. Some
   regions won't be transplantable and must be *detected and skipped with a reason*,
   exactly as CGIR skips what it can't lift.
3. **Memory-ordering equivalence is subtle.** Proving a Rust `Ordering::Release`
   matches a C `smp_store_release` in context is an LKMM statement. KCSAN under
   fuzzing is a *sound-ish* dynamic check (it finds real races) but not complete
   (absence of a report ≠ absence of a race). We claim what the oracle claims and
   no more — the same discipline as "the differential found no counterexample in N
   trials," scaled to a weaker-but-honest equivalence.
4. **The abstraction may not exist yet.** Some C patterns have no R4L equivalent
   today. Those regions are out of scope until the abstraction lands upstream —
   flagged, not forced.

---

## 6. What is proven vs. speculative

**Proven (in CGIR, this repo builds on it):** the kbuild+QEMU gate; that a Rust
object links into `vmlinux` and runs; that a cheap model produces correct pure
rewrites at ~$0.007 each with 0 false passes; that a negative control catches a
vacuous gate. Header-aware lifting reaching 57 kernel crypto functions.

**Plausible, unproven (Lockstep's burden):** that the concurrency IR is extractable
at useful fidelity; that abstraction synthesis is in cheap-model reach; that the
KCSAN+lockdep+syzkaller battery is a strong-enough oracle to trust a transplant.
Each is an M-rung with a dynamic proof attached, so we will *know*, not guess.

**Out of scope (honest ceiling):** whole-kernel automatic rewrite with no human in
the loop; anything where the correct equivalence is a full LKMM proof rather than a
dynamic check; subsystems whose invariants aren't expressible in today's R4L. The
end state is *maintainer-reviewable patches with sanitizer evidence*, not a magic
button — and that is the honest shape of "rewrite the kernel."

---

## 7. Relationship to Rust-for-Linux

Rust-for-Linux is the landing zone, not a competitor. R4L is humans hand-designing
safe abstractions and rewriting drivers on top of them. Lockstep is a machine that
*applies* those abstractions at scale to existing C, gated by the kernel's own
sanitizers, emitting patches in R4L's shape. If it works, it is a force multiplier
for R4L's mission; if a region is too subtle for the machine, it falls back to
exactly the human process R4L already runs. There is no version of this that
competes with the human effort — only one that feeds it.

---

*Written 2026-07-25. The claims about CGIR here are backed by its experiment log;
the claims about Lockstep are hypotheses with proofs attached to milestones. When
this doc and a measured result disagree, the result wins.*
