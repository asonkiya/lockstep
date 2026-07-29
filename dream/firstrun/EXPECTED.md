# First official minimal rewrite — what we expect (pre-registered before the run)

Written *before* launching, so the morning `REPORT.md` is judged against a
prediction, not rationalized after the fact.

## What this run IS — and what it is NOT

**IS:** proof the autonomous machine runs end-to-end unattended, and the first
*banked* set of real kernel functions reimplemented in Rust and verified against
the C they'd replace — some boot-verified inside a booting arm64 kernel.

**IS NOT:** a kernel "running mainly in Rust." Be clear-eyed: a minimal vmlinux
has tens of thousands of functions. Tonight weaves in **single-digits to low-tens**
of Rust functions. That is a **Rust fraction well under 0.1%** — a verified
toehold in a kernel that is still >99.9% C. "Mainly Rust" is the *destination*
the cost model describes (~89% reachable, a strong-majority achievable), reached
by repeating the family / shared-library grind across ~40 subsystems + worker
provisioning — **weeks of grind, not one night.** Tonight is rung 1: the machine
works, and the first functions are real and verified.

## Expected REPORT.md shape (mock)

```
# First official minimal rewrite — overnight report
- runtime: ~30–120 min (cap 7h)          # finishes well inside the window
- Haiku spent: $1–5 of $7.50 cap          # cap should NOT bind
- verified functions: ~15–45

## gpio-family: 2  ({'template': 2})       # $0, deterministic, should always pass
  - gpio-mmio(bgpio-core) [template]
  - mxs-alias(set/clr@+4/+8) [template]

## scalar-leaf: ~12–40  ({'local': most, 'haiku': the tail})
  - <kernel fn> [local]  ...              # e.g. int_sqrt, gcd-family, kfifo/bitmap helpers

## phase 2 (boot-weave)
  {"attempted": true, "woven": ~10–30, "boot_verified": ~5–20, "dropped": <the rest>}
```

## Expected numbers (grounded in prior runs)

| metric | expected | basis |
|---|---|---|
| GPIO family verified | **2/2** | template synth, already proven ($0) |
| scalar leaves verified (host) | **~12–40** of 80 attempted | wide run verified ~10–13 genuinely-pure of ~72; local+Haiku ladder + hostdiff |
| Phase-2 in-kernel boot-verified | **~5–20** | `build_boot` auto-drops symbols not linked in the minimal config — "config coverage gates the testable set" |
| Haiku spend | **~$1–5** | measured $0.0014–0.006/fn; most solved by $0 local/template |
| false passes | **0** | the gates (hostdiff / trace oracle) have 0 across every prior run |
| kernel boots | **yes** | Image builds + QEMU reaches init with the Rust set woven in |

## Success criteria

- **PASS:** kernel builds + boots, ≥1 function boot-verified in-kernel, a boot-free
  verified set banked, **0 false passes**, spend < cap.
- **PARTIAL (still a real result):** Phase 1 banks a verified set but Phase 2's boot
  drops everything / fails to build — the host-verified C→Rust functions still count.
- **FAIL / investigate:** ~0 verified, or cap hit with almost nothing solved, or a
  harness crash.

## Red flags to check in the morning

- **Spend near $7.50 but few solves** → local Qwen wasn't running (everything went
  to Haiku). Check `ollama ps`; re-run with ollama up for far more per dollar.
- **0 scalar leaves verified** → harvest/gate/KSRC problem (did KSRC persist?).
- **Phase 2 "dropped": nearly all** → expected-ish for a minimal config (few symbols
  linked); the boot-free Phase-1 count is the real yield. Not a failure.
- **any `bad=N (N>0)` that still reported MATCH** → would be a false pass; must be 0
  (it will be — pre-registered as the one non-negotiable).

## The honest gap to "mainly Rust", and what closes it

Tonight: ~tens of functions, <0.1% of vmlinux. To climb toward a strong Rust
majority is the *measured* grind — not more nights of this exact run, but:
1. repeat the GPIO family process for the shared cores (regmap = 1,815 files,
   spi-bitbang, uart, mmc, i2c-algo) — each a template + trace-oracle family;
2. the struct-mirror families for the Tier-B middle;
3. broader configs so more symbols link (config coverage was the ceiling);
4. worker provisioning for wall-clock.

Each is bounded and mechanism-proven. Tonight proves the loop that all of that
repeats. Judge it as rung 1 of that ladder, not the summit.
