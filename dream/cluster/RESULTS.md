# Static-function cluster weaving — the third critical-path library

The weaver excises one function at a time: body → seam call to a Rust object. That
breaks the moment the function calls a **file-local `static` helper**. Weaving the
entry alone leaves the helper with no C caller, and `-Werror=unused-function` fails
the build. This is not hypothetical — Ring 2 hit it on `lib/math/gcd.c` (`gcd` calls
`static binary_gcd`) and had to record `gcd` as **`verified_not_woven`**: proven
equal to C, but un-weavable. Every non-trivial kernel function with a private helper
is blocked the same way.

The fix is to weave the whole **cluster** — the entry plus its transitive `static`
callees — as ONE Rust object:

- the **entry** becomes `#[no_mangle] pub extern "C"` (keeps the ABI symbol its
  callers link against);
- each **`static` helper** becomes a **private** Rust `fn` — no exported symbol (no
  link collision) and it *is* called by the entry (no dead-code warning);
- **all** cluster members are excised from the C: the entry's body forwards to the
  Rust symbol, and every `static` helper definition is **removed** — no orphan.

Verification stays at the **exported boundary**. The `static` helpers are unreachable
by name (private in Rust, gone from C), but a differential over the entry drives them
transitively — so proving the entry equals its C original proves the whole cluster.

## The engine (`cluster.py`)

Pure analysis + excision, no toolchain:

1. `functions(src)` — lists top-level definitions and whether each is `static`.
2. `static_cluster(src, entry)` — BFS over intra-TU callees, following **only**
   `static` functions defined in this TU. Non-static / out-of-TU callees (macros,
   header inlines, arch intrinsics) are *boundary* symbols, left alone.
3. `cluster_weave(...)` — entry → forwarding shell, every `static` cluster member →
   removed, Rust `extern` prototype inserted.
4. `naive_weave(...)` — the broken single-fn weave, kept to *prove* the orphan.

On the real `lib/math/gcd.c` it computes `cluster(gcd) = [gcd, binary_gcd]`.

## Result (`gate.sh`, on the real kernel source)

```
== cluster analysis (from real gcd.c) ==
  cluster(gcd) = ['gcd', 'binary_gcd']   [ENTRY gcd] [static binary_gcd]

(1) ORPHAN  : naive single-fn weave -> error: unused function 'binary_gcd'
              [-Werror,-Wunused-function]                        ✓ (problem shown)
(2) CLUSTER : whole-cluster weave compiles clean, same -Werror   ✓ (no orphan)
(3) DIFF    : cluster-woven gcd (-> Rust cgir_gcd) vs stock gcd_ref
              CLUSTER cases=2000400 bad=0 verdict=MATCH          ✓ (equal to C)
(4) CONTROL : bug planted INSIDE private binary_gcd (dropped the
              `<< __ffs(r)` restoration) -> bad=94294 DIVERGE,
              first gcd(6,6) got=3 exp=6                          ✓ (helper reached)

CLUSTER WEAVING GATE: PASS
```

Leg (1) is load-bearing: without it the demo would be vacuous, so the gate *requires*
the naive weave to fail, for the right reason (the orphaned `binary_gcd` named in the
error). Leg (4) is the soundness check on the boundary argument: the bug is inside the
**private** helper — never called by name — and the entry-only differential still
catches it on 94k of 2M cases, first at `gcd(6,6)`. What's driven (the exported entry)
covers what's hidden (the helper).

## Scope + how it lands in-kernel

- The engine and gate are self-contained (real `gcd.c` with kernel includes swapped
  for a small userspace shim; the function *bodies* are the actual kernel source).
- In-kernel realization is a drop-in for the existing weaver: `weave.py` already
  excises bodies and boots freestanding Rust objects (Rings 0–9). Cluster weaving only
  changes *which* C is excised — the whole `static_cluster` set instead of one
  function — so `gcd`, previously `verified_not_woven`, is now **weavable**: entry to
  a `#[no_mangle]` seam, `binary_gcd` a private Rust fn, both gone from `gcd.c`.
- Conservative by construction: a helper whose definition isn't in the TU is a
  boundary symbol, not pulled in (the Rust `extern`-calls or reimplements it, as the
  leaf transplants already do). Recursive/mutually-recursive static clusters fall out
  of the BFS naturally (all members land in one object).
- What it does **not** do: reforming a `static` helper that is *also* called by other
  entries in the same TU into a shared Rust symbol — there the helper is a genuine
  boundary and wants its own transplant first (detectable: it has C callers outside
  the cluster). Flagged, not guessed.

## Status — third of three critical-path libraries, done

- Cluster analysis (intra-TU static-callee BFS) + dual weave (cluster / naive): built. ✅
- Orphan demonstrated then avoided on the real `gcd`/`binary_gcd` cluster. ✅
- Cluster differential-verified at the exported boundary, 2.0M cases bit-identical. ✅
- Bug inside the private helper caught by the entry-only oracle. ✅
- Resolves Ring 2's `gcd = verified_not_woven`: it is now weavable. ✅

With the recorder (drivers), the mirror library (Tier-B structs), and cluster weaving
(static helpers), the three engineering blockers the gap analysis named between here
and a booting majority-Rust kernel are each built and gated.
