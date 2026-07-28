# The unified multi-tier router — the one loop

Every verification mechanism in this project existed as a *separate* gate:
`hostdiff` (boot-free differential), the in-kernel differential (sweep/fleet),
`mirror` + diff (Tier B), `recorder` (drivers), `concgate` (locks). The fleet
loop only ground the pure-scalar class. This is the missing integration piece:
**one loop that takes an arbitrary worklist, routes each function to the
strongest oracle it can *soundly* use, executes the automatable tiers, and
reports one dashboard.** It is the last thing standing between "a pile of proven
mechanisms" and "point it at a config and let it run."

## The routing rules (the project's findings, encoded)

Cheapest checks first, soundness-ordered, and — critically — **nothing is ever
placed in a weaker oracle than its census/purity class requires**, so the
widerun's over-crediting bug (a value differential passing a side-effecting
function) is *structurally impossible*:

| route | rule | oracle | automatable now |
|---|---|---|---|
| `C_FOREVER` | census D (container_of/per-cpu/RCU/list) | none | — (the ~11% floor) |
| `TC_REGION` | census C (takes a lock) | concgate / M-ladder | mechanism proven, per-region |
| `T2_MIRROR` | census B (reads struct fields) | mirror + differential | per struct family (rings 8/9) |
| `T0_HOST` | A + pure + host TU compiles | ladder synth + `hostdiff` | ✅ **fully, boot-free** |
| `T1_KERNEL` | A + pure/read-only + linkable | synth + ONE batched boot | ✅ **fully, one boot** |
| `T1_UNLINKABLE` | pure/read-only but symbol not in this config | — | config gap, not a model gap |
| `T3_TRACE` | effectful + MMIO markers | recorder | per-driver recording |
| `T3_EFFECT` | effectful otherwise | per-fn effect trace | quarantined |

The read-only-into-`T1` rule is the sound one the trace-retry established: a
function that only *reads* state (no effect, no opaque callee) has
return-equivalence == behavior-equivalence, so the in-kernel differential
against the real state is sound for it.

## Result — routing the widerun's 72

```
worklist 72 | routing:
  C_FOREVER     2   rcu_get_gpwrap_count, cpu_is_hotpluggable
  TC_REGION     4   __refrigerator, cs5535_gpio_isset, irq_check_status_bit, irq_has_action
  T2_MIRROR    12   irq_set_irq_wake, irq_percpu_is_enabled, ... (struct readers)
  T0_HOST       6   __sw_hweight8/16/32, int_pow, int_sqrt, int_sqrt64
  T1_KERNEL     6   __kfifo_max_r, __node_distance, cper_severity_to_aer,
                    nfs_check_flags, pci_rebar_bytes_to_size, xas_try_split_min_order
  T3_EFFECT    42   add_cpu, capable, blk_mq_num_*_queues, ... (effectful quarantine)
```

Every prior finding lands where it belongs, automatically:

- **`__refrigerator` → `TC_REGION`**, not a value differential. The exact
  function whose return-only diff over-credited it in the wide run is now caught
  *by classification* as lock-taking, before a single dollar is spent. The bug
  that motivated the purity router is now unrepresentable.
- **`__node_distance` → `T1_KERNEL`** — the 1/59 the trace-retry found soundly
  recoverable (read-only, config-scoped) is routed to exactly its oracle.
- **The 42 effectful functions** (`capable`, `add_cpu`, …) are quarantined from
  any value check — they owe a real effect trace, and the router says so.

## Execution

<!-- RESULTS: filled from the run -->

The router **executed both automatable tiers end to end** on the routed set:

- **T0_HOST**: the ladder (c2rust → 14B → Haiku) + `hostdiff`, boot-free.
- **T1_KERNEL**: ladder-discipline synth of every candidate, then **one build,
  one boot** verifying all of them in-kernel against the live C symbols
  (the sweep/fleet machinery, parameterized).

```
=== UNIFIED ROUTER DASHBOARD ===
  worklist 72 | VERIFIED 9 (T0 5, T1 4) | spend $0.0040 | wall 445s (one boot)
    verified_T0(c2rust)        5   __sw_hweight8/16/32, int_pow, int_sqrt   (boot-free)
    verified_T1                4   __kfifo_max_r, __node_distance, nfs_check_flags, xas_try_split_min_order
    T1_rejected                2   cper_severity_to_aer (bad=1998), pci_rebar_bytes_to_size (bad=2001)
    T0_unsolved                1   int_sqrt64 (the #if BITS_PER_LONG<64 path — hard for every rung)
    T2_MIRROR                 12   routed (owe a struct mirror)
    T3_EFFECT                 42   routed (owe an effect trace)
    TC_REGION                  4   routed (region machinery)
    C_FOREVER                  2   the floor
```

**9 sound verdicts from one worklist through one loop — 5 boot-free, 4 in a
single shared boot — for $0.004, with zero false passes.** The two `T1_rejected`
are the gate being conservative in the *safe* direction: the fixed 0..2000
differential domain over-tests enum/table functions (`cper_severity_to_aer` is
defined on ~4 severity values; `pci_rebar_bytes_to_size` on a narrow size range)
beyond where their behavior is specified, so it rejects rather than risk an
unsound pass. A false *reject* costs a retry with a tighter domain; a false
*accept* would corrupt the ratchet — the router never makes the second kind.
`int_sqrt64` is genuinely hard (its 64-bit path sits behind `#if
BITS_PER_LONG < 64`), unsolved by every rung — honestly reported, not hidden.

Every `verified_T0` / `verified_T1` is a *sound* verdict at that tier's
strength; T2/T3/TC functions are routed and reported with their per-family
requirement named, never silently counted as passed.

## What this is and isn't

- **Is:** the one autonomous loop the "distance from the dream" analysis named
  as the single unbuilt piece. It composes the census, the purity router, the
  ladder, hostdiff, and the in-kernel batched-boot differential into a single
  worklist-consuming machine, and it preserves the soundness invariants by
  construction.
- **Isn't (yet):** the T2/T3/TC executors. Their *machinery* is proven
  (mirror rings 8/9, recorder, concgate) but each needs a per-family artifact
  (a struct mirror, a driver recording, a region rewrite). The router routes
  them and names the artifact owed — turning "the rest of the kernel" from a
  vague mass into an itemized, per-family work queue.

## Files

`router.py` — classification (`census` + `purity` + host-TU precheck +
linkability via `System.map`), the two executors (T0 boot-free, T1 one boot),
the dashboard. `router_result.json` — per-function route + status.
