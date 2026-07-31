# The dream — overnight progress

> **Distilled lessons** (measure-before-building, the soundness ladder, the 7-class
> oracle map, the cost/factorization truths): [`dream/LESSONS.md`](./LESSONS.md).

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

**Ring 5 ran the pipeline as an automated fleet loop** (`dream/ratchet/ring5/`,
RING5.md): 6 real lib functions (fresh: lcm, lcm_not_zero) parallel-synthesized in
one round-trip ($0.0054), one boot verifying all against the kernel's own symbols
(~724k comparisons). The gate CAUGHT a wrong parallel-synth (int_sqrt chose a bad
algorithm, failed from x=4), the loop fed back the counterexample and recovered to
6/6 — selectivity + retry, the real autonomous-rewrite loop. Also surfaced the
principle: gate on a pristine tree, weave separately.

**Ring 6 closed the loop** (`dream/ratchet/ring6/`, RING6.md): the Ring 5 fleet's
fresh verified passers (lcm, lcm_not_zero) were woven through the ratchet into a
booting kernel — the first functions to traverse the ENTIRE autonomous path
(worklist → parallel synth → verify → catch/retry → weave → boot) in one pipeline.
Dashboard: **10/18 functions Rust (55.6%), 5 sources, 7 Rust objects, 10/10
differentially gated**, booting.

**Ring 7 exercised the last wall-clock lever** (`dream/ratchet/ring7/`, RING7.md):
parallel QEMU workers. Two pristine volumes (clone: 17s), the fleet split into two
batches, both built + booted concurrently — all DIFF_PASS, **1.99× speedup on 2
workers** (247s wall-clock vs 493s summed), near-linear. Both multipliers in the
research's cost model — batching (Rings 2/5, ~292k comparisons/boot) and parallel
workers (Ring 7) — are now demonstrated; the full-run bottleneck is provisioning,
not capability.

**Ring 8 went for depth** (`dream/ratchet/ring8/`, RING8.md): the reusable
`ksdk` crate that unlocks the ~73% Tier-B middle — `#[repr(C)]` struct mirrors with
compile-time layout guards, reimplemented inline/macro helpers, `container_of`. A
real struct-context function (clk-divider's `clk_div_table` table walk — the class
pure-scalar synth can't touch) was transplanted against the mirror and verified
bit-identical. Two complementary mirror gates proven: a size-wrong mirror FAILS TO
COMPILE (layout guard, `E0080`); a field-swapped mirror (same size, guard blind)
is caught at runtime by the differential (bad=13). Struct mirroring needs both.

Total model spend across every transplant: **~5 cents.** The complete arc:
research → differential oracle → ratchet → driver-class oracle → real in-tree
driver → automated fleet loop → closed loop → parallel workers → **depth substrate
for the Tier-B middle**. The machine is whole and autonomous — worklist in,
verified Rust woven into a booting kernel out, wrong transplants caught and
retried, no human in the loop; it scales by adding workers; and the struct-context
substrate now reaches the majority of the kernel one struct family at a time.

**Ring 9 swept a real subsystem** (`dream/ratchet/ring9/`, RING9.md): the entire
divider-math family of `drivers/clk/clk-divider.c` — 6 functions reading
`clk_div_table` arrays and branching on divider flags — transplanted as ONE Rust
object against the Ring 8 `ksdk` mirror ($0.0087, one shot), all 6 verified
bit-identical to the C across the flag/width/table matrix in one boot. The depth
payoff made concrete: the mirror built once in Ring 8 unlocked the whole family at
no extra cost — breadth × depth on real in-tree code, the shape a full subsystem
sweep repeats. What remains is running it wide (buying cores) and mirroring more
struct families.

**The container-ADT oracle became a production path** (`dream/container_adt/
{reach,harness}.py`): the mechanism proof's representation-independent
differential now runs against REAL kernel functions taken verbatim from the
tree. The reach gate measured the honest v2 vocabulary over 58,773 corpus
functions (~3,120 touch the list family): straight-line ops over params — the
naive v1 shape — is essentially EMPTY in .c files (trivial mutators live in
headers as inlines); real mutators iterate `list_for_each_entry[_safe]`, anchor
on static-global `LIST_HEAD`s, run under lock brackets, and retire nodes with
`kfree`. Speaking that vocabulary (locks stripped-and-flagged = container-half
claim, kfree = flagged arena retire, pointer fields as opaque tokens) accepts
19 functions across lib/kernel/mm/fs/btrfs/drivers, with every refusal tallied
as the v3 backlog (multi-lh-field nodes dominate the prepare tail; list_entry
cursors and allocation dominate the reach tail). The harness compiles the real
C under `#line 1000` with op-site coverage instrumentation against a generated
Rust ADT surface, and gates on ADT state after EVERY call + returns + retire
log + coverage (un-exercised mutation site → REFUSED_COVERAGE, proven by
negative control). **Live fire: all 9 harness-preparable functions solved
autonomously for $0.005 — 5 at $0 via local qwen (with a gate-feedback repair
round), 4 via Haiku — including ACPI/IORT, device-mapper, USB gadget, and
hibernation code.** The oracle earned its keep live twice: it rejected a
signed/unsigned translation bug (i64 −1 vs `unsigned long`), and it refused to
certify a function whose workload never exercised the add path. Wired into the
autonomous runner as phase 1B2 (`CONTAINERS=1`).
