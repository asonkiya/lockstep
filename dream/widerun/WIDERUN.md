# The wide run — first production-scale verification pass

The sweep answered "how close" on a 37-function harvest. The wide run is the
thing itself, larger and autonomous: harvest scalar-exported leaves **tree-wide**,
synthesize them all in parallel, and verify them against the kernel's own symbols
in batched boots — with an auto-drop loop so a symbol that isn't linked in this
config can't sink the batch.

## Scope

Harvested **72** pure-scalar exported leaves across `lib`, `kernel`, `mm`,
`crypto`, `block`, `net/core`, `fs`, `drivers/{gpio,clk,pci,base}`, `sound/core`
(side-effect/div/lock names filtered up front). Parallel-synthesized by Haiku,
verified in batches of 40 in one boot each, differential vs the live kernel
symbol over 1,500–2,300 inputs per function.

## Result

```
harvested 72 | compiled 71 ($0.10) | linkable+booted 16 | return-diff PASS 10 | rejected 6
elapsed 7.4 min
```

Two findings matter more than the headline count.

### Finding 1 — config coverage gates the testable surface

Of 71 compiled candidates, only ~16 were **linkable and bootable in this minimal
config**; the rest referenced kernel symbols not built here and were auto-dropped.
Tree-wide harvest over a *minimal* kernel yields mostly symbols that aren't
compiled in — the effective testable set in any one config is far smaller than the
harvest. Scaling the *verified* count means building a larger config (more symbols
linked), not just harvesting more. (The drop bookkeeping under-counts; the console
shows the true set.)

### Finding 2 — return-value differential OVER-CREDITS side-effectful functions

Of the 10 that passed, ~7 are genuinely pure and are real verifications across new
subsystems: `__sw_hweight8/16/32`, `__kfifo_max_r`, `pci_rebar_bytes_to_size`
(drivers/pci), `__node_distance` / `phys_to_target_node` (mm), `nfs_check_flags`
(fs — pure O_APPEND/O_DIRECT flag check).

But ~2–3 are **return-equivalent, not behavior-equivalent**. `__refrigerator(bool)`
is the process freezer — the model dropped the freeze side effect and returned
`was_frozen=false`; the scalar differential compares only the return, so it passed
a transplant that does not do what the original does. `probe_irq_mask` likewise —
the model noted it "cannot access global irq_desc state" and ported `mask & val`.

**This is the wide run's real lesson:** a return-value differential is sound for
*provably pure* functions and unsound for stateful ones — it can green-light a
reimplementation that reproduces the return while dropping the effect. This is
exactly the gap Ring 3's recorded-I/O / trace oracle exists to close. At scale the
pipeline must **route by purity**: pure leaves → scalar differential (cheap,
sound); anything touching state/effects → the trace oracle, never the scalar one.
Mislabeling a stateful function as a pure leaf is the one way a wrong transplant
slips through, and this run caught it by inspection.

### The rejects were honest

The 6 rejections (`neigh_rand_reach_time` uses PRNG, `numa_nearest_node` /
`poll_state_synchronize_rcu` / `pci_bus_find_emul_domain_nr` read live state,
`memory_group_register_static` registers, `nfs4_stat_to_errno` a table miss) are
all state/random/registration-dependent — correctly refused, no false pass among
the genuinely-comparable set.

## What the wide run establishes

- The machine runs at production shape autonomously: harvest → parallel synth →
  batched differential boots → tally, 71 functions for $0.10 in 7 minutes.
- **Two scaling truths, measured**: (1) config coverage bounds the testable set;
  (2) the oracle must be chosen by purity — scalar differential for pure leaves,
  trace oracle for anything stateful, or you over-credit.
- Genuinely-pure functions verify soundly and cheaply across every subsystem
  touched (lib, mm, fs, drivers/pci, kernel). That class is turnkey at scale.

## v2 — with the purity router (`purity.py`), the run is now SOUND

Finding 2 said the scalar differential over-credits stateful functions. `purity.py`
fixes it: before the gate, classify each function (conservative — anything not
provably pure is quarantined), route **pure → scalar differential**, **stateful →
the trace oracle**. Re-ran wide:

```
harvested 72 -> purity router: 13 PURE (scalar-gate), 59 quarantined (trace oracle)
pure+linkable booted 11 -> VERIFIED 10, rejected 1, dropped 2
verified: __sw_hweight8/16/32, __kfifo_max_r, int_pow, int_sqrt, int_sqrt64,
          nfs_check_flags, pci_rebar_bytes_to_size, xas_try_split_min_order
```

The three over-credited functions from v1 — `__refrigerator`, `probe_irq_mask`,
`__node_distance` — are now **correctly quarantined** (the router flags `pr_debug`,
IRQ-desc state, `numa_` respectively), so they never reach the scalar gate. Every
one of the 10 passes is now a genuinely-pure, **behavior-equivalent** verification
across lib / mm / fs / drivers-pci / kfifo / xarray. The 1 reject
(`cper_severity_to_aer`, an ACPI severity table) is a retryable synth miss; the 2
drops are config-unlinkable. The router is intentionally conservative — it even
quarantines `gcd`/`lcm` (they read a static-branch global) — because for a
verification gate, **soundness beats completeness**: better to send a pure function
to the (also-sound) trace oracle than to let one stateful function through the
scalar one.

**Net:** the wide pipeline now has zero unsound passes by construction, not by
luck. Purity routing was the one missing piece of plumbing between "runs at scale"
and "every pass means what it says." It's built.

## v3 — retrying the quarantined 59 with the observation oracle

Can the quarantined functions be verified by the *in-kernel* differential (the C
runs against real state; the Rust is a pure reimplementation)? Soundly, only if a
function differs from pure **only by reading state** — no side effect, and no
opaque callee that could hide one. For such a read-only function, return-
equivalence to the C *is* behavior-equivalence in this config (nothing to miss).

`retry_trace.py` split the 59 by that sound criterion:

```
quarantined 59  =  1 read-only-recoverable  +  58 effectful / opaque-callee
read-only booted 1 -> RECOVERED (config-sound) 1, still state-dependent 0
recovered: __node_distance   (single-node config -> constant distance; the pure
           reimpl matches the C here — a valid per-config transplant)
58 effectful -> need a per-function effect trace (NOT auto-run): __refrigerator,
   irq_* , numa_* , memory_group_register_* , vm_munmap, remove_cpu, gcd/lcm
   (call opaque static helpers), zstd_* , ...
```

**This is the honest boundary of automation, drawn from the data:** the return/
observation differential soundly recovers essentially *nothing* from the
quarantine (1 of 59) — because almost every stateful function either has a real
effect (`__refrigerator` freezes, `vm_munmap` unmaps, the `irq_*` family touches
descriptors) or calls a helper the checker must treat as opaque. Those genuinely
need a **recorded effect trace**, per function or per state-source. Ring 3/4
proved the *MMIO* subclass of that is uniformly automatable (record `readl`/
`writel`); arbitrary global/per-cpu/RCU state is not, and is honest per-function
work — or it stays C.

So the four-way verdict, now fully measured end to end:
- **pure leaves** → scalar differential, turnkey, sound, cents (this is the bulk
  of the reachable win);
- **read-only-otherwise-pure** → in-kernel differential, config-sound, a thin
  sliver (1/59 here);
- **effectful** → recorded effect trace; MMIO subclass automatable (Ring 3/4),
  the rest per-function;
- **entangled (Tier D, ~11%)** → C-forever.

The quarantine was right, the router is sound, and the line between "automatable"
and "hand/effect-trace work" is now empirical, not guessed.
