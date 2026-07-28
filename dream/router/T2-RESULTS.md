# The T2 executor — what census-B actually needs

The router routes anything census classifies as **B** (a `->`/`.` deref) to
`T2_MIRROR` — "owes a struct mirror." The T2 executor's job is to *act* on that
route. Running it produced a finding worth more than a batch of closes: **the
coarse "48% of the kernel is Tier-B, needs mirrors" headline is an
over-estimate.** Census-B by a deref-regex is heterogeneous, and most of it
doesn't need a mirror at all.

## What it does

For each census-B function it re-examines the *actual* dependency and sends it to
its true tier:

- **PARAM_STRUCT** — takes `struct X *p` and derefs it: the genuine mirror case.
  Auto-run the generator on `X` → emit the mirror (`T2_mirror_ready`) or itemize
  the refusal (`T2_blocked`, with reason).
- **PURE_GLOBAL** — scalar signature, reads state but no effect, no opaque call:
  not a mirror case — a read-only-otherwise-pure function the in-kernel
  differential already handles. **Promote to T1.**
- **EFFECTFUL** — writes/waits on global state: reroute to `T3_EFFECT` (a value
  differential would over-credit it — the `__refrigerator` lesson).
- **LOCK** — takes a lock: reroute to `TC_REGION`.

## Result — refining the router's 12 T2 routees

```
census-B routees 12 | refined:
    EFFECTFUL / T3_EFFECT   10   (irq_set_irq_wake, probe_irq_off, synchronize_hardirq,
                                  memory_add_physaddr_to_nid, poll_state_synchronize_rcu, ...)
    PURE_GLOBAL              2   snd_pcm_rate_to_rate_bit, snd_pcm_rate_bit_to_rate
    PARAM_STRUCT             0   ← none of the 12 actually reads a caller-passed struct
```

**Zero of the twelve are real mirror cases.** Every `->` that made census call
them B is a deref of a *global* — `irq_to_desc(irq)->field`, a static rate
table — not a caller-passed struct pointer. Ten are effectful global-state
functions (correctly quarantined to T3, exactly where a value differential must
not go); two are pure reads of a static table. The router's coarse B bucket was,
for this sample, **100% not-a-mirror-case** — and the executor catches that
before a single mirror or dollar is spent.

The two PURE_GLOBAL ones (`snd_pcm_rate_*`) promote cleanly to T1 but are **not
linked in this minimal config** (`CONFIG_SND` off) — the "config coverage gates
the testable set" finding again: verifiable in principle, absent from this
kernel. Closed here: 0 — bounded by the config, not the method.

## The auto-mirror engine (capability proof)

The mirror path is real; it just needs a real param-struct to fire on. Proven on
actual kernel structs:

```
struct clk_div_table  -> mirrorable  size=8,  2 fields
struct cyclecounter   -> mirrorable  size=24, 4 fields
struct timecounter    -> mirrorable  size=40, 5 fields
struct irq_desc       -> refused     config-dependent (#if) fields — layout not fixed
```

`clk_div_table` is the family Ring 9 verified 6/6 in one boot against exactly
this mirror — the executor now **auto-generates** that mirror instead of it
being hand-written. `irq_desc` (the struct the 10 effectful routees actually
touch) is correctly **refused**: 55 lines, nested structs, `#if` config fields —
not auto-mirrorable, honest hand-work. This required a generator fix: the struct
finder assumed `\n};`, but real kernel structs end `} ____cacheline_...;` /
`} __packed;` — now handled (mirror gate still green).

## The finding

**Tier-B is not one thing.** A deref-regex over-counts it. The real populations:

1. **caller-passed struct readers** — the true auto-mirror case; a distinct,
   likely *smaller* slice than 48%, tractable when the struct is flat
   (clk_div_table) and hand-work when entangled (irq_desc);
2. **global-state readers** — route to existing oracles: pure → T1, effectful →
   T3. No mirror needed.

So the T2 executor's highest-value output is **corrected routing**: it stops the
router from parking global-state functions in mirror-limbo, sends each to the
oracle it truly needs, and reserves the mirror machinery for the functions that
genuinely read a caller's struct. For this worklist that means 10 correctly
quarantined, 2 config-gated, 0 wasted on mirrors — and a proven engine ready for
the real param-struct population when the worklist reaches it.

## Files

`t2_executor.py` — the refiner (`refine()` sub-classifies; `auto_mirror()` runs
the generator on a real struct), the promotion-to-T1 path, the dashboard.
`t2_result.json` — per-function sub-route. Depends on `router.py` (T1 machinery),
`dream/mirror/` (the generator, finder now fixed), `dream/widerun/purity.py`.
Companion: `RESULTS.md` (the router), `dream/ratchet/RING9.md` (the mirror→diff
close this engine feeds).
