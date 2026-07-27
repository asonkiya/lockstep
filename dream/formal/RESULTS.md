# Formal tier — bounded model checking over the full domain

The prior art's sharpest edge was rigor: VERT and RustAssure back their passes with
bounded model checking / symbolic execution, stronger than a dynamic differential.
`dream/exhaustive` closed the gap for ≤2^16 domains by exhaustion; this closes it
the way the literature does — a **model checker** — reaching 2^32 and **2^64**.

## Three rungs of the same guarantee, now all real

| method | domain reached | hweight8 | hweight16 | hweight32 | hweight64 |
|--------|----------------|:--------:|:---------:|:---------:|:---------:|
| in-kernel exhaustion (`dream/exhaustive`) | ≤2^16 | ✅ 256 | ✅ 65,536 | — | — |
| native exhaustion (`exhaust_u32.rs`, black_box) | 2^32 | | | ✅ 4.29e9 (44s) | — |
| **Kani/CBMC bit-vector** (`cargo kani`) | **2^32 & 2^64** | | | **✅** | **✅** |

```
$ cargo kani
VERIFICATION:- SUCCESSFUL   (Verification Time: 0.09s)
Complete - 2 successfully verified harnesses, 0 failures, 2 total.
  hweight32_equiv_popcount  — for ALL 2^32 u32, cgir_sw_hweight32(x) == x.count_ones()
  hweight64_equiv_popcount  — for ALL 2^64 u64, cgir_sw_hweight64(x) == x.count_ones()
```

`count_ones()` is the trusted popcount oracle, and the kernel's `__sw_hweight32/64`
are by definition popcount, so `cgir == count_ones` **proves the transplant
equivalent to the kernel C over the entire domain** — no sampling, no untested
input. CBMC proves it in a fraction of a second because these are **loop-free SWAR
bit manipulation**, exactly its bit-vector sweet spot; the native 2^32 run (44s of
genuine iteration) independently corroborates the 32-bit case.

## Honest scope

- **Loop-free bit/arithmetic functions are fully provable** by CBMC over any domain
  (hweight, mask/shift helpers, `pci_rebar_bytes_to_size`-style).
- **Looping functions** (`int_pow`, `int_sqrt`) need a Kani **unwind bound**; CBMC
  then proves them complete *up to that bound* — sound for inputs that stay within
  it (e.g. int_pow's loop is ≤32 iterations, so bounded-complete), but not a
  universal proof for unbounded loops. That is the remaining formal frontier, same
  as VERT's (bounded MC is incomplete beyond the unwind bound).
- Environment note: Kani needed a `rustup` + nightly + CBMC install (done here);
  the proofs run via `cargo kani` in `dream/formal/`.

## Net

The transplants' correctness for the popcount family is no longer "matches C on the
inputs we tried" — it is **proven equal over 2^64**, matching the rigor tier the
literature uses to be ahead of dynamic differential. Combined with `concgate`
(the concurrency oracle nobody else has) and the dynamic differential (the cheap
bulk gate), the acceptance stack now spans dynamic → exhaustive → formal, each
applied where it's sound.
