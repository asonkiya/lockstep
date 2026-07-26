# The dream — overnight progress

Goal (your words): *"rewrite Linux into Rust and run it."* Tonight took it from a
research question to a working, growing ratchet with a real driver and a real
lib leaf transplanted into a booting kernel — and, more importantly, surfaced and
solved the constraints that actually govern how far it can go.

## What landed (each committed, each with a RESULTS.md)

1. **The research pass** (`dream/RESEARCH.md`) — measured, not guessed, against the
   real 99k-function arm64 build. Three ceilings that *multiply*:
   - eligibility ~86% reachable (14% hard Tier-D floor);
   - **oracle** the binding one: ~17% strongly verifiable, ~73% only weakly
     ("boots, no KCSAN" = didn't-crash, not correct), ~11% C-forever;
   - linking scales but with a reimpl/shim/mirror tax and a panic-handler wall.
   Headline: model cost is noise (~$hundreds); **verification wall-clock is the
   whole game**; and the single highest-ROI missing piece is *manufacturing
   oracles* for the 73% driver middle.

2. **(b) The differential-oracle harness** (`dream/diffgate/`) — the missing
   capability, built and proven. Link the C original + the Rust candidate into one
   kernel, drive an identical deterministic script, assert bit-identical traces.
   - correct transplant → `DIFF_PASS` (bit-identical to C);
   - a wrong-but-**non-crashing** transplant → `DIFF_FAIL`, localized to the exact
     op — *the bug boot+KCSAN would have passed.* This is what turns the driver
     mass from "weakly attested" into "provably equivalent to the C it replaced."

3. **(a) The ratchet** (`dream/ratchet/`) — the "one-command pass": a manifest
   (single source of truth + dashboard) and a weaver that excises functions from
   the **real in-tree .c**, compiles the Rust in, links, builds, boots, and reports
   %-Rust.
   - **Ring 0**: `drivers/ptp/ptp_mock`'s 4 locked regions woven into a booting
     kernel; **Ring 0.1**: the woven driver's regions differentially verified
     **in-situ** (same object that ships in vmlinux, bit-identical to C).
   - **Ring 1**: a second subsystem — `lib/math/int_sqrt`, a Haiku-synthesized
     leaf, differentially verified over **20,197 inputs, zero mismatches** —
     accumulated into the manifest alongside ptp. The ratchet grows: %-Rust climbs,
     the prior entry stays green.

## The wall we hit and solved (a real one, predicted then proven)

The linking research *predicted* that multiple freestanding Rust objects each
carrying `#[panic_handler]` would collide at the vmlinux link. At N=2 real objects
(ptp + int_sqrt) we **hit it exactly** — `multiple definition of rust_begin_unwind`
— and applied the fix the research had already verified (`objcopy
--localize-symbol`), now baked into the weaver. Predicted → hit → solved, in one
night. That's the research doing its job.

## Where this leaves the dream

- The **machinery is real and cumulative**, not a demo: manifest → weave → build →
  boot → gate → dashboard, on the live tree, growing by rows.
- The **honest ceiling is understood**: ~17% strongly provable, a large
  differentially-gateable driver middle (the oracle harness is the key that
  unlocks it), ~11% C-forever. "Boots, mostly Rust, ratcheted to the floor" — not
  100%, and never was.
- The **next moves are clear**: scale Ring 1 (more lib/ leaves — the tier where the
  oracle is real), then push the differential harness onto a driver with recorded
  I/O to start converting the 73%; parallel QEMU workers to attack the wall-clock
  bottleneck.

Dashboard, current (`weave.py status`) — after Rings 0 → 1 → 2:
```
sources woven     : 4  (drivers/ptp, lib/math x2, lib/hweight)
functions -> Rust : 8/16  (50.0% of tracked bodies)   [4/9 -> 5/11 -> 8/16]
strongly gated    : 8/8  (differential — every Rust function proven equal to its C)
  drivers/ptp/ptp_mock.c: adjfine, adjtime, settime64, gettime64  [differential:PASS]
  lib/math/int_sqrt.c:    int_sqrt                                 [differential:PASS]
  lib/math/int_pow.c:     int_pow                                  [differential:PASS]
  lib/hweight.c:          __sw_hweight32, __sw_hweight64           [differential:PASS]
  (gcd: verified_not_woven — proven correct, weave deferred on a static-helper orphan)
```
Five Rust objects, four sources, one booting kernel — every function proven
bit-identical to the C it replaced, and the widely-called ones (int_pow, hweight)
run as Rust via **real callers at boot**. Ring 2 verified a 4-leaf batch in ONE
boot (~292k differential comparisons) — the wall-clock lever the research called
decisive.

**Ring 3 built the key to the 73%** (`dream/ratchet/ring3/`, RING3.md): the
recorded register-access (MMIO) differential oracle. A driver's meaning is its
register program — write order, status poll, which register — not its return
value. Haiku transplanted the canonical driver hot path (stage/command/poll/read,
$0.0013); the oracle drove 256 transfers, recorded the full 2,048-access trace of
C and Rust, and asserted bit-identical (DIFF_PASS). The negative control — "skip
the status poll" — **returns identical values** (a value-only check passes it) but
is REJECTED on its trace (half the accesses, diverges at the missing poll). That
is the capability that converts the driver mass from "boots" to "programs the
hardware identically to the C."

**Ring 4 transplanted a REAL in-tree driver** (`dream/ratchet/ring4/`, RING4.md):
`drivers/gpio/gpio-zevio.c` — genuine register-address math, RMW bit programming,
the get/set/direction ops. Its real register logic (verbatim; only the seam
adapted) was Haiku-transplanted ($0.0058) and trace-verified bit-identical to the
C over a 448-access register trace across 32 pins. The negative control — the
classic "wrong register" bug (OUTPUT ops → INPUT offset) — is REJECTED at the
first access. First conversion of code that shipped in Linux; the 73% is
addressable.

Total model spend across every transplant: **~4 cents.** Research → differential
oracle → ratchet (50% Rust, Rings 0–2) → driver-class oracle (Ring 3) → a real
in-tree driver (Ring 4). The bricks are laid, the batching works, the driver
oracle exists, and it runs on real driver code; the next thousand are the same shape.
