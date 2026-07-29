# Driver-family trace oracle — SCOPE (the cost-generalization test)

## The question this answers

The cost analysis' one open wildcard: does the trace-oracle harness **generalize
across a driver family cheaply**, or is each driver an independent problem? Ring 4
trace-verified ONE real driver (`gpio-zevio`). "Full arm64 rewrite" hinges on the
73% driver mass being a few hundred *families* (write-once-per-family) rather than
~15,000 independent functions. This scopes + measures that on the GPIO family.

## The measured starting picture (census)

- **98** control-flow-refused register functions in `drivers/gpio/`, across **23**
  drivers. Uniform core = the ~43 `get`/`set`/`direction`/`get_direction` ops; the
  rest (irq/config/init) are more heterogeneous (out of v1 scope).
- Family candidates (>=3 ops): tegra186 (15), rockchip (9), tangier (9),
  bcm-kona (8), mxc (8), graniterapids (7), nomadik (7), mxs (5), ftgpio010 (4).

## The key finding — the device model factors into a few IDIOMS, not per-driver

Inspecting three drivers, the register-programming idiom differs:

| driver | idiom | how a bit is set |
|---|---|---|
| gpio-zevio (Ring 4) | **RMW-DATA** | read OUTPUT, OR/AND the bit, write back |
| gpio-ftgpio010 | **SET/CLR registers** | write `BIT(pin)` to a separate `DATA_SET` / `DATA_CLR` |
| gpio-mxs | **SET/CLR alias** | write `BIT(pin)` to `base+0x4` (set-alias) / `+0x8` (clr-alias) of a banked DOUT |

So the "write-once device model" is false per-driver but true **per idiom**: a small
closed set (~3-4) of register-programming idioms covers most of the family. This
refines the cost shape: **harness cost is O(idioms), not O(drivers)** — enumerate
the idioms once, map each driver to (idiom + register offsets).

### Stronger: the kernel ALREADY factored the idioms (gpio-mmio.c)

`drivers/gpio/gpio-mmio.c` (the "bgpio" generic MMIO-GPIO library, ~22 KB) is a
SHARED implementation that ~41 gpio drivers delegate their get/set/direction to. It
installs the register accessor by flag — and those variants ARE exactly the idioms:

| gpio-mmio.c accessor | idiom |
|---|---|
| `gpio_mmio_set_with_clear` / `_multiple_with_clear` | SET/CLR registers |
| `gpio_mmio_set_set` / `_multiple_set` | SET-only |
| `gpio_mmio_set` | RMW-DATA |
| `gpio_mmio_set_none` | no output |

So ftgpio010 has NO get/set of its own — it's the library's. **Transplanting
gpio-mmio.c ONCE trace-verifies the shared core for every bgpio-based driver.**
The cost shape sharpens again: a large slice of the 73% is not "per driver" or even
"per idiom" but **per shared subsystem library** (gpio-mmio, regmap, spi-bitbang,
…) — dozens of files, each covering many drivers. Only the drivers with CUSTOM
register logic (zevio, mxs, tegra186, …) are per-driver, and those still map to the
same bounded idiom set. This is the empirical core of why the full-rewrite bill is
low: the kernel's own de-duplication did much of the factoring for us.

## The write-once / per-driver / per-function split (from Ring 4 anatomy)

| layer | scope | content |
|---|---|---|
| op-driver + trace comparator | **write-once (whole family)** | drive get/set/dir across pins×values; record ordered (kind,off,val) trace; compare |
| device-model idiom | **write-once per IDIOM (~3-4 total)** | the software register block: RMW-DATA / SET-CLR-regs / SET-CLR-alias |
| register config | **per driver** (mechanical) | offsets, section/bank math, INPUT seed; ~10 lines |
| C reference | **per driver** (mechanical) | driver's register logic verbatim, seam-adapted (readl/writel -> mmio_r/mmio_w, take `regs` directly) |
| Rust transplant | **per function** | synth (~$0.006), gate-arbitrated |

## v1 build plan

0. **Highest-leverage target: transplant the shared `gpio-mmio.c` core** (the ~5
   set-accessor variants + read/direction) against the generic op-driver — one
   transplant that trace-verifies the bgpio core every dependent driver reuses.
1. **Generic op-driver + idiom library** (`dream/family/gpio_family.py`), host-first
   (boot-free, the recorder substrate) so idiom coverage is measured in seconds, not
   boots. Generic op script (dir_out -> set 0/1 -> get -> dir_in -> get across N pins)
   + the 3 idiom device models + ordered-trace comparator.
2. **Per-driver configs** for zevio (RMW-DATA, re-verify), ftgpio010 (SET/CLR-regs),
   mxs (SET/CLR-alias): offsets + idiom + seam-adapted C ref + a correct Rust
   transplant + a wrong-register negative control.
3. **Soundness (carried from Ring 4 + structdiff):** compare the FULL register trace
   (kind, offset, value, order) — not the return; wrong-register negative control
   must DIFF_FAIL; sweep pins×values with a path-coverage check that every op arm is
   exercised. Zero false passes.
4. **In-kernel boot gate:** unchanged from Ring 4 — once host idioms pass, the same
   `_shipped`-object boot gate verifies in vmlinux (the RECORD phase then hits the
   real accessor instead of the model). One boot per driver-cluster, batched.

## The measurement this produces (the cost answer)

- **# idioms** needed to cover the ~43-op GPIO core (hypothesis: 3-4).
- **Per-driver incremental cost** once the idiom exists: lines of config + transplant $.
- Extrapolated: **family cost = (idioms x harness-authoring) + (drivers x ~mechanical config) + (functions x ~$0.006)** — which, if idioms are few, makes the 73% mass O(hundreds of idioms across all subsystems), the number the full-rewrite bill actually turns on.

## Honest edges

- Idiom count could be higher across all 23 drivers (banked/paged, read-to-clear,
  shadow-register drivers exist) — v1 measures the count, doesn't assume it.
- The ~49 non-core gpio ops (irq/config) are deferred — heterogeneous.
- Reactive/timing-dependent reads and non-MMIO effects (DMA) stay out of scope
  (the recorder's stated edge).
