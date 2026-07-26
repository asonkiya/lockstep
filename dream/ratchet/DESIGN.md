# The ratchet — infrastructure for the full-kernel rewrite

The dream (design.md §4.1) needs cumulative state, not one-shot gates. This is
the machine that turns "N verified transplants" into "a booting kernel that is
X% Rust, and never regresses." Grounded in the excision surgery we've now done
by hand three times (M4 depth, M4 breadth, M5's `rewire_c`).

## The manifest — single source of truth

`manifest.json`: one entry per function in the target config.

```json
{
  "config": "arm64-defconfig",
  "functions": {
    "drivers/ptp/ptp_mock.c::mock_phc_adjfine": {
      "file": "drivers/ptp/ptp_mock.c",
      "symbol": "mock_phc_adjfine",
      "status": "rust",             // c | rust  (the ratchet state)
      "tier": "unsafe",             // unsafe (repr(C)/raw-ptr) | safe (idiomatic)
      "gate": "differential",       // boot-digest | kcsan | kunit | differential
      "verdict": "PASS",            // last gate result; ratchet: PASS never -> C
      "seam": "lockstep_phc_adjfine",
      "rust_impl": "drivers/ptp/rust/mock_phc_adjfine.rs",
      "evidence": "dream/diffgate/out/correct-console.txt"
    }
  }
}
```

The manifest IS the dashboard: `%-Rust = |status:rust| / |functions|`, sliced by
tier (unsafe vs idiomatic) and by gate strength (differential/kunit = proven;
boot-digest = weakly attested — never conflated, per the research §7).

## The weaver — apply the manifest, produce a kernel

`weave.py`:
1. **Excise** each `status:rust` function from its `.c`. Proven mechanism (M5
   `rewire_c`): replace the C body with a call to the Rust `seam`, leaving the
   C function as a thin shell that keeps the kernel-facing ABI, glue, and
   `container_of` on the C side (the partial-migration shape that made the whole
   thing verifiable). Add the `extern` decl for the seam after the includes.
2. **Compile** all `rust_impl` files for a subsystem into ONE Rust object
   (one crate → one `#[panic_handler]`, per the linking research — no
   independent-object collision). Link against the shared runtime crate
   (idioms + shims + struct mirrors).
3. **Wire** kbuild (`obj-y += <subsys>_rust.o`) and build Image.
4. **Gate** cumulatively: every prior `status:rust` entry's gate must stay
   green plus the new batch's. Tiered: boot-digest for most, KCSAN for tier-C,
   differential where an oracle was manufactured. Bisect-on-red to localize.
5. **Ratchet**: on all-green, commit the manifest delta; a PASS entry never
   reverts to C. On red, the batch is rejected, manifest unchanged.

## Ring 0 — the first cut

`drivers/ptp/ptp_mock`: the 4 regions are already verified Rust (M4 breadth) and
now differentially-oracled (this milestone). Ring 0 = weave them via the manifest
into a booting kernel where the manifest reports ptp_mock's regions as
`status:rust, gate:differential, verdict:PASS`, and the whole thing still boots
green. That is the first brick: not a hand-run gate, but the ratchet itself
carrying a real driver's regions into a booting Rust-carrying vmlinux, with the
metric reading off the manifest.

Then Ring 1 (lib/ leaves, where the oracle is real) is just more manifest rows.
