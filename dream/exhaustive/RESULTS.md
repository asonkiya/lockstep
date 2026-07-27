# Exhaustive bounded verification — closing the sampling-soundness gap

The prior art (`dream/PRIOR-ART.md`) flagged the one place the field is ahead of a
pure dynamic differential: RustAssure's symbolic execution **caught 11 bugs a
fuzzer missed**, because a differential over *sampled* inputs can miss a divergence
at an untested input. Without a model checker (no Kani in this environment), the
sound, model-checker-free way to close that gap is to test the **entire input
domain** where it is small enough — which turns the test into a proof.

## Result (`gate.sh`, one boot)

```
EXHAUSTIVE: __sw_hweight8  domain=256    bad=0  verdict=PROVEN
EXHAUSTIVE: __sw_hweight16 domain=65536  bad=0  verdict=PROVEN
EXHAUSTIVE GATE: PASS (2 fns proven over full domain)
```

`cgir___sw_hweight8` and `cgir___sw_hweight16` are now verified equal to the kernel
C at **every point of their input domain** (2^8 and 2^16) — not sampled, exhausted.
For these functions there is no untested input, so the RustAssure failure mode
cannot occur: this is a complete equivalence proof over the domain, the sound
analogue of VERT's bounded-verification tier, done with nothing but a boot.

## Scope, honestly

- **Applies to small-domain functions only.** A single u8/u16 argument (or any
  function whose reachable input space is ≤ ~2^16–2^20) is exhaustible in a boot.
  A u32 argument (2^32) is ~minutes-to-hours of QEMU and a u64 is infeasible —
  those stay **bounded-sampled** (structured + adversarial inputs), which is where
  the sampling gap remains and a real model checker (Kani/CBMC) would be the next
  investment.
- So this doesn't make the *whole* pipeline formally sound; it makes the
  **small-domain slice provably sound** and marks exactly where the dynamic
  differential's guarantee weakens (wide domains).

## Net

Two functions moved from "matches C on the inputs we tried" to "matches C on every
input, proven." It's a small set, but it's the honest, tool-available way to
answer the prior art's sharpest criticism — and it names precisely where the
guarantee stops (wide-domain functions need a model checker, not more boots).
