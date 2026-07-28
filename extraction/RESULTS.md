# M1 results — concurrency IR extraction, cross-checked against runtime

M1 extracts the concurrency IR (design.md §3.1) for a small subsystem and proves
it against runtime observation. Two deliverables landed:

1. a **static extractor** (`extract.py`) — lock-bearing structs, critical
   sections, the `protects(lock, field)` map, and unprotected accesses;
2. the **proof** (`crosscheck.py`) — the extracted `protects` map matches what a
   race detector observes at runtime, on a ring buffer (design.md's own M1
   candidate).

## The proof: static map == runtime observation

`ringbuf.c` is one file read two ways. Statically, `extract.py` sees kernel-style
`spinlock_t` / `spin_lock(&r->lock)` names (shimmed to pthreads) and builds the
map. Dynamically, the same file compiles under `-fsanitize=thread` and a 6-thread
harness hammers it; ThreadSanitizer observes which fields actually race. TSan is
the userspace stand-in for KCSAN/lockdep — same "diff the findings" discipline as
the M0 gate.

```
STATIC  (extract.py):  protected = {buf, count, head}   unprotected = {name}
RUNTIME (TSan):        raced on   = {name}               reports = 2
CROSS-CHECK:
  ✓ no field the map called protected raced at runtime
  ✓ every raced field was flagged unprotected by the static map
M1 PROOF: PASS
```

`name` is written with no lock held on purpose (`ring_set_name`) — the static map
flags it, and it is exactly the field TSan races on. The lock-protected fields
(`head`, `count`, `buf`), hammered by four concurrent pushers, never race. The
map's two claims — *these are safe, that one isn't* — are both confirmed by the
kernel-style runtime the way M2+ transplants will be. `test_crosscheck.py` runs
this as a gated test (skips without a TSan-capable clang).

Negative-control shape is built into the check: if the map ever called a field
protected that in fact raced, `false_safe` trips and the proof FAILs — a vacuous
"everything protected" map cannot pass.

## Real-kernel sweep

`extract.py` was run on real subsystems (`sweep.py <files>`). On the dominant
`spin_lock(&obj->lock)` sibling-field idiom it produces a clean map:

| file | lock struct | sections | protects |
|------|-------------|----------|----------|
| `drivers/ptp/ptp_mock.c` | `mock_phc.lock` | 5 | cc, clock, info, tc |
| `drivers/gpio/gpio-rdc321x.c` | `rdc321x_gpio.lock` | 4 | data_reg, reg{1,2}_*_base, sb_pdev, … |
| `drivers/gpio/gpio-zevio.c` | `zevio_gpio.lock` | 5 | chip *(+ `regs` missed — see below)* |

## Honest limits (what the static half does not see)

- **Interprocedural accesses.** `gpio-zevio` touches its `regs` register base
  through a helper (`zevio_gpio_port_get(c, …)`) called *inside* the critical
  section; the field deref lives in the helper, not inline, so the static scan
  flags `regs` unprotected (a false positive). This is precisely what the runtime
  cross-check corrects — lockdep/KCSAN would observe `regs` accessed under the
  lock. Inlining one level of same-object helpers is the v2 fix.
- **Non-sibling locking.** `lib/atomic64.c` (a hashed array of `arch_spinlock_t`
  protecting the *caller's* `atomic64_t->counter`, via a bare `lock` pointer from
  `lock_addr(v)`, inside macro-generated functions) extracts nothing. It stacks
  three unhandled patterns at once: `arch_spinlock_t`/`arch_spin_lock`,
  pointer-not-`&obj->lock` acquisition, and the lock protecting an external
  object. A real but harder pattern; deferred, not pretended.

## In-kernel realization (deferred)

The self-contained TSan proof is the userspace analog. The in-kernel version —
load a real extracted driver under the M0 QEMU+lockdep harness and diff its
lock-class observations against the map — needs a QEMU-probeable driver
(`ptp_mock` is the likeliest candidate; the two GPIO drivers need their SoC/
southbridge hardware). Wiring that is M1.5 / folded into M2's transplant gate.

## M1 status: done

- Static extractor: lock structs, sections, protects map, unprotected accesses. ✅
- Proof: static map matches runtime race observation on the ring buffer. ✅
- Real-kernel sweep with the map produced and its limits documented honestly. ✅
