# Ring 1 — the ratchet grows to a second subsystem

Ring 0 proved the ratchet carries one driver. Ring 1 proves it *accumulates*: a
second, unrelated subsystem enters the manifest, the metric climbs, the prior
entry stays green, and two independent Rust objects coexist in one booting kernel.

## The new transplant: `lib/math/int_sqrt`

A pure integer-sqrt leaf — Tier A (the research's strongly-verifiable class: it
has a real KUnit oracle, `int_sqrt_kunit`). Synthesized freestanding by Haiku
(`synth_leaf.py`), its only "call" (`__fls`) reimplemented inline as
`leading_zeros` — no externs, fully self-contained.

**Differential gate** (`ring1/gate.sh`): the Rust candidate and the C original
linked into one kernel, driven over **20,197 inputs** — dense 0..20000, every
power-of-two boundary (±1), and large values through `ULONG_MAX`:

```
ISQRT_PROBE: n=20197 mismatches=0 firstbad=-1 verdict=DIFF_PASS
```

Zero mismatches. Bit-identical to the C across the entire tested range.

## The accumulation (`add_int_sqrt.py` + `weave.py gate`)

```
manifest now: 2 sources, 5 functions status:rust
wove drivers/ptp/ptp_mock.c: 4 bodies -> Rust seam calls
wove lib/math/int_sqrt.c:    1 body   -> Rust seam call
✓ compiled + wired drivers/ptp/ptp_mock_regions.o (Rust)
✓ compiled + wired lib/math/int_sqrt_rust.o (Rust)
✓ Image built; boot-digest smp_up=True boot_complete=True early_panic=False

=== ratchet dashboard ===
  sources woven     : 2
  functions -> Rust : 5/11  (45.5%)
  strongly gated    : 5/5 (differential)
  drivers/ptp/ptp_mock.c: adjfine, adjtime, settime64, gettime64 [differential:PASS]
  lib/math/int_sqrt.c:    int_sqrt                               [differential:PASS]
```

Ring 0 was 4/9; Ring 1 is 5/11 across two subsystems. The ptp entry was untouched
by the int_sqrt addition — **non-regression**, the property that makes it a
ratchet.

## The wall this exposed and solved — the panic-handler collision

At two real Rust objects in one vmlinux, we hit exactly what the linking research
predicted from a standalone test: both freestanding objects export a global
`rust_begin_unwind` (`#[panic_handler]`), and the vmlinux link fails with
`multiple definition`. The fix the research had already verified —
`aarch64-linux-gnu-objcopy --wildcard --localize-symbol '*rust_begin_unwind*'` on
all-but-one object — is now baked into `weave.py` and `ring1/gate.sh`. Predicted,
hit at N=2, solved. This is the mechanism that lets the ratchet scale past one
Rust object, which every prior milestone had.

## Status

- A second, unrelated subsystem transplanted and accumulated. ✅
- Metric climbs (4/9 → 5/11), prior entries stay green (non-regression). ✅
- Two Rust objects coexist in one booting kernel (panic-collision solved). ✅
- Every Rust function differentially proven equal to its C. ✅

Ring 2 is just more rows: a batch of lib/ leaves (where the oracle is real),
then the differential harness pointed at a driver with recorded I/O to start
converting the 73% weakly-gated mass. The shape does not change — only the count.
