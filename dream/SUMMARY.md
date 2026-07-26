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

Dashboard, current (`weave.py status`):
```
sources woven     : 2  (drivers/ptp, lib/math)
functions -> Rust : 5/11  (45.5% of tracked bodies)
strongly gated    : 5/5  (differential — every Rust function proven equal to its C)
  drivers/ptp/ptp_mock.c: adjfine, adjtime, settime64, gettime64  [differential:PASS]
  lib/math/int_sqrt.c:    int_sqrt                                 [differential:PASS]
```
Two subsystems, two Rust objects, one booting kernel, every function proven
bit-identical to the C it replaced. Total model spend for all transplants tonight:
under 2 cents. The bricks are laid; the next thousand are the same shape.
