# Non-leaf weave: the readers class

Sweep-1 cycle-1 concluded the mirror-field wideners had hit diminishing returns
and the lever is **integration of the banked verified translations**. This is
the first non-leaf weave path — turning boot-free-verified oracle candidates
into kernel objects woven in place of their C originals.

## Why readers weave (and efftrace/alloc don't, yet)

The oracle candidates are not all alike:

- **readers** (structdiff): the candidate is `fn <fn>_rs(p: *mut Mirror, ...)`
  where the `#[repr(C)]` Mirror was gate-verified to have the SAME layout as
  the real kernel struct. So `*mut Mirror` IS `struct X *` at the ABI — the
  candidate is *already a real-struct function*. It drops straight into the
  kernel as a seam replacement.
- **efftrace / alloc**: the candidate operates on a flat i64 *cell model*
  (`rs_call(a0: i64, ...)`), a behavioral model, not a real-struct function.
  Weaving those needs a model→real translation step — a separate build.

So readers is the soundly-weavable non-leaf class today. `weave_readers.py`
handles it.

## The mechanism (`weave_readers.py`)

From a verified reader candidate it produces the three artifacts the ratchet
weaver (`weave.py`) consumes:

1. **Rust object** — the candidate verbatim (mirror + const-asserts + `_rs` fn)
   plus a `#![no_std]` freestanding preamble + panic handler. Compiled
   `--target aarch64-unknown-none-softfloat --emit=obj` and linked into vmlinux
   (the Ring 1–7 freestanding-object path; panic handlers localized on collision).
2. **C seam** — the in-tree function's body replaced by `{ <fn>_rs(<args>); }`
   with an `extern` decl carrying the real C parameter types.
3. **In-tree layout re-cert** — a `_Static_assert(sizeof/offsetof)` per field,
   emitted into the C file, checked against REAL kernel headers at kernel build.

## Soundness chain (three independent legs)

- **Behavior**: the host differential already verified the candidate against
  the real C over swept field values (the sweep gate).
- **Layout**: the in-tree `_Static_assert` re-certifies the mirror layout ==
  the real kernel struct AT KERNEL BUILD — the one previously-deferred check,
  now closed in-tree. A wrong offset fails the build (load-bearing: pinned by
  `test_weave_readers.py::test_layout_guard_is_load_bearing`).
- **Runs**: it links + boots (the ratchet's proven boot-digest gate).

A wrong layout fails the kernel build; a wrong behavior was caught on the host.
Zero new false-pass surface.

## Status: BOOT CAPSTONE PASSED

`weave_readers.py batch` — **7 verified readers woven into 6 real kernel source
files across 4 subsystems, freestanding aarch64 Rust objects linked into
vmlinux, kernel BUILT and BOOTED clean** (smp_up + boot_complete + no early
panic; the expected no-rootfs VFS panic ends a full boot). All 7 seam symbols
are `T` (defined) in `nm vmlinux`; each layout re-certified in-kernel by the
`_Static_assert` against real headers at build.

  kernel/resource.c:      resource_clip
  lib/bitmap-str.c:       bitmap_check_region
  lib/linear_ranges.c:    linear_range_get_value
  kernel/dma/swiotlb.c:   wrap_area_index
  kernel/bpf/log.c:       bpf_vlog_update_len_max
  kernel/locking/lockdep.c: lock_time_inc, lock_time_add

resource_clip / wrap_area_index / lock_time_* are on the boot path, so they
RAN during the boot — not just linked.

Four in-tree integration details the host proof structurally could not surface,
each found and fixed against the real build:
  1. crate name can't contain '.' (from the `.c` in the key) — sanitized.
  2. freestanding `--emit=obj` needs `#![no_main]`.
  3. `_Static_assert` needs the COMPLETE struct — moved to block scope inside
     the woven body (the struct is complete at the function's own site).
  4. a locally-defined struct (`struct region` in bitmap-str.c) made the
     extern's `struct X *` a prototype-scoped incomplete tag — fixed with a
     file-scope forward declaration before the extern.
Plus a weave.py fix: insert the extern block after the LEADING include region,
not the globally-last include (kernel files carry mid-file macro-table includes;
lockdep.c broke on that).

- `weave_readers.py prove <file> <fn>` — host-compile both artifacts (no boot).
- `weave_readers.py batch` — weave the batch + docker build + boot gate.

## What's next

- Scale the batch (~104 verified readers weave-ready — the readers slice of the
  1,123 banked); batch-boot economics are the Ring-7 worker/HVF story.
- The efftrace/alloc model→real translation is the following non-leaf class.
